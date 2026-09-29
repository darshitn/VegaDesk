"""P2 durable-workspace layer for VEGA.

These handlers follow the EXACT same contract as tools.py (mutating handlers
return a receipt dict and are committed/idempotency-wrapped by
tools.execute_intent; reads set ``read_only``). No second action framework, no
shell, no repo mutation: "Resume" is read-only and shows a bounded, read-only
git snapshot for a registered folder. Registration is reversible/inspectable.
"""

import os
import re
import subprocess

try:
    from db import Workspace, SessionNote, Task, FocusSession, Coursework, iso_utc
    import timeutil
    from tools import ToolError, NeedsClarification
except ImportError:  # package-relative fallback
    from .db import Workspace, SessionNote, Task, FocusSession, Coursework, iso_utc
    from . import timeutil
    from .tools import ToolError, NeedsClarification

WORKSPACE_TYPES = ("personal", "academic")
WORKSPACE_STATUSES = ("active", "paused", "done")
COURSEWORK_KINDS = ("assignment", "exam", "lab", "reading", "project")


def _days_until(now_utc, due_utc):
    """Whole + fractional days from `now_utc` to `due_utc` (negative = overdue)."""
    return (due_utc - now_utc).total_seconds() / 86400.0


def _human_due(now_utc, due_utc):
    d = _days_until(now_utc, due_utc)
    if d < 0:
        return f"overdue by {abs(round(d))} day(s)"
    if d < 1:
        return "due today"
    if d < 2:
        return "due tomorrow"
    return f"due in {round(d)} days"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:120] or "workspace"


def _unique_key(db, name):
    base = slugify(name)
    key, n = base, 2
    while db.query(Workspace).filter(Workspace.key == key).first():
        key = f"{base}-{n}"
        n += 1
    return key


def _validate_path(path):
    """Store only an existing local directory; never enumerate drives."""
    if path is None or not str(path).strip():
        return None
    candidate = os.path.abspath(os.path.expanduser(str(path).strip()))
    if not os.path.isdir(candidate):
        raise ToolError(f"That folder doesn't exist or isn't a directory: '{path}'. "
                        f"Register the project with a real folder path.")
    if len(candidate) > 600:
        raise ToolError("That path is too long to register.")
    return candidate


def _resolve_workspace(db, params):
    ws_id = params.get("workspace_id")
    if ws_id is not None:
        ws = db.query(Workspace).filter(Workspace.id == int(ws_id)).first()
        if not ws:
            raise ToolError(f"Workspace #{ws_id} was not found.")
        return ws
    name = (params.get("name") or "").strip()
    if not name:
        raise ToolError("Which project? Give me its name or number.")
    low = name.lower()
    allws = db.query(Workspace).all()
    exact = [w for w in allws if w.name.lower() == low or w.key == slugify(low)]
    if len(exact) == 1:
        return exact[0]
    partial = [w for w in allws if low in w.name.lower() or low in w.key]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ToolError(f"No registered project matches '{name}'. "
                        f"Register it first, e.g. 'register a project called {name}'.")
    raise NeedsClarification(
        "Several projects match: " + ", ".join(f"#{w.id} '{w.name}'" for w in partial[:5]) +
        ". Which one did you mean?")


def read_only_git_status(path):
    """Bounded, READ-ONLY git summary for a registered repo. Runs
    `git status --branch --porcelain` only; never add/commit/push/reset/build.
    Returns a short string, or None if not a repo / git unavailable / timed out.
    Command output is treated strictly as data, never as instructions."""
    if not path or not os.path.isdir(path):
        return None
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", path, "status", "--branch", "--porcelain=v1"],
            capture_output=True, text=True, timeout=5, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").splitlines()
    branch = lines[0].lstrip("#").strip() if lines and lines[0].startswith("##") else "detached"
    dirty = [ln for ln in lines[1:] if ln.strip()]
    return f"{branch}; {len(dirty)} uncommitted change(s)" if dirty else f"{branch}; clean"


