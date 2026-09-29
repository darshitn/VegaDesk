"""Tool executor + dispatcher behavior: receipts, idempotency, cancellation,
snooze, persistence across a simulated restart, and clarification paths."""

from datetime import datetime, timedelta

import dispatcher
import tools
from db import Task, Timer, Reminder, FocusSession, ScheduledAlert, ActionReceipt, SessionLocal


def _dispatch(db, clock, text, **kwargs):
    return dispatcher.dispatch_command(db, clock, text, source="test", **kwargs)


def test_full_spec_flow_offline(db, clock):
    r = _dispatch(db, clock, "Add a task: finish the DBMS assignment by Friday at 6 PM")
    assert r["handled"] and r["receipt"]["success"]
    task_id = r["receipt"]["entity_id"]
    task = db.query(Task).filter(Task.id == task_id).first()
    assert task.text == "finish the DBMS assignment"
    assert task.deadline_utc == datetime(2026, 9, 25, 12, 30, 0)
    assert "2026-09-25T12:30:00Z" in r["response"]  # ISO in reply
    assert "Friday" in r["response"]                 # readable local rendering

    r = _dispatch(db, clock, "What is due this week?")
    assert "finish the DBMS assignment" in r["response"]

    r = _dispatch(db, clock, f"complete task {task_id}")
    assert r["receipt"]["success"]
    db.refresh(task)
    assert task.completed is True
    # deadline alert cancelled on completion
    pending = db.query(ScheduledAlert).filter(
        ScheduledAlert.entity_type == "task", ScheduledAlert.entity_id == task_id,
        ScheduledAlert.status == "pending").count()
    assert pending == 0


def test_timer_lifecycle(db, clock):
    r = _dispatch(db, clock, "Set a timer for 25 minutes")
    timer_id = r["receipt"]["entity_id"]
    timer = db.query(Timer).filter(Timer.id == timer_id).first()
    assert timer.status == "active"
    assert timer.end_utc == clock.now_utc() + timedelta(minutes=25)
    assert timer.timezone_name  # tz context stored
    alert = db.query(ScheduledAlert).filter(
        ScheduledAlert.entity_type == "timer", ScheduledAlert.entity_id == timer_id).first()
    assert alert.status == "pending" and alert.kind == "timer_due"

    r = _dispatch(db, clock, "Cancel my timer")
    assert r["receipt"]["success"]
    db.refresh(timer)
    db.expire_all()
    timer = db.query(Timer).filter(Timer.id == timer_id).first()
    assert timer.status == "cancelled"
    db.expire_all()
    alert = db.query(ScheduledAlert).filter(ScheduledAlert.id == alert.id).first()
    assert alert.status == "cancelled"


def test_cancel_timer_ambiguous_asks_which(db, clock):
    _dispatch(db, clock, "Set a timer for 10 minutes")
    _dispatch(db, clock, "Set a timer for 20 minutes")
    r = _dispatch(db, clock, "Cancel my timer")
    assert r["clarification"] is True
    assert "which one" in r["response"].lower()
    active = db.query(Timer).filter(Timer.status == "active").count()
    assert active == 2  # nothing cancelled by the ambiguous request


def test_reminder_create_and_snooze(db, clock):
    r = _dispatch(db, clock, "Remind me tomorrow at 7 PM to revise trees")
    rem_id = r["receipt"]["entity_id"]
    rem = db.query(Reminder).filter(Reminder.id == rem_id).first()
    assert rem.due_utc == datetime(2026, 9, 22, 13, 30, 0)
    assert rem.status == "pending"

    # Snooze a pending (not-yet-due) reminder postpones from its due time.
    r = _dispatch(db, clock, "Snooze that reminder for 10 minutes")
    assert r["receipt"]["success"]
    db.expire_all()
    rem = db.query(Reminder).filter(Reminder.id == rem_id).first()
    assert rem.due_utc == datetime(2026, 9, 22, 13, 40, 0)
    assert rem.snooze_count == 1
    pending = db.query(ScheduledAlert).filter(
        ScheduledAlert.entity_type == "reminder", ScheduledAlert.status == "pending").count()
    assert pending == 1  # old alert cancelled, new one created — exactly one


def test_focus_session_lifecycle(db, clock):
    r = _dispatch(db, clock, "Start a 45-minute focus session for DSA")
    sid = r["receipt"]["entity_id"]
    sess = db.query(FocusSession).filter(FocusSession.id == sid).first()
    assert sess.status == "active" and sess.objective == "DSA"
    assert sess.timer_id is not None
    assert sess.planned_end_utc == clock.now_utc() + timedelta(minutes=45)

    # second active session refused
    r2 = _dispatch(db, clock, "Start a 25-minute focus session for maths")
    assert r2["receipt"]["success"] is False

    r = _dispatch(db, clock, "End my focus session; I finished recursion practice")
    assert r["receipt"]["success"]
    db.expire_all()
    sess = db.query(FocusSession).filter(FocusSession.id == sid).first()
    assert sess.status == "completed"
    assert sess.outcome_note == "I finished recursion practice"
    timer = db.query(Timer).filter(Timer.id == sess.timer_id).first()
    assert timer.status == "cancelled"


def test_idempotency_key_prevents_duplicate(db, clock):
    r1 = _dispatch(db, clock, "Add a task: submit lab record", idempotency_key="key-abc")
    r2 = _dispatch(db, clock, "Add a task: submit lab record", idempotency_key="key-abc")
    assert r1["receipt"]["entity_id"] == r2["receipt"]["entity_id"]
    assert r2["receipt"].get("replayed") is True
    assert db.query(Task).filter(Task.text == "submit lab record").count() == 1
    assert db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "key-abc").count() == 1


