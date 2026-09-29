"""Validated tool executor for VEGA M1.

One execution path shared by typed commands, wake-word transcripts, and (in
later milestones) model tool calls. Every mutating action produces an
ActionReceipt; idempotency keys make network retries return the stored receipt
instead of executing twice. No shell execution, no file deletion, no arbitrary
PC control — only the bounded productivity tools below.
"""

from datetime import timedelta, datetime, timezone

from sqlalchemy.exc import IntegrityError

try:
    import idempotency
    from errors import ToolError
    import policy
    from db import (Task, Timer, Reminder, FocusSession, ScheduledAlert,
                    ActionReceipt, iso_utc, utcnow_naive)
    import timeutil
    import ai_radar
    import command_parser
except ImportError:
    from . import idempotency
    from .errors import ToolError
    from . import policy
    from .db import (Task, Timer, Reminder, FocusSession, ScheduledAlert,
                     ActionReceipt, iso_utc, utcnow_naive)
    from . import timeutil
    from . import ai_radar
    from . import command_parser

MAX_TEXT = 500


class NeedsClarification(Exception):
    """Ambiguous request — dispatcher asks the user instead of executing."""


def _fmt_duration(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        if minutes % 60 == 0 and minutes >= 60:
            hours = minutes // 60
            rem = minutes % 60
            return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {rem} minutes" if rem else "")
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _when_line(utc_naive) -> str:
    return f"{timeutil.render_local(utc_naive)} ({iso_utc(utc_naive)})"


def _cancel_alerts(db, entity_type: str, entity_id: int, kinds=None):
    q = db.query(ScheduledAlert).filter(
        ScheduledAlert.entity_type == entity_type,
        ScheduledAlert.entity_id == entity_id,
        ScheduledAlert.status == "pending",
    )
    if kinds:
        q = q.filter(ScheduledAlert.kind.in_(kinds))
    for alert in q.all():
        alert.status = "cancelled"


# ─────────────────────────────────────────────
# Tool handlers: each returns dict(success, message, entity_type, entity_id)
# and leaves the session uncommitted (execute_intent commits once).
# ─────────────────────────────────────────────

def create_task(db, clock, params, source):
    text = (params.get("text") or "").strip()
    if not text:
        raise ToolError("Task text is empty.")
    if len(text) > MAX_TEXT:
        raise ToolError(f"Task text too long (max {MAX_TEXT} characters).")
    deadline = params.get("deadline_utc")
    now = clock.now_utc()
    task = Task(text=text, completed=False, deadline_utc=deadline,
                subject=params.get("subject"), source=source,
                created_at=now, updated_at=now)
    db.add(task)
    db.flush()
    if deadline is not None:
        db.add(ScheduledAlert(
            kind="task_deadline", entity_type="task", entity_id=task.id,
            due_utc=deadline, status="pending",
            message=f"Task due now: '{task.text}'"))
    msg = f"Task #{task.id} created: '{task.text}'."
    if deadline is not None:
        msg += f" Deadline: {_when_line(deadline)}."
    return {"success": True, "message": msg, "entity_type": "task", "entity_id": task.id}


def list_tasks(db, clock, params, source):
    window = (params.get("window") or "all").lower()
    q = db.query(Task).filter(Task.completed.is_(False))
    now = clock.now_utc()
    if window == "week":
        start, end = timeutil.week_bounds_utc(clock)
        q = q.filter(Task.deadline_utc.isnot(None), Task.deadline_utc >= start, Task.deadline_utc <= end)
    elif window == "today":
        local_now = timeutil.local_now(clock)
        day_start = timeutil.naive_utc(local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc))
        day_end = day_start + timedelta(days=1)
        q = q.filter(Task.deadline_utc.isnot(None), Task.deadline_utc >= day_start, Task.deadline_utc < day_end)
    elif window == "tomorrow":
        local_now = timeutil.local_now(clock) + timedelta(days=1)
        day_start = timeutil.naive_utc(local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc))
        day_end = day_start + timedelta(days=1)
        q = q.filter(Task.deadline_utc.isnot(None), Task.deadline_utc >= day_start, Task.deadline_utc < day_end)
    tasks = q.order_by(Task.deadline_utc.is_(None), Task.deadline_utc, Task.id).all()

    labels = {"week": "due this week", "today": "due today", "tomorrow": "due tomorrow"}.get(window, "open")
    if not tasks:
        return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
                "message": f"No {labels} tasks."}
    lines = []
    for t in tasks[:10]:
        if t.deadline_utc:
            lines.append(f"#{t.id} {t.text} — due {timeutil.render_local(t.deadline_utc)}")
        else:
            lines.append(f"#{t.id} {t.text}")
    more = f" (+{len(tasks) - 10} more)" if len(tasks) > 10 else ""
    return {"success": True, "read_only": True, "entity_type": None, "entity_id": None,
            "message": f"{len(tasks)} task(s) {labels}:\n" + "\n".join(lines) + more}