# ── handlers (mirrors tools.py contract) ───────────────────────

def register_workspace(db, clock, params, source):
    name = (params.get("name") or "").strip()
    if not name:
        raise ToolError("What is the project called?")
    if len(name) > 200:
        raise ToolError("Project name is too long (max 200 characters).")
    wtype = (params.get("type") or "personal").lower()
    if wtype not in WORKSPACE_TYPES:
        raise ToolError(f"Project type must be one of {WORKSPACE_TYPES}.")
    existing = db.query(Workspace).filter(Workspace.name == name).first()
    if existing:
        raise NeedsClarification(f"A project named '{name}' already exists (#{existing.id}). "
                                 f"Give it a different name, or update it with a new next action.")
    path = _validate_path(params.get("path"))
    now = clock.now_utc()
    ws = Workspace(key=_unique_key(db, name), name=name[:200], type=wtype, path=path,
                   goal=(params.get("goal") or "")[:500], status="active",
                   next_action=(params.get("next_action") or "")[:500],
                   blocker=(params.get("blocker") or "")[:500],
                   created_utc=now, updated_utc=now)
    db.add(ws)
    db.flush()
    msg = f"Registered {wtype} project #{ws.id} '{ws.name}'"
    if path:
        msg += f" at {path}"
    if ws.next_action:
        msg += f". Next action: {ws.next_action}."
    return {"success": True, "message": msg + "." if not msg.endswith(".") else msg,
            "entity_type": "workspace", "entity_id": ws.id}


def update_workspace(db, clock, params, source):
    ws = _resolve_workspace(db, params)
    changed = []
    for field in ("goal", "next_action", "blocker"):
        if params.get(field) is not None:
            setattr(ws, field, str(params[field])[:500])
            changed.append(field.replace("_", " "))
    if params.get("status") is not None:
        st = str(params["status"]).lower()
        if st not in WORKSPACE_STATUSES:
            raise ToolError(f"Status must be one of {WORKSPACE_STATUSES}.")
        ws.status = st
        changed.append("status")
    if params.get("path") is not None:
        ws.path = _validate_path(params["path"])
        changed.append("path")
    if not changed:
        raise NeedsClarification(f"What should I update on '{ws.name}'? "
                                 f"You can set its next action, goal, blocker, or status.")
    ws.updated_utc = clock.now_utc()
    return {"success": True, "entity_type": "workspace", "entity_id": ws.id,
            "message": f"Updated '{ws.name}' ({', '.join(changed)}). "
                       f"Next action: {ws.next_action or '—'}."}


def list_workspaces(db, clock, params, source):
    q = db.query(Workspace)
    status = (params.get("status") or "").lower()
    if status:
        q = q.filter(Workspace.status == status)
    rows = q.order_by(Workspace.updated_utc.desc()).all()
    if not rows:
        return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
                "message": "No projects registered yet. Try 'register a project called <name>'."}
    lines = [f"#{w.id} [{w.status}] {w.name}"
             + (f" — next: {w.next_action}" if w.next_action else "")
             for w in rows[:15]]
    return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
            "message": f"{len(rows)} project(s):\n" + "\n".join(lines)}


def _default_active_workspace(db):
    """'Resume my work' with no explicit name → most recently active project."""
    ws = (db.query(Workspace).filter(Workspace.status == "active")
          .order_by(Workspace.updated_utc.desc()).first())
    if ws:
        return ws
    ws = db.query(Workspace).order_by(Workspace.updated_utc.desc()).first()
    if ws is None:
        raise NeedsClarification(
            "You haven't registered any projects yet. Try: "
            "'register a project called <name> with next action <what>'.")
    return ws


