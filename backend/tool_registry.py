"""One tool registry for VEGA P1.

Single source of truth for every action a model may propose: stable id +
version, JSON schema, read/write classification, bounds, and the handler
binding. Provider schemas are GENERATED from this registry (nothing is
duplicated per provider), and every proposal is strictly validated here
before it reaches the existing executor (tools.execute_intent) or the
existing bounded system actions. Business logic is never copied into
provider adapters.

Rejection rules (no partial writes, ever):
- unknown tool name, non-dict arguments, extra properties -> rejected
- wrong types, out-of-enum, over-length, out-of-range -> rejected
- missing required targets (which task? which timer?) -> rejected with
  the executor's own wording where possible
- multiple proposals in one turn -> rejected by the caller (one action
  per request in P1)
- date/duration phrases are resolved with the authoritative local clock
  and timezone context (timeutil); the executor remains the final
  authority on past/ambiguous times.
"""

import hashlib
import re

try:
    import timeutil
    import tools
    import system_actions
    import command_parser
    import policy
    from db import ActionReceipt, utcnow_naive
except ImportError:
    from . import timeutil
    from . import tools
    from . import system_actions
    from . import command_parser
    from . import policy
    from .db import ActionReceipt, utcnow_naive

REGISTRY_VERSION = "1.0"


class ProposalRejected(Exception):
    """A model proposal failed validation. Nothing was executed."""


# ─────────────────────────────────────────────
# Registry entries
# ─────────────────────────────────────────────

def _entry(id, version, description, schema, kind, classification,
           produces_receipt=True, expose_to_model=True, when_context=None,
           risk_level=None):
    if risk_level is None:
        risk_level = 0 if classification == "read" else 1
    return {
        "id": id, "version": version, "description": description,
        "schema": schema, "kind": kind,  # "intent" | "system"
        "classification": classification,  # "read" | "write" | "launch"
        "produces_receipt": produces_receipt,
        "expose_to_model": expose_to_model,
        "when_context": when_context,
        "risk_level": risk_level,
    }


_STR500 = {"type": "string", "minLength": 1, "maxLength": 500}
_STR300 = {"type": "string", "maxLength": 300}