def _resolve_task(db, params):
    task_id = params.get("task_id")
    if task_id is not None:
        task = db.query(Task).filter(Task.id == int(task_id)).first()
        if not task:
            raise ToolError(f"Task #{task_id} was not found.")
        return task
    text = (params.get("task_text") or "").strip().lower()
    if not text:
        raise ToolError("Which task? Give me its number or exact text.")
    candidates = db.query(Task).filter(Task.completed.is_(False)).all()
    exact = [t for t in candidates if t.text.lower() == text]
    if len(exact) == 1:
        return exact[0]
    partial = [t for t in candidates if text in t.text.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ToolError(f"No open task matching '{params.get('task_text')}'.")
    raise NeedsClarification(
        "Several tasks match: " + ", ".join(f"#{t.id} '{t.text}'" for t in partial[:5]) +
        ". Which one should I complete?")


def set_task_completed(db, clock, params, source):
    task = _resolve_task(db, params)
    completed = bool(params.get("completed", True))
    if task.completed == completed:
        return {"success": True, "entity_type": "task", "entity_id": task.id,
                "message": f"Task #{task.id} is already {'completed' if completed else 'open'}: '{task.text}'."}
    task.completed = completed
    task.updated_at = clock.now_utc()
    if completed:
        _cancel_alerts(db, "task", task.id, kinds=["task_deadline"])
    return {"success": True, "entity_type": "task", "entity_id": task.id,
            "message": f"Task #{task.id} {'completed' if completed else 'reopened'}: '{task.text}'."}


def start_timer(db, clock, params, source):
    seconds = int(params.get("duration_seconds") or 0)
    if seconds <= 0:
        raise ToolError("Timer duration must be greater than zero.")
    if seconds > 24 * 3600:
        raise ToolError("Timers are capped at 24 hours.")
    label = (params.get("label") or "").strip()[:300]
    now = clock.now_utc()
    end = now + timedelta(seconds=seconds)
    timer = Timer(label=label, duration_seconds=seconds, start_utc=now, end_utc=end,
                  status="active", source=source, timezone_name=timeutil.tz_context_name(),
                  created_at=now)
    db.add(timer)
    db.flush()
    db.add(ScheduledAlert(
        kind="timer_due", entity_type="timer", entity_id=timer.id, due_utc=end,
        status="pending",
        message=f"Timer finished: {label or _fmt_duration(seconds)}"))
    msg = f"Timer #{timer.id} set for {_fmt_duration(seconds)} — ends {_when_line(end)}."
    return {"success": True, "message": msg, "entity_type": "timer", "entity_id": timer.id}


def cancel_timer(db, clock, params, source):
    timer_id = params.get("timer_id")
    if timer_id is not None:
        timer = db.query(Timer).filter(Timer.id == int(timer_id), Timer.status == "active").first()
        if not timer:
            raise ToolError(f"Timer #{timer_id} is not active.")
        targets = [timer]
    else:
        targets = db.query(Timer).filter(Timer.status == "active").order_by(Timer.end_utc).all()
    if not targets:
        raise ToolError("There is no active timer to cancel.")
    if len(targets) > 1:
        listing = ", ".join(
            f"#{t.id} ({t.label or _fmt_duration(t.duration_seconds)}, ends {timeutil.render_local(t.end_utc)})"
            for t in targets[:5])
        raise NeedsClarification(f"You have {len(targets)} active timers: {listing}. Which one should I cancel?")
    timer = targets[0]
    timer.status = "cancelled"
    timer.ended_at = clock.now_utc()
    _cancel_alerts(db, "timer", timer.id, kinds=["timer_due"])
    return {"success": True, "entity_type": "timer", "entity_id": timer.id,
            "message": f"Timer #{timer.id} cancelled ({timer.label or _fmt_duration(timer.duration_seconds)})."}


def create_reminder(db, clock, params, source):
    text = (params.get("text") or "").strip()
    if not text:
        raise ToolError("Reminder text is empty.")
    if len(text) > MAX_TEXT:
        raise ToolError(f"Reminder text too long (max {MAX_TEXT} characters).")
    due = params.get("due_utc")
    if due is None:
        raise NeedsClarification("When should I remind you? For example: 'Remind me tomorrow at 7 PM to revise trees'.")
    if due <= clock.now_utc():
        raise NeedsClarification("That time has already passed. Please give me a future time for the reminder.")
    reminder = Reminder(text=text[:MAX_TEXT], due_utc=due, status="pending",
                        source=source, timezone_name=timeutil.tz_context_name(),
                        created_at=clock.now_utc())
    db.add(reminder)
    db.flush()
    db.add(ScheduledAlert(
        kind="reminder_due", entity_type="reminder", entity_id=reminder.id,
        due_utc=due, status="pending", message=f"Reminder: {reminder.text}"))
    return {"success": True, "entity_type": "reminder", "entity_id": reminder.id,
            "message": f"Reminder #{reminder.id} set: '{reminder.text}' — {_when_line(due)}."}


def snooze_reminder(db, clock, params, source):
    reminder_id = params.get("reminder_id")
    reminder = None
    if reminder_id is not None:
        reminder = db.query(Reminder).filter(Reminder.id == int(reminder_id)).first()
        if not reminder or reminder.status == "cancelled":
            raise ToolError(f"Reminder #{reminder_id} was not found.")
    else:
        # Most recently fired reminder, else the next pending one, else most recently missed.
        reminder = (db.query(Reminder).filter(Reminder.status == "fired")
                    .order_by(Reminder.fired_at.desc()).first())
        if reminder is None:
            reminder = (db.query(Reminder).filter(Reminder.status == "pending")
                        .order_by(Reminder.due_utc).first())
        if reminder is None:
            reminder = (db.query(Reminder).filter(Reminder.status == "missed")
                        .order_by(Reminder.due_utc.desc()).first())
        if reminder is None:
            raise ToolError("There is no reminder to snooze.")
    seconds = int(params.get("snooze_seconds") or 600)
    if seconds <= 0:
        raise ToolError("Snooze duration must be greater than zero.")
    now = clock.now_utc()
    # Snooze semantics: a reminder that already fired or missed moves to now + snooze;
    # a still-pending one is postponed from its current due time.
    base = now if reminder.status in ("fired", "missed") or reminder.due_utc <= now else reminder.due_utc
    new_due = base + timedelta(seconds=seconds)
    _cancel_alerts(db, "reminder", reminder.id, kinds=["reminder_due"])
    reminder.status = "pending"
    reminder.due_utc = new_due
    reminder.snooze_count = (reminder.snooze_count or 0) + 1
    reminder.fired_at = None
    db.add(ScheduledAlert(
        kind="reminder_due", entity_type="reminder", entity_id=reminder.id,
        due_utc=new_due, status="pending", message=f"Reminder: {reminder.text}"))
    return {"success": True, "entity_type": "reminder", "entity_id": reminder.id,
            "message": f"Reminder #{reminder.id} snoozed by {_fmt_duration(seconds)} — now due {_when_line(new_due)}."}


def start_focus_session(db, clock, params, source):
    existing = db.query(FocusSession).filter(FocusSession.status == "active").first()
    if existing:
        raise ToolError(
            f"Focus session #{existing.id} is already active ({existing.objective or 'no objective'}). "
            f"End it first with 'End my focus session'.")
    seconds = int(params.get("duration_seconds") or 0)
    if seconds <= 0:
        raise ToolError("Focus duration must be greater than zero.")
    if seconds > 8 * 3600:
        raise ToolError("Focus sessions are capped at 8 hours.")
    objective = (params.get("objective") or "").strip()[:300]
    now = clock.now_utc()
    end = now + timedelta(seconds=seconds)
    session = FocusSession(objective=objective, start_utc=now, planned_end_utc=end,
                           status="active", source=source,
                           timezone_name=timeutil.tz_context_name(), created_at=now)
    db.add(session)
    db.flush()
    timer = Timer(label=f"Focus: {objective or 'session'}", duration_seconds=seconds,
                  start_utc=now, end_utc=end, status="active", source="focus",
                  timezone_name=timeutil.tz_context_name(), created_at=now)
    db.add(timer)
    db.flush()
    session.timer_id = timer.id
    # One alert for the session (not the inner timer) — at-most-once for the user.
    db.add(ScheduledAlert(
        kind="focus_end", entity_type="focus_session", entity_id=session.id,
        due_utc=end, status="pending",
        message=f"Focus session finished: {objective or 'session'} ({_fmt_duration(seconds)})"))
    return {"success": True, "entity_type": "focus_session", "entity_id": session.id,
            "message": (f"Focus session #{session.id} started"
                        + (f" for '{objective}'" if objective else "")
                        + f" — {_fmt_duration(seconds)}, planned end {_when_line(end)}.")}


def end_focus_session(db, clock, params, source):
    session = (db.query(FocusSession).filter(FocusSession.status == "active")
               .order_by(FocusSession.start_utc.desc()).first())
    if not session:
        raise ToolError("There is no active focus session to end.")
    now = clock.now_utc()
    session.status = "completed"
    session.actual_end_utc = now
    session.outcome_note = (params.get("outcome_note") or "").strip()[:1000]
    _cancel_alerts(db, "focus_session", session.id, kinds=["focus_end"])
    if session.timer_id:
        timer = db.query(Timer).filter(Timer.id == session.timer_id, Timer.status == "active").first()
        if timer:
            timer.status = "cancelled"
            timer.ended_at = now
            _cancel_alerts(db, "timer", timer.id, kinds=["timer_due"])
    elapsed = int((now - session.start_utc).total_seconds() // 60)
    msg = f"Focus session #{session.id} ended after {elapsed} minute(s)."
    if session.outcome_note:
        msg += f" Outcome saved: '{session.outcome_note}'."
    return {"success": True, "message": msg, "entity_type": "focus_session", "entity_id": session.id}


def get_ai_radar_digest(db, clock, params, source):
    """Deterministic, offline digest read from stored AI Radar data — no LLM, no
    network. mode='free' answers the free-models/credits question."""
    mode = "free" if str(params.get("mode", "")).lower() == "free" else "general"
    state = ai_radar.get_radar_state(db, clock)
    message = ai_radar.format_digest(state, clock, mode=mode)
    return {"success": True, "read_only": True, "entity_type": None,
            "entity_id": None, "message": message}


def refresh_ai_radar(db, clock, params, source):
    """User-invoked research pass. Uses the live fetcher; a per-source failure is
    isolated and prior items are retained. May take a few seconds (network)."""
    result = ai_radar.run_radar(db, clock, trigger="manual")
    run = result["run"]
    msg = f"AI Radar refreshed ({result['status']}): {result['new_items']} new item(s)."
    if run.get("sources_failed"):
        msg += f" {run['sources_failed']} source(s) failed; kept previously stored items."
    return {"success": True, "entity_type": "ai_radar_run", "entity_id": run["id"], "message": msg}


def handle_open_website(db, clock, params, source):
    target = params.get("site_or_url") or params.get("url")
    if not target:
        raise ToolError("Target website or URL is empty.")
    try:
        import system_actions
    except ImportError:
        from . import system_actions
    res = system_actions.execute_open_url(
        target, db=None, clock=clock, source=source,
        browser_launcher=params.get("browser_launcher"),
        write_receipt=False
    )
    if not res.get("success"):
        raise ToolError(res.get("message") or res.get("response") or "URL execution failed.")
    return {
        "success": True,
        "message": res["response"],
        "entity_type": "url",
        "entity_id": None,
    }


def handle_open_app(db, clock, params, source):
    try:
        import system_actions
    except ImportError:
        from . import system_actions
    res = system_actions.execute_open_app(
        params.get("name"), db=None, clock=clock, source=source,
        write_receipt=False, app_launcher=params.get("app_launcher"))
    if not res.get("success"):
        raise ToolError(res.get("message") or "Application launch failed.")
    return {"success": True, "message": res["response"],
            "entity_type": "app", "entity_id": None}


HANDLERS = {
    "create_task": create_task,
    "list_tasks": list_tasks,
    "set_task_completed": set_task_completed,
    "start_timer": start_timer,
    "cancel_timer": cancel_timer,
    "create_reminder": create_reminder,
    "snooze_reminder": snooze_reminder,
    "start_focus_session": start_focus_session,
    "end_focus_session": end_focus_session,
    "get_ai_radar_digest": get_ai_radar_digest,
    "refresh_ai_radar": refresh_ai_radar,
    "open_website": handle_open_website,
    "system.open_url": handle_open_website,
    "open_app": handle_open_app,
}


def execute_intent(db, clock, intent, params, source="typed",
                   idempotency_key=None, command_text=None):
    """Validate + execute one intent, returning an action receipt dict.

    Idempotency: when ``idempotency_key`` is supplied and a receipt with that
    key already exists, the stored receipt is returned (replayed=True) and
    nothing executes again.

    Capability boundary: a request that carries ``command_text`` is checked
    against the tool's real destination before anything runs. Asking VEGA to
    reach a system it has no tool for (calendar, email, a messaging app) is
    refused here — in both lanes, at the one choke point — without writing an
    entity or a receipt. Direct REST/UI calls pass no ``command_text``: an
    explicit click is the user choosing the destination, not guessing one.
    """
    if intent not in HANDLERS:
        decision = policy.evaluate_policy(
            intent, params or {}, context={"source": source, "command_text": command_text}
        )
        raise ToolError(decision.reason)

    target_key = idempotency.request_target_key(intent, params)

    if command_text:
        blocked = command_parser.unsupported_destination(command_text, True)
        if blocked and not (intent in ("open_website", "system.open_url", "open_app") and command_parser.is_open_request(command_text)):
            # Nothing has run yet, so nothing needs rolling back — and a
            # refused request must leave no trace in the database.
            db.rollback()
            return {"success": False, "clarification": True,
                    "blocked": blocked["destination"], "action": intent,
                    "message": blocked["message"],
                    "entity_type": None, "entity_id": None}

    if idempotency_key:
        existing = (db.query(ActionReceipt)
                    .filter(ActionReceipt.idempotency_key == idempotency_key).first())
        if existing:
            if idempotency.receipt_conflict(existing, intent, target_key):
                raise ToolError("Idempotency key already belongs to another action or target.")
            out = existing.to_dict()
            out["replayed"] = True
            return out

    handler = HANDLERS[intent]
    policy_decision = None
    try:
        policy_decision = policy.evaluate_policy(
            intent, params or {}, context={"source": source, "command_text": command_text}
        )
        if not policy_decision.allowed:
            if policy_decision.risk_level == policy.RISK_LEVEL_0_READ_ONLY:
                db.rollback()
                return {"success": False, "read_only": True, "action": intent,
                        "message": policy_decision.reason, "entity_type": None, "entity_id": None,
                        "policy_decision": policy_decision.to_dict()}
            raise ToolError(policy_decision.reason)

        result = handler(db, clock, params, source)
    except NeedsClarification as e:
        db.rollback()
        return {"success": False, "clarification": True, "action": intent,
                "message": str(e), "entity_type": None, "entity_id": None}
    except ToolError as e:
        db.rollback()
        receipt = ActionReceipt(
            idempotency_key=idempotency_key, action=intent, target_key=target_key,
            success=False,
            message=str(e), command_text=(command_text or "")[:600] or None,
            source=source, created_utc=clock.now_utc())
        db.add(receipt)
        db.commit()
        out = receipt.to_dict()
        if policy_decision:
            out["policy_decision"] = policy_decision.to_dict()
        return out

    if result.get("read_only"):
        # Read-only queries get no receipt row; they mutate nothing. Carry
        # through any structured payload the read handler produced (e.g. Today
        # 'items', session 'draft') so REST/UI consumers get the data, not just
        # the human-readable message.
        out = {"success": True, "read_only": True, "action": intent,
               "message": result["message"],
               "entity_type": result.get("entity_type"),
               "entity_id": result.get("entity_id")}
        for k in ("items", "draft"):
            if k in result:
                out[k] = result[k]
        if policy_decision:
            out["policy_decision"] = policy_decision.to_dict()
        db.commit()
        return out

    receipt = ActionReceipt(
        idempotency_key=idempotency_key if idempotency_key else None,
        target_key=target_key,
        action=intent, success=bool(result.get("success", True)),
        entity_type=result.get("entity_type"), entity_id=result.get("entity_id"),
        message=result["message"], command_text=(command_text or "")[:600] or None,
        source=source, created_utc=clock.now_utc())
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent retry with the same idempotency key: the other request
        # already committed a receipt — return that one instead of duplicating.
        db.rollback()
        if idempotency_key:
            existing = (db.query(ActionReceipt)
                        .filter(ActionReceipt.idempotency_key == idempotency_key).first())
            if existing:
                if idempotency.receipt_conflict(existing, intent, target_key):
                    raise ToolError("Idempotency key already belongs to another action or target.")
                out = existing.to_dict()
                out["replayed"] = True
                return out
        raise
    out = receipt.to_dict()
    if policy_decision:
        out["policy_decision"] = policy_decision.to_dict()
    return out


# ── P2 workspace handlers (added to the SAME one executor / receipt path) ──
# Imported last so `workspaces` can import ToolError/NeedsClarification from
# this module without a circular-import problem. Reads (resume/list/today/draft)
# return read_only and produce no receipt; writes go through the idempotent
# ActionReceipt flow above.
try:
    import workspaces as _workspaces
except ImportError:  # pragma: no cover - package-relative fallback
    from . import workspaces as _workspaces

HANDLERS.update({
    "register_workspace": _workspaces.register_workspace,
    "update_workspace": _workspaces.update_workspace,
    "list_workspaces": _workspaces.list_workspaces,
    "resume_workspace": _workspaces.resume_workspace,
    "link_task_to_workspace": _workspaces.link_task_to_workspace,
    "add_session_note": _workspaces.add_session_note,
    "build_session_draft": _workspaces.build_session_draft,
    "get_today": _workspaces.get_today,
    # Stage 3 — smallest academic loop
    "add_coursework": _workspaces.add_coursework,
    "list_coursework": _workspaces.list_coursework,
    "suggest_study": _workspaces.suggest_study,
    "complete_coursework": _workspaces.complete_coursework,
})