def resume_workspace(db, clock, params, source):
    if params.get("workspace_id") is None and not (params.get("name") or "").strip():
        ws = _default_active_workspace(db)
    else:
        ws = _resolve_workspace(db, params)
    ws.updated_utc = clock.now_utc()  # touch last activity; a read that records attention only
    lines = [f"Resuming '{ws.name}' ({ws.type})."]
    if ws.goal:
        lines.append(f"  Goal: {ws.goal}")
    if ws.next_action:
        lines.append(f"  Next action: {ws.next_action}")
    else:
        lines.append(f"  Next action: (none set — say: set next action for {ws.name}: ...)")
    if ws.blocker:
        lines.append(f"  Blocker: {ws.blocker}")
    note = (db.query(SessionNote).filter(SessionNote.workspace_id == ws.id)
            .order_by(SessionNote.created_utc.desc()).first())
    if note:
        lines.append(f"  Last session note ({timeutil.render_local(note.created_utc)}): "
                     f"{note.outcome or 'no outcome recorded'}"
                     + (f" | next: {note.next_action}" if note.next_action else ""))
    open_tasks = (db.query(Task).filter(Task.workspace_id == ws.id, Task.completed.is_(False))
                  .order_by(Task.deadline_utc.is_(None), Task.deadline_utc).all())
    if open_tasks:
        lines.append("  Open tasks: " + ", ".join(
            f"#{t.id} {t.text}" + (f" (due {timeutil.render_local(t.deadline_utc)})"
                                   if t.deadline_utc else "") for t in open_tasks[:8]))
    git = read_only_git_status(ws.path)
    if ws.path:
        lines.append(f"  Folder: {ws.path}")
        lines.append(f"  Git (read-only): {git or 'not a git repo / git unavailable'}")
    return {"success": True, "read_only": True, "entity_type": "workspace",
            "entity_id": ws.id, "message": "\n".join(lines)}


def link_task_to_workspace(db, clock, params, source):
    ws = _resolve_workspace(db, params)
    task_id = params.get("task_id")
    if task_id is None:
        raise ToolError("Which task? Give me its number.")
    task = db.get(Task, int(task_id))
    if task is None:
        raise ToolError(f"Task #{task_id} was not found.")
    task.workspace_id = ws.id
    ws.updated_utc = clock.now_utc()
    return {"success": True, "entity_type": "task", "entity_id": task.id,
            "message": f"Linked task #{task.id} '{task.text}' to '{ws.name}'."}


def add_session_note(db, clock, params, source):
    outcome = (params.get("outcome") or "").strip()[:2000]
    blocker = (params.get("blocker") or "").strip()[:1000]
    next_action = (params.get("next_action") or "").strip()[:1000]
    if not (outcome or blocker or next_action):
        raise NeedsClarification("What should the session note say? "
                                 "e.g. outcome, blocker, and next action.")
    ws = None
    if params.get("workspace_id") is not None or params.get("name"):
        ws = _resolve_workspace(db, params)
    now = clock.now_utc()
    note = SessionNote(workspace_id=ws.id if ws else None, outcome=outcome,
                       blocker=blocker, next_action=next_action, source=source,
                       created_utc=now)
    db.add(note)
    db.flush()
    if ws:
        if next_action:
            ws.next_action = next_action[:500]
        ws.blocker = blocker[:500] if blocker else ws.blocker
        ws.updated_utc = now
    scope = f" for '{ws.name}'" if ws else ""
    return {"success": True, "entity_type": "session_note", "entity_id": note.id,
            "message": f"Session note #{note.id} saved{scope}. "
                       f"Next action: {next_action or outcome[:60] or '—'}."}