def test_different_keys_create_separate_records(db, clock):
    _dispatch(db, clock, "Add a task: submit lab record", idempotency_key="k1")
    _dispatch(db, clock, "Add a task: submit lab record", idempotency_key="k2")
    assert db.query(Task).filter(Task.text == "submit lab record").count() == 2


def test_state_survives_restart(db, clock):
    r = _dispatch(db, clock, "Remind me tomorrow at 7 PM to revise trees")
    rem_id = r["receipt"]["entity_id"]
    _dispatch(db, clock, "Set a timer for 30 minutes")
    _dispatch(db, clock, "Start a 45-minute focus session for DSA")
    _dispatch(db, clock, "Add a task: finish the DBMS assignment by Friday at 6 PM")

    # Simulate backend restart: brand-new session on the same DB file.
    db2 = SessionLocal()
    try:
        assert db2.query(Reminder).filter(Reminder.id == rem_id).first().status == "pending"
        assert db2.query(Timer).filter(Timer.status == "active").count() >= 2  # timer + focus timer
        assert db2.query(FocusSession).filter(FocusSession.status == "active").count() == 1
        assert db2.query(Task).filter(Task.text == "finish the DBMS assignment").first().deadline_utc \
            == datetime(2026, 9, 25, 12, 30, 0)
    finally:
        db2.close()


def test_every_mutation_has_receipt(db, clock):
    cmds = [
        "Add a task: write report",
        "Set a timer for 5 minutes",
        "Remind me tomorrow at 9 AM to stretch",
        "Start a 25-minute focus session for physics",
        "End my focus session",
        "Cancel my timer",
    ]
    for cmd in cmds:
        r = _dispatch(db, clock, cmd)
        assert r["handled"], cmd
        assert r["receipt"] is not None, cmd
        assert r["receipt"]["created_utc"], cmd
    # read-only listing creates no receipt
    before = db.query(ActionReceipt).count()
    _dispatch(db, clock, "list my tasks")
    assert db.query(ActionReceipt).count() == before


def test_failed_action_receipt_recorded(db, clock):
    r = _dispatch(db, clock, "Cancel my timer")  # no timer exists
    assert r["receipt"]["success"] is False
    assert db.query(ActionReceipt).filter(ActionReceipt.success.is_(False)).count() == 1


def test_set_completed_is_idempotent_not_toggle(db, clock):
    r = _dispatch(db, clock, "Add a task: pay fees")
    tid = r["receipt"]["entity_id"]
    _dispatch(db, clock, f"complete task {tid}")
    r = _dispatch(db, clock, f"complete task {tid}")  # retry
    assert r["receipt"]["success"]
    assert "already completed" in r["response"]
    task = db.query(Task).filter(Task.id == tid).first()
    assert task.completed is True  # state not reversed by retry


# ── pre-execution capability guard (tools.execute_intent) ──────────

def test_unsupported_destination_executes_nothing(db, clock):
    """The live defect: asked for a calendar entry, the model proposed the
    nearest available write and VEGA created a task for it. The refusal must
    happen before the handler runs, so there is no entity AND no receipt — a
    failed-action receipt (the ToolError path) would still be a write."""
    r = tools.execute_intent(
        db, clock, "create_task", {"text": "Dentist appointment"},
        source="model", command_text="schedule a dentist appointment on my calendar")
    assert r["success"] is False
    assert r["clarification"] is True
    assert r["blocked"] == "your calendar"
    assert r["entity_type"] is None and r["entity_id"] is None
    assert 'say "add a task"' in r["message"]
    assert db.query(Task).count() == 0
    assert db.query(ActionReceipt).count() == 0


def test_explicit_task_wording_still_writes_through_the_dispatcher(db, clock):
    """The other side of the same request: naming a task is the user's own
    choice of destination, so it executes normally and is receipted."""
    r = _dispatch(db, clock, "add a task to book a dentist appointment")
    assert r["handled"] and r["receipt"]["success"]
    assert db.query(Task).count() == 1
    receipt = db.query(ActionReceipt).first()
    assert receipt.action == "create_task"
    assert receipt.command_text == "add a task to book a dentist appointment"


def test_direct_ui_call_is_not_blocked(db, clock):
    """No command_text means the user picked the destination in the hub rather
    than phrasing a request — nothing to contradict, so the guard stands down."""
    r = tools.execute_intent(db, clock, "create_task",
                             {"text": "dentist (calendar)"}, source="ui")
    assert r["success"] and r["entity_id"]
    assert db.query(Task).count() == 1


def test_deterministic_offline_commands_are_untouched(db, clock):
    """Regression net for the guard sitting on the shared path: every command
    form the deterministic parser already handles must still execute. The
    'lab'/'assignment' forms are here because the exemption list was trimmed
    around exactly those words.

    Not in this list on purpose: "what's on my plate today" and "any new free
    models?" — both discovered while writing this test to fall through to the
    model lane rather than parsing deterministically (the radar branch needs a
    whole word 'model', and the today branch expects 'plate for today'). That
    is a pre-existing parser gap, unrelated to the guard, and left as-is.
    """
    cmds = [
        "Add a task: finish the DBMS assignment by Friday at 6 PM",
        "add lab: DBMS titration due monday",
        "add assignment: linear algebra problem set due friday",
        "remind me tomorrow at 7 pm to email the professor",
        "Set a timer for 25 minutes",
        "Start a 25-minute focus session for physics",
        "What is due this week?",
        "list my tasks",
        "register a project called Thesis (academic) with next action write the intro",
        "add a session note: finished the normalization chapter",
        "resume my work",
    ]
    for cmd in cmds:
        r = _dispatch(db, clock, cmd)
        assert r["handled"], cmd
        assert not r["receipt"].get("blocked"), cmd
        assert r["receipt"]["success"] or r["receipt"].get("read_only"), cmd