REGISTRY = {
    e["id"]: e for e in [
        _entry("create_task", "1.0",
               "Create a task in the user's local task list, optionally with a deadline. "
               "For the deadline, prefer 'when' as the user's own natural phrase (e.g. 'friday at 6 pm'); "
               "VEGA resolves it in the user's timezone. 'deadline_utc' (ISO 8601) is accepted as an alternative.",
               {"type": "object",
                "properties": {
                    "text": dict(_STR500, description="The task text, without the date phrase"),
                    "when": {"type": "string", "maxLength": 120,
                             "description": "Natural-language deadline phrase in the user's words"},
                    "deadline_utc": {"type": "string", "maxLength": 40,
                                     "description": "ISO 8601 datetime, alternative to 'when'"},
                    "subject": {"type": "string", "maxLength": 120},
                },
                "required": ["text"], "additionalProperties": False},
               "intent", "write", when_context="deadline"),
        _entry("list_tasks", "1.0",
               "Read-only: VIEW/list the user's incomplete tasks from the local database. "
               "Use only for questions that ask to see what is pending (e.g. 'what's on my list?'). "
               "Never invent tasks. Do NOT use this when the user says a task is done, finished, "
               "or 'wrapped up' — that is a completion, use set_task_completed instead.",
               {"type": "object",
                "properties": {
                    "window": {"type": "string", "enum": ["all", "today", "tomorrow", "week"],
                               "description": "Deadline filter; default 'all'"},
                },
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("set_task_completed", "1.0",
               "Mark a task completed (or reopen it). Use when the user says a task is done / "
               "finished / complete / 'wrapped up X' / 'mark X done'. Prefer task_id when it is "
               "known; otherwise pass task_text as the user's own words for the task (VEGA resolves "
               "it or asks which one). Ambiguous or unknown targets are rejected with a clarification, "
               "never guessed. Do NOT answer completion with list_tasks.",
               {"type": "object",
                "properties": {
                    "task_id": {"type": "integer", "minimum": 1},
                    "task_text": dict(_STR500, description="The task's text, as the user described it"),
                    "completed": {"type": "boolean", "description": "Default true"},
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("start_timer", "1.0",
               "Start a countdown timer. Give 'duration_text' in the user's words (e.g. '25 minutes') "
               "or 'duration_seconds' directly. Maximum 24 hours.",
               {"type": "object",
                "properties": {
                    "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
                    "duration_text": {"type": "string", "maxLength": 120},
                    "label": _STR300,
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("cancel_timer", "1.0",
               "Cancel an active timer by id. Without an id, only works when exactly one timer is active; "
               "otherwise the executor asks which one.",
               {"type": "object",
                "properties": {"timer_id": {"type": "integer", "minimum": 1}},
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("create_reminder", "1.0",
               "Create a reminder at a specific future time. 'when' should be the user's own phrase "
               "(e.g. 'tomorrow at 7 pm'); a specific time of day is required. 'due_utc' (ISO 8601) "
               "is accepted as an alternative.",
               {"type": "object",
                "properties": {
                    "text": dict(_STR500, description="What to remind about, without the time phrase"),
                    "when": {"type": "string", "maxLength": 120},
                    "due_utc": {"type": "string", "maxLength": 40},
                },
                "required": ["text"], "additionalProperties": False},
               "intent", "write", when_context="reminder"),
        _entry("snooze_reminder", "1.0",
               "Snooze a reminder. Without reminder_id, snoozes the most recently fired (or next pending) "
               "reminder. Default snooze is 10 minutes.",
               {"type": "object",
                "properties": {
                    "reminder_id": {"type": "integer", "minimum": 1},
                    "snooze_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
                    "snooze_text": {"type": "string", "maxLength": 120,
                                    "description": "Snooze duration in the user's words, e.g. '10 minutes'"},
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("start_focus_session", "1.0",
               "Start a focus session (one at a time). Give 'duration_text' in the user's words or "
               "'duration_seconds'. Maximum 8 hours.",
               {"type": "object",
                "properties": {
                    "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 28800},
                    "duration_text": {"type": "string", "maxLength": 120},
                    "objective": _STR300,
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("end_focus_session", "1.0",
               "End the active focus session, optionally saving an outcome note.",
               {"type": "object",
                "properties": {"outcome_note": {"type": "string", "maxLength": 1000}},
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("get_ai_radar_digest", "1.0",
               "Read-only: summarize stored AI Radar research (offline). mode 'free' answers "
               "free-models/API-credits questions.",
               {"type": "object",
                "properties": {"mode": {"type": "string", "enum": ["general", "free"]}},
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("refresh_ai_radar", "1.0",
               "Run a live AI Radar research pass (network). Deterministic-command only; not offered "
               "to models in P1 to keep prompts bounded.",
               {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
               "intent", "write", expose_to_model=False),

        # ── P2 durable-workspace intents (RESUME MY WORK vertical slice) ──────
        # The deterministic command parser + the Today/Projects UI are the
        # primary paths (these work with Ollama and Gemini offline). Reads are
        # exposed to the model too; every write still flows through the SAME
        # executor (tools.execute_intent) + ActionReceipt idempotency path.
        _entry("register_workspace", "1.0",
               "Register a durable project/workspace so it can be resumed later. 'name' is required; "
               "type is 'personal' or 'academic'; optionally a goal, a next action, and a local folder "
               "'path' (VEGA validates that the folder exists; it never scans drives). Reversible and "
               "inspectable — this only records metadata about the project.",
               {"type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "type": {"type": "string", "enum": ["personal", "academic"]},
                    "goal": {"type": "string", "maxLength": 500},
                    "next_action": {"type": "string", "maxLength": 500},
                    "path": {"type": "string", "maxLength": 600},
                },
                "required": ["name"], "additionalProperties": False},
               "intent", "write"),
        _entry("update_workspace", "1.0",
               "Update a registered project's goal, next action, blocker, status, or folder. Give "
               "'workspace_id' or 'name' to identify it; pass only the fields to change.",
               {"type": "object",
                "properties": {
                    "workspace_id": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 200},
                    "goal": {"type": "string", "maxLength": 500},
                    "next_action": {"type": "string", "maxLength": 500},
                    "blocker": {"type": "string", "maxLength": 500},
                    "status": {"type": "string", "enum": ["active", "paused", "done"]},
                    "path": {"type": "string", "maxLength": 600},
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("list_workspaces", "1.0",
               "Read-only: list the user's registered projects and their current next actions.",
               {"type": "object",
                "properties": {"status": {"type": "string", "enum": ["active", "paused", "done"]}},
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("resume_workspace", "1.0",
               "Read-only: 'resume my work' for one project — show its goal, current next action, "
               "blocker, last session note, open linked tasks, and a bounded READ-ONLY git status for "
               "its folder. Identify by 'workspace_id' or 'name'. Ambiguous names trigger a "
               "clarification, never a guess. This does not open anything or run any command.",
               {"type": "object",
                "properties": {
                    "workspace_id": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 200},
                },
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("add_session_note", "1.0",
               "Save a session note (what you got done, a blocker, the next action) for a project. "
               "Only store facts the user actually stated — never invent completed work. Optional "
               "'workspace_id'/'name' to scope it to a project.",
               {"type": "object",
                "properties": {
                    "outcome": {"type": "string", "maxLength": 2000},
                    "blocker": {"type": "string", "maxLength": 1000},
                    "next_action": {"type": "string", "maxLength": 1000},
                    "workspace_id": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 200},
                },
                "required": [], "additionalProperties": False},
               "intent", "write"),
        _entry("link_task_to_workspace", "1.0",
               "Associate an EXISTING task with a project (by task number). Only links when the user "
               "explicitly asks; preserves the task's other fields.",
               {"type": "object",
                "properties": {
                    "task_id": {"type": "integer", "minimum": 1},
                    "workspace_id": {"type": "integer", "minimum": 1},
                    "name": {"type": "string", "maxLength": 200},
                },
                "required": ["task_id"], "additionalProperties": False},
               "intent", "write", expose_to_model=False),
        _entry("build_session_draft", "1.0",
               "Read-only draft of today's session summary from REAL receipts/tasks/focus records "
               "(never fabricates). The user edits and confirms before add_session_note saves it.",
               {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("get_today", "1.0",
               "Read-only: compact 'Today' briefing — active projects' next actions, tasks due soon, "
               "open coursework due within a week (with why-it's-urgent), and any active focus session.",
               {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),

        # ── Stage 3: smallest academic loop (subject = academic workspace) ──
        _entry("add_coursework", "1.0",
               "Log a piece of coursework for a subject: an assignment/exam/lab/reading/project with an "
               "optional due date and an optional time estimate. 'title' is required. 'subject' optionally "
               "names an ALREADY-registered academic project to link it to (VEGA never invents a project). "
               "For 'due' prefer the user's own phrase (e.g. 'friday', 'next week'); VEGA validates it in "
               "their timezone and rejects past/ambiguous dates with a clarification. 'effort_text' is the "
               "user's own estimate (e.g. '90 minutes', 'an hour') — never a model guess.",
               {"type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 300},
                    "kind": {"type": "string", "enum": ["assignment", "exam", "lab", "reading", "project"]},
                    "subject": {"type": "string", "maxLength": 200},
                    "due": {"type": "string", "maxLength": 120},
                    "effort_text": {"type": "string", "maxLength": 120},
                    "effort_minutes": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                "required": ["title"], "additionalProperties": False},
               "intent", "write", when_context="deadline"),
        _entry("list_coursework", "1.0",
               "Read-only: list the user's OPEN coursework (assignments/exams) with due dates and time "
               "estimates, soonest first. Optionally scoped to one project via 'workspace_id'.",
               {"type": "object",
                "properties": {"workspace_id": {"type": "integer", "minimum": 1}},
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("suggest_study", "1.0",
               "Read-only: 'I have N minutes — what should I work on?' Suggests the best-fit open "
               "coursework using due date + the user's recorded time estimate, and lists alternatives so "
               "the choice is editable. Default window is 25 minutes. This only reads; it does not start "
               "anything.",
               {"type": "object",
                "properties": {"minutes": {"type": "integer", "minimum": 5, "maximum": 1440}},
                "required": [], "additionalProperties": False},
               "intent", "read", produces_receipt=False),
        _entry("complete_coursework", "1.0",
               "Mark a coursework item complete by number (or reopen it). Only acts on an explicit item "
               "the user points at; unknown ids are rejected, never guessed.",
               {"type": "object",
                "properties": {
                    "coursework_id": {"type": "integer", "minimum": 1},
                    "completed": {"type": "boolean"},
                },
                "required": ["coursework_id"], "additionalProperties": False},
               "intent", "write"),

        _entry("open_app", "1.0",
               "Open a local application by name from the user's discovered catalog (fuzzy-matched, "
               "injection-guarded). Never pass shell commands — only an application name.",
               {"type": "object",
                "properties": {"name": {"type": "string", "minLength": 2, "maxLength": 200}},
                "required": ["name"], "additionalProperties": False},
               "system", "launch", produces_receipt=True, risk_level=1),
        _entry("open_website", "1.0",
               "Open a website by alias (e.g. 'github') or a plain URL in the default browser.",
               {"type": "object",
                "properties": {"site_or_url": {"type": "string", "minLength": 2, "maxLength": 300}},
                "required": ["site_or_url"], "additionalProperties": False},
               "system", "launch", produces_receipt=True, risk_level=1),
        _entry("system.open_url", "1.0",
               "Open a website by alias (e.g. 'github') or a plain URL in the default browser.",
               {"type": "object",
                "properties": {
                    "url": {"type": "string", "minLength": 2, "maxLength": 300},
                    "site_or_url": {"type": "string", "minLength": 2, "maxLength": 300},
                },
                "required": [], "additionalProperties": False},
               "system", "launch", produces_receipt=True, risk_level=1, expose_to_model=False),
    ]
}


def model_tool_ids():
    return [tid for tid, e in REGISTRY.items() if e["expose_to_model"]]


# ─────────────────────────────────────────────
# Schema generation (one registry -> every provider format)
# ─────────────────────────────────────────────

def _strip_for_gemini(schema):
    """Gemini's OpenAPI subset ignores/rejects strict-JSON-schema keywords;
    derived from the same registry object, never hand-maintained."""
    if isinstance(schema, dict):
        return {k: _strip_for_gemini(v) for k, v in schema.items()
                if k not in ("additionalProperties", "minLength", "maxLength",
                             "minimum", "maximum")}
    if isinstance(schema, list):
        return [_strip_for_gemini(v) for v in schema]
    return schema


def schemas_for_provider(kind):
    """kind: 'openai' (Ollama tool format) | 'gemini'. Same registry source."""
    out = []
    for tid in model_tool_ids():
        e = REGISTRY[tid]
        schema = e["schema"] if kind == "openai" else _strip_for_gemini(e["schema"])
        out.append({"type": "function",
                    "function": {"name": tid, "description": e["description"],
                                 "parameters": schema}})
    return out


# ─────────────────────────────────────────────
# Strict schema validation
# ─────────────────────────────────────────────

_JSON_TYPES = {"string": str, "integer": int, "boolean": bool}


def _validate_against_schema(args, schema, path=""):
    """Strict, dependency-free validation. Rejects extra properties, wrong
    types, out-of-enum, over-length, out-of-range. Raises ProposalRejected."""
    if schema.get("type") != "object":
        raise ProposalRejected("Tool schema must be an object.")
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for key in args:
        if key not in props:
            raise ProposalRejected(f"Unknown argument '{path}{key}' — extra properties are not accepted.")
    for key in required:
        if key not in args or args[key] is None or (isinstance(args[key], str) and not args[key].strip()):
            raise ProposalRejected(f"Missing required argument '{path}{key}'.")
    for key, value in args.items():
        if value is None:
            continue
        spec = props[key]
        expected = _JSON_TYPES.get(spec.get("type"))
        if expected is None:
            raise ProposalRejected(f"Argument '{key}' has an unsupported schema type.")
        if expected is int and isinstance(value, bool):
            raise ProposalRejected(f"Argument '{key}' must be an integer.")
        if not isinstance(value, expected):
            raise ProposalRejected(
                f"Argument '{key}' must be of type {spec['type']}, got {type(value).__name__}.")
        if "enum" in spec and value not in spec["enum"]:
            raise ProposalRejected(f"Argument '{key}' must be one of {spec['enum']}.")
        if isinstance(value, str):
            if len(value) > spec.get("maxLength", 4000):
                raise ProposalRejected(f"Argument '{key}' is too long (max {spec.get('maxLength')} characters).")
            if "minLength" in spec and len(value.strip()) < spec["minLength"]:
                raise ProposalRejected(f"Argument '{key}' is empty or too short.")
        if isinstance(value, int) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                raise ProposalRejected(f"Argument '{key}' must be at least {spec['minimum']}.")
            if "maximum" in spec and value > spec["maximum"]:
                raise ProposalRejected(f"Argument '{key}' must be at most {spec['maximum']}.")


# ─────────────────────────────────────────────
# Normalization into executor params
# ─────────────────────────────────────────────

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")


def _resolve_instant(when, iso, clock, context):
    """Resolve a when-phrase or ISO datetime to naive UTC via the authoritative
    local clock/timezone. Returns (utc_dt|None, matched_text). Ambiguous or
    past times raise the same clarification the deterministic parser gives."""
    if isinstance(when, str) and when.strip():
        result = timeutil.extract_when(when.strip()[:120], clock, context=context)
        if result.needs_clarification:
            raise ProposalRejected(result.question)
        if result.ok:
            return result.utc_dt, result.matched_text
        raise ProposalRejected(
            f"I couldn't understand the time '{when}'. Please use a phrase like 'tomorrow at 7 pm' or 'friday at 6 pm'.")
    if isinstance(iso, str) and iso.strip():
        raw = iso.strip().replace(" ", "T")
        if not _ISO_RE.match(raw):
            raise ProposalRejected(f"'{iso}' is not a valid ISO 8601 datetime.")
        try:
            if len(raw) == 10:
                naive_local = timeutil.datetime.fromisoformat(raw + "T23:59:00")
            else:
                naive_local = timeutil.datetime.fromisoformat(raw)
        except ValueError:
            raise ProposalRejected(f"'{iso}' is not a valid ISO 8601 datetime.")
        utc_dt = timeutil.to_utc(naive_local)
        if utc_dt <= clock.now_utc():
            raise ProposalRejected(
                f"{timeutil.render_local(utc_dt)} has already passed. Please give me a future time.")
        return utc_dt, None
    return None, None


def _resolve_duration(seconds, text, label):
    if isinstance(seconds, int):
        return seconds
    if isinstance(text, str) and text.strip():
        try:
            value = timeutil.parse_duration_seconds(text.strip()[:120])
        except ValueError as e:
            raise ProposalRejected(str(e))
        if value is None:
            raise ProposalRejected(f"I couldn't find a duration in '{text}'. Try something like '25 minutes'.")
        return value
    raise ProposalRejected(f"How long should the {label} be? For example: '25 minutes'.")


def _strip_when_phrase(text, matched):
    """Remove the consumed when-phrase from a task/reminder body, mirroring the
    deterministic parser's cleanup so both paths store the same task text. The
    match is case-insensitive because a model may supply 'when' lowercased while
    the text keeps the user's original capitalization."""
    if not matched or not isinstance(text, str):
        return (text or "").strip()
    out = re.sub(re.escape(matched), " ", text, count=1, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip(" ,;.")
    out = re.sub(r"[,;]?\s*\b(?:by|before|due|until|at|on|in|for|to|that)\b\s*$", "", out, flags=re.IGNORECASE)
    out = re.sub(r"^(?:to|that|i\s+should|should|about)\s+", "", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,;.")


def validate_proposal(proposal, clock):
    """Validate + normalize one ToolProposal into
    {'tool', 'entry', 'intent'|'system', 'params'} — ready for the existing
    executor. Raises ProposalRejected with an honest, user-safe message."""
    name = (getattr(proposal, "name", None) or "").strip()
    args = getattr(proposal, "args", None)
    if name not in REGISTRY:
        decision = policy.evaluate_policy(name, args if isinstance(args, dict) else {})
        raise ProposalRejected(
            f"{decision.reason} I can't run '{name or 'that'}' — it isn't a VEGA action. "
            f"Supported actions include tasks, timers, reminders, focus sessions, and opening apps/sites.")
    entry = REGISTRY[name]
    if not entry["expose_to_model"]:
        raise ProposalRejected(f"'{name}' is not available through the model. Try the exact command instead.")
    if not isinstance(args, dict):
        raise ProposalRejected(f"Arguments for '{name}' were malformed, so nothing was executed.")
    _validate_against_schema(args, entry["schema"])

    params = dict(args)

    if name == "create_task":
        utc_dt, matched = _resolve_instant(args.get("when"), args.get("deadline_utc"),
                                           clock, entry["when_context"])
        text = _strip_when_phrase(args["text"], matched)
        if not text:
            raise ProposalRejected("What should the task say? For example: 'add a task: submit the lab record by friday 6 pm'.")
        params = {"text": text[:tools.MAX_TEXT], "deadline_utc": utc_dt,
                  "subject": args.get("subject")}
    elif name == "create_reminder":
        utc_dt, matched = _resolve_instant(args.get("when"), args.get("due_utc"),
                                           clock, entry["when_context"])
        if utc_dt is None:
            raise ProposalRejected("When should I remind you? For example: 'remind me tomorrow at 7 pm to revise trees'.")
        text = _strip_when_phrase(args["text"], matched)
        if not text:
            raise ProposalRejected("What should the reminder say?")
        params = {"text": text[:tools.MAX_TEXT], "due_utc": utc_dt}
    elif name in ("start_timer", "start_focus_session"):
        label = "timer" if name == "start_timer" else "focus session"
        seconds = _resolve_duration(args.get("duration_seconds"), args.get("duration_text"), label)
        params = {"duration_seconds": seconds}
        if name == "start_timer":
            params["label"] = (args.get("label") or "")[:300]
        else:
            params["objective"] = (args.get("objective") or "")[:300]
    elif name == "snooze_reminder":
        params = {"reminder_id": args.get("reminder_id")}
        if args.get("snooze_seconds") is not None or args.get("snooze_text"):
            params["snooze_seconds"] = _resolve_duration(
                args.get("snooze_seconds"), args.get("snooze_text"), "snooze")
        else:
            params["snooze_seconds"] = 600
    elif name == "set_task_completed":
        if args.get("task_id") is None and not (args.get("task_text") or "").strip():
            raise ProposalRejected("Which task? Give me its number (like 'complete task 4') or its exact text.")
        params = {"task_id": args.get("task_id"), "task_text": args.get("task_text"),
                  "completed": bool(args.get("completed", True))}
    elif name == "list_tasks":
        params = {"window": (args.get("window") or "all")}
    elif name == "get_ai_radar_digest":
        params = {"mode": (args.get("mode") or "general")}
    elif name == "cancel_timer":
        params = {"timer_id": args.get("timer_id")}
    elif name == "end_focus_session":
        params = {"outcome_note": (args.get("outcome_note") or "")}
    elif name in ("register_workspace", "update_workspace", "add_session_note",
                  "list_workspaces", "resume_workspace", "get_today",
                  "build_session_draft"):
        # Registry schema already rejected extra/wrong-typed keys. Pass the
        # (non-null) arguments straight to the shared executor; the handlers in
        # workspaces.py are the final authority on targeting and clarification.
        if name == "update_workspace" and args.get("workspace_id") is None \
                and not (args.get("name") or "").strip():
            raise ProposalRejected("Which project? Give me its name or number.")
        if name == "resume_workspace" and args.get("workspace_id") is None \
                and not (args.get("name") or "").strip():
            raise ProposalRejected("Which project should I resume? Name it, or say 'what's on today'.")
        params = {k: v for k, v in args.items() if v is not None}
    elif name in ("add_coursework", "list_coursework", "suggest_study",
                  "complete_coursework"):
        if name == "add_coursework" and not (args.get("title") or "").strip():
            raise ProposalRejected("What's the assignment or exam called?")
        if name == "complete_coursework" and args.get("coursework_id") is None:
            raise ProposalRejected("Which coursework item? Give me its number.")
        params = {k: v for k, v in args.items() if v is not None}
    elif name == "open_app":
        params = {"name": args["name"]}
    elif name in ("open_website", "system.open_url"):
        target = args.get("site_or_url") or args.get("url")
        if not target or not isinstance(target, str) or not target.strip():
            raise ProposalRejected("Which website or URL should I open?")
        params = {"site_or_url": target.strip()}
    else:  # pragma: no cover — registry and branches are kept in sync by tests
        raise ProposalRejected(f"'{name}' is not executable yet.")

    return {"tool": name, "entry": entry, "params": params}


# ─────────────────────────────────────────────
# Execution (existing executor / existing bounded system actions)
# ─────────────────────────────────────────────

def request_fingerprint(message, source):
    """Stable request key shared across provider retries and client replays."""
    raw = f"{(message or '').strip().lower()}|{source or 'chat'}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def execute_proposal(validated, db_factory, clock, source="model",
                     idempotency_key=None, command_text=None):
    """Run one validated proposal through the EXISTING executor / system
    actions. Returns {'response', 'receipt'|None, 'clarification', 'opened'}.
    The final user message always derives from the executor's receipt —
    never from the model's own claim."""
    entry = validated["entry"]
    tool = validated["tool"]
    if entry["kind"] == "system":
        # The launch lane never reaches the executor, so it gets its own copy
        # of the same boundary check — and an explicit 'open …' request is the
        # one case where reaching the destination is the fulfillment.
        blocked = (command_parser.unsupported_destination(command_text, True)
                   if command_text else None)
        if blocked and not command_parser.is_open_request(command_text):
            return {"response": blocked["message"], "receipt": None,
                    "clarification": True, "opened": False}
        if tool in ("open_app", "open_website", "system.open_url"):
            db = db_factory()
            try:
                try:
                    receipt = tools.execute_intent(
                        db, clock, tool, validated["params"], source=source,
                        idempotency_key=idempotency_key, command_text=command_text)
                except tools.ToolError as exc:
                    return {"response": str(exc), "receipt": None,
                            "clarification": True, "opened": False}
            finally:
                if db:
                    db.close()
            msg = receipt.get("message", "")
            return {
                "response": msg,
                "receipt": receipt,
                "clarification": bool(receipt.get("clarification")),
                "opened": bool(receipt.get("success")),
            }

    db = db_factory()
    try:
        try:
            receipt = tools.execute_intent(
                db, clock, tool, validated["params"], source=source,
                idempotency_key=idempotency_key, command_text=command_text)
        except tools.ToolError as exc:
            return {"response": str(exc), "receipt": None,
                    "clarification": True, "opened": False}
    finally:
        db.close()
    return {"response": receipt.get("message", ""), "receipt": receipt,
            "clarification": bool(receipt.get("clarification")), "opened": False}