def build_session_draft(db, clock, params, source):
    """READ-ONLY draft of today's session from REAL receipts/tasks — never
    fabricates completed work. The user edits/confirms, then add_session_note
    (idempotent) saves it. Produces no entity itself."""
    now = clock.now_utc()
    start_local = timeutil.local_now(clock).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = timeutil.to_utc(start_local)
    done = (db.query(Task).filter(Task.completed.is_(True), Task.updated_at >= start_utc).all())
    created = (db.query(Task).filter(Task.created_at >= start_utc).all())
    focus = (db.query(FocusSession).filter(FocusSession.actual_end_utc >= start_utc).all())
    outcome_bits = []
    if done:
        outcome_bits.append("Completed: " + ", ".join(t.text for t in done[:10]))
    if focus:
        outcome_bits.append(f"Focus: {len(focus)} session(s) "
                            + ", ".join((f.objective or "untitled") for f in focus[:6]))
    if created and not done:
        outcome_bits.append("Logged today: " + ", ".join(t.text for t in created[:10]))
    open_next = (db.query(Task).filter(Task.completed.is_(False))
                 .order_by(Task.deadline_utc.is_(None), Task.deadline_utc).first())
    draft = {
        "outcome": " | ".join(outcome_bits) or "No completed work was recorded today.",
        "next_action": (open_next.text if open_next else ""),
        "blocker": "",
        "observed": {"done": [t.text for t in done], "created": [t.text for t in created],
                     "focus": [f.objective for f in focus]},
    }
    lines = ["Here's a draft of today's session (edit anything before I save):",
             f"  Outcome: {draft['outcome']}"]
    if draft["next_action"]:
        lines.append(f"  Suggested next action: {draft['next_action']}")
    lines.append("Say 'save session note' with your edits, or 'add a session note: ...'.")
    return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
            "message": "\n".join(lines), "draft": draft}


def get_today(db, clock, params, source):
    """Compact Today view (read-only): active projects' next actions, near
    deadlines, and active focus. Feeds the dashboard and 'what should I do now'."""
    now = clock.now_utc()
    ws_rows = (db.query(Workspace).filter(Workspace.status == "active")
               .order_by(Workspace.updated_utc.desc()).limit(6).all())
    from db import ScheduledAlert  # noqa
    soon = (db.query(Task).filter(Task.completed.is_(False), Task.deadline_utc.isnot(None),
            Task.deadline_utc >= now, Task.deadline_utc <= now + timeutil.timedelta(days=2))
            .order_by(Task.deadline_utc).all())
    active_focus = db.query(FocusSession).filter(FocusSession.status == "active").first()
    # Stage 3: open coursework due within the next 7 days (soonest first).
    cw = (db.query(Coursework).filter(Coursework.completed.is_(False),
          Coursework.due_utc.isnot(None),
          Coursework.due_utc <= now + timeutil.timedelta(days=7))
          .order_by(Coursework.due_utc).all())
    coursework_items = [{"id": c.id, "title": c.title, "kind": c.kind,
                         "due_utc": iso_utc(c.due_utc), "effort_minutes": c.effort_minutes,
                         "workspace_id": c.workspace_id, "due_in_days": round(_days_until(now, c.due_utc), 1),
                         "why": _human_due(now, c.due_utc)} for c in cw[:8]]
    items = {"next_actions": [{"workspace_id": w.id, "name": w.name,
                               "next_action": w.next_action} for w in ws_rows if w.next_action],
             "due_soon": [{"task_id": t.id, "text": t.text,
                           "deadline_utc": iso_utc(t.deadline_utc)} for t in soon[:8]],
             "coursework": coursework_items,
             "active_focus": ({"id": active_focus.id, "objective": active_focus.objective}
                              if active_focus else None)}
    lines = ["Today:"]
    if items["next_actions"]:
        lines += [f"  • {n['name']}: {n['next_action']}" for n in items["next_actions"]]
    for t in soon[:8]:
        lines.append(f"  ⏰ {t.text} — due {timeutil.render_local(t.deadline_utc)}")
    for c in coursework_items[:5]:
        effort = f", ~{c['effort_minutes']} min" if c["effort_minutes"] else ""
        lines.append(f"  🎓 {c['kind']} '{c['title']}' — {c['why']}{effort}")
    if coursework_items:
        top = coursework_items[0]
        lines.append(f"  → Tackle {top['kind']} '{top['title']}' first: it's {top['why']}.")
    if items["active_focus"]:
        lines.append(f"  ◉ In focus: {items['active_focus']['objective'] or 'session'}")
    if len(lines) == 1:
        lines.append("  Nothing scheduled. Register a project with 'register a project called <name>'.")
    return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
            "message": "\n".join(lines), "items": items}


