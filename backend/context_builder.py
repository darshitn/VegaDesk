"""Context assembly + token budgeting for VEGA P1.

Budget policy: the model context window is configuration, not a message
count. We reserve output space first, then fit (in order) the versioned
system prompt, the registry-derived tool schemas, the current user
request, and finally as much recent conversation as fits. The current
request is never truncated or paraphrased — if it alone does not fit, the
user is asked for a shorter request.

No tokenizer dependency is installed, so sizes use a conservative
documented estimate: ceil(chars / 4) tokens (≈ typical English BPE), and
every budget figure is labeled estimated wherever it is surfaced.
"""

import json
import math

try:
    import timeutil
    import tool_registry
except ImportError:
    from . import timeutil
    from . import tool_registry

SYSTEM_PROMPT_VERSION = "vega-p1.1"

# Hard cap on one chat request so an oversized message is a clarification,
# not a blown context. (The HTTP contract already caps at 4000 chars.)
MAX_REQUEST_CHARS = 3200

HISTORY_SHARE = 0.5  # recent conversation may use at most half the free budget


def estimate_tokens(text):
    """Conservative estimate — no tokenizer installed; labeled 'estimated'
    wherever reported."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def build_system_prompt(clock, user_name="Sir"):
    """Versioned VEGA identity + grounding rules + current local time context
    (so the model never has to guess 'today')."""
    now_local = timeutil.local_now(clock)
    return (
        f"[{SYSTEM_PROMPT_VERSION}] You are V.E.G.A., a concise, dry-witted personal assistant "
        f"running on the user's own machine. Address the user as {user_name}.\n"
        f"Current local time: {now_local.strftime('%A, %d %B %Y, %I:%M %p')} "
        f"({timeutil.tz_context_name()}).\n"
        "Rules:\n"
        "1. If the user asks for one supported action (task, timer, reminder, focus session, "
        "task list, AI-news digest, opening an app or website), propose EXACTLY ONE tool call. "
        "One action per request — if they ask for several, pick nothing and ask which one to do first.\n"
        "2. For dates and times, pass the user's own phrase in 'when' or 'duration_text' "
        "(e.g. 'friday at 6 pm', '25 minutes'). VEGA resolves it in the user's timezone and "
        "rejects past or ambiguous times — never invent a time the user did not give.\n"
        "3. For ANY question about the user's tasks or to-do list, call list_tasks and report ONLY "
        "what it returns. Never invent, guess, or recall tasks from memory.\n"
        "4. Never claim an action succeeded on your own — VEGA reports the executor's receipt. "
        "If a tool does not fit the request, answer conversationally and, when useful, mention the "
        "exact offline command form (e.g. \"say 'set a timer for 25 minutes'\").\n"
        "5. Never print tool definitions, raw JSON, or function-call syntax in your text.\n"
        "6. Keep answers short. Do not ask for personal data beyond what the action needs."
    )


def build_messages(clock, history, message, user_name, caps, schema_kind):
    """Returns (messages, meta).

    messages: [{role, content}] for the provider — system first, then the
    newest-fitting slice of history, then the current user request last.
    meta: {'prompt_tokens_est', 'history_budget', 'history_included',
    'history_dropped', 'estimated': True}

    Raises ValueError('request_too_large') when the current request itself
    cannot fit beside the system prompt, tools, and reserved output.
    """
    if len(message) > MAX_REQUEST_CHARS:
        raise ValueError("request_too_large")

    tools_schema = tool_registry.schemas_for_provider(schema_kind)
    tools_json = json.dumps(tools_schema, separators=(",", ":"))

    system_prompt = build_system_prompt(clock, user_name)
    fixed = (estimate_tokens(system_prompt) + estimate_tokens(tools_json)
             + estimate_tokens(message) + 8)  # +8: role/structure overhead
    budget = caps.context_window - caps.reserved_output
    if fixed > budget:
        raise ValueError("request_too_large")

    messages = [{"role": "system", "content": system_prompt}]

    history_budget = int((budget - fixed) * HISTORY_SHARE)
    included = []
    used = 0
    for m in reversed(history or []):
        role = "assistant" if getattr(m, "role", None) == "assistant" else "user"
        content = (getattr(m, "content", "") or "")[:4000]
        cost = estimate_tokens(content) + 4
        if used + cost > history_budget:
            break
        included.append({"role": role, "content": content})
        used += cost
    included.reverse()
    messages.extend(included)
    messages.append({"role": "user", "content": message})

    meta = {
        "prompt_tokens_est": fixed + used,
        "history_budget": history_budget,
        "history_included": len(included),
        "history_dropped": max(0, len(history or []) - len(included)),
        "estimated": True,  # estimate, not a tokenizer count
    }
    return messages, meta