# ══════════════════════════════════════════════════════════════════════
# STAGE 3 — smallest academic loop (subject=workspace, assignment/exam,
# due date, user-estimated effort, daily briefing, "I have N minutes").
# Reuses the SAME executor/registry; no second framework.
# ══════════════════════════════════════════════════════════════════════

def _parse_effort_minutes(raw):
    """Accept an int, or a duration phrase ('90 minutes', 'an hour',
    '1.5 hours', 'forty five minutes') → whole minutes. None if absent."""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    secs = timeutil.parse_duration_seconds(s)
    if secs is None:
        raise ToolError(f"I couldn't read that time estimate: '{s}'.")
    return max(1, int(round(secs / 60)))


def _parse_due(db, clock, raw):
    """Resolve a due phrase to naive UTC, validating timezone + past/ambiguous
    dates. Returns None when no phrase is supplied (a date is optional)."""
    if raw is None or str(raw).strip() == "":
        return None
    # 'deadline' context: a date without a clock time means end of that day
    # (23:59 local) — "due Friday" is a day-level deadline, not an appointment.
    res = timeutil.extract_when(str(raw), clock, context="deadline")
    if res.needs_clarification:
        raise NeedsClarification(res.question)
    if not (res.found and res.utc_dt is not None):
        raise ToolError(f"That due date isn't a real date: '{raw}'.")
    now = clock.now_utc()
    # Accept same-day deadlines (allow a small grace so 'due today' at 23:59
    # still works) but reject clearly past dates.
    if res.utc_dt < now - timeutil.timedelta(hours=1):
        raise ToolError(f"That due date is already in the past ({timeutil.render_local(res.utc_dt)}). "
                        f"Give me an upcoming date.")
    return res.utc_dt


def _link_subject(db, params):
    """Optionally attach coursework to a registered subject (academic workspace).
    Uses an explicit workspace_id (UI path) or resolves a subject name; never
    auto-creates a workspace (that would be an unintended write)."""
    if params.get("workspace_id") is not None:
        return _resolve_workspace(db, {"workspace_id": params["workspace_id"]})
    subject = params.get("subject") or params.get("name")
    if not subject or not str(subject).strip():
        return None
    return _resolve_workspace(db, {"name": str(subject).strip()})


def add_coursework(db, clock, params, source):
    title = (params.get("title") or "").strip()[:300]
    if not title:
        raise NeedsClarification("What's the assignment or exam called?")
    kind = (params.get("kind") or "assignment").strip().lower()
    if kind not in COURSEWORK_KINDS:
        kind = "assignment"
    ws = _link_subject(db, params)
    due = _parse_due(db, clock, params.get("due") or params.get("due_utc"))
    effort = _parse_effort_minutes(params.get("effort_minutes") or params.get("effort")
                                   or params.get("effort_text"))
    now = clock.now_utc()
    c = Coursework(workspace_id=ws.id if ws else None, kind=kind, title=title,
                   due_utc=due, effort_minutes=effort, completed=False,
                   source=source, created_utc=now, updated_utc=now)
    db.add(c)
    db.flush()
    if ws:
        ws.updated_utc = now
    subject_txt = f" for '{ws.name}'" if ws else ""
    bits = [f"Logged {kind} '{title}'{subject_txt}."]
    if due:
        bits.append(f"{_human_due(now, due)} ({timeutil.render_local(due)}).")
    if effort:
        bits.append(f"~{effort} min estimated.")
    return {"success": True, "entity_type": "coursework", "entity_id": c.id,
            "message": " ".join(bits)}


def list_coursework(db, clock, params, source):
    q = db.query(Coursework).filter(Coursework.completed.is_(False))
    if params.get("workspace_id") is not None:
        q = q.filter(Coursework.workspace_id == int(params["workspace_id"]))
    rows = q.order_by(Coursework.due_utc.is_(None), Coursework.due_utc).all()
    now = clock.now_utc()
    items = [{"id": c.id, "title": c.title, "kind": c.kind, "due_utc": iso_utc(c.due_utc),
              "effort_minutes": c.effort_minutes, "workspace_id": c.workspace_id,
              "why": (_human_due(now, c.due_utc) if c.due_utc else "no due date")}
             for c in rows]
    if not items:
        return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
                "message": "No open coursework logged. Try: 'add assignment <title> due friday about 90 minutes'.",
                "items": []}
    lines = [f"Open coursework ({len(items)}):"]
    for it in items:
        effort = f", ~{it['effort_minutes']} min" if it["effort_minutes"] else ""
        lines.append(f"  #{it['id']} {it['kind']} '{it['title']}' — {it['why']}{effort}")
    return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
            "message": "\n".join(lines), "items": items}


def suggest_study(db, clock, params, source):
    """'I have 25 minutes' → the best-fit open coursework, with an editable
    choice (alternatives). Ranks by due-soonest; prefers a task that FITS the
    window (fully completable) over a bigger one; falls back to a first slice."""
    minutes = params.get("minutes")
    if minutes is None:
        minutes = 25
    minutes = max(5, int(minutes))
    now = clock.now_utc()
    rows = (db.query(Coursework).filter(Coursework.completed.is_(False))
            .order_by(Coursework.due_utc.is_(None), Coursework.due_utc).all())
    if not rows:
        return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
                "message": "Nothing open to suggest. Add one: 'add assignment <title> due friday about 60 minutes'.",
                "items": {"window_minutes": minutes, "suggestion": None, "alternatives": []}}

    def fits(c):
        return c.effort_minutes is not None and c.effort_minutes <= minutes

    fitting = [c for c in rows if fits(c)]
    pool = fitting or rows          # prefer completable-now work, else due-soonest
    chosen = pool[0]
    alts = [c for c in rows if c.id != chosen.id][:5]

    s = _items_with_fit([chosen], now, minutes)[0]
    lines = [f"With {minutes} minutes, do {chosen.kind} '{chosen.title}' first — "
             f"{s['why_due']}, {s['fit']}."]
    if alts:
        lines.append("Other open items (say the number to switch):")
        for a in _items_with_fit(alts, now, minutes):
            lines.append(f"  #{a['id']} {a['kind']} '{a['title']}' — {a['why_due']}, {a['fit']}")
    lines.append("Want me to start a focus session on it?")
    return {"success": True, "read_only": True, "entity_type": "coursework",
            "entity_id": chosen.id, "message": "\n".join(lines),
            "items": {"window_minutes": minutes, "suggestion": s,
                      "alternatives": _items_with_fit(alts, now, minutes)}}


def _items_with_fit(rows, now, minutes):
    """Shape coursework rows for a study suggestion (uses the caller's clock)."""
    out = []
    for c in rows:
        e = c.effort_minutes
        why = _human_due(now, c.due_utc) if c.due_utc else "no due date"
        if e is None:
            fit = "time estimate not set"
        elif e <= minutes:
            fit = f"~{e} min — fits your {minutes} min window"
        else:
            fit = f"~{e} min total — bigger than {minutes} min, do a first slice"
        out.append({"id": c.id, "title": c.title, "kind": c.kind, "due_utc": iso_utc(c.due_utc),
                    "effort_minutes": e, "workspace_id": c.workspace_id, "why_due": why,
                    "fit": fit})
    return out


def complete_coursework(db, clock, params, source):
    cid = params.get("coursework_id")
    if cid is None:
        raise ToolError("Which coursework item? Give me its number.")
    c = db.get(Coursework, int(cid))
    if c is None:
        raise ToolError(f"Coursework #{cid} was not found.")
    if c.completed:
        return {"success": True, "entity_type": "coursework", "entity_id": c.id,
                "message": f"{c.kind} '{c.title}' is already marked done."}
    c.completed = True
    c.updated_utc = clock.now_utc()
    return {"success": True, "entity_type": "coursework", "entity_id": c.id,
            "message": f"Done — marked {c.kind} '{c.title}' complete."}
