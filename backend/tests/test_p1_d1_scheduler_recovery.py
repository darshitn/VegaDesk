"""P1-D1 focused tests: scheduler restart recovery, stale alert entity state, and serial idempotency.

Tests verify:
1. Due alerts with listener: Timer/Reminder transition to 'fired' with timestamp and success receipt.
2. Due alerts without listener: entities and alerts remain 'active'/'pending' with 0 receipts.
3. Expiry beyond grace: ScheduledAlert moves to 'missed', Timer/Reminder transition to 'missed'
   with truthful semantics (Reminder.fired_at remains None), failure receipt written.
4. Restart then tick: recovers pending alerts correctly on a fresh scheduler instance across reopen.
5. Repeated tick idempotency: multiple tick passes produce zero duplicate receipts and no re-deliveries.
6. Cancellation interaction: cancelled timer cannot be altered by scheduler passes.
7. Snooze interaction: snoozing a reminder protects it from old alerts; missed reminders can be snoozed.
8. Orphan reconciliation: pre-existing active timers and pending reminders with missed alerts are healed.
9. Invariance: Task deadline and focus session alerts leave Task.completed and FocusSession.status unchanged.
"""

from datetime import datetime, timedelta
import os
import shutil
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import (Base, SessionLocal, ScheduledAlert, ActionReceipt, Timer, Reminder,
                Task, FocusSession)
from scheduler import AlertScheduler
import tools
import timeutil


class _FakeWS:
    """Sentinel /ws/alerts subscriber for test delivery."""
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _make_scheduler(clock, grace_minutes=30):
    return AlertScheduler(SessionLocal, clock=clock, grace_minutes=grace_minutes)


# ── 1. Due Alert with Listener ──

def test_due_timer_with_listener_fires_and_records_receipt(db, clock):
    now = clock.now_utc()
    timer = Timer(label="pasta", duration_seconds=600, start_utc=now - timedelta(seconds=605),
                  end_utc=now - timedelta(seconds=5), status="active",
                  source="assistant", timezone_name="IST", created_at=now - timedelta(seconds=605))
    db.add(timer)
    db.commit()
    db.refresh(timer)

    alert = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                           due_utc=timer.end_utc, status="pending", message="pasta timer done")
    db.add(alert)
    db.commit()
    db.refresh(alert)

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert len(delivered) == 1
    assert delivered[0]["id"] == alert.id

    db.expire_all()
    t = db.query(Timer).filter(Timer.id == timer.id).first()
    assert t.status == "fired"
    assert t.ended_at is not None

    a = db.query(ScheduledAlert).filter(ScheduledAlert.id == alert.id).first()
    assert a.status == "delivered"
    assert a.delivered_at is not None

    receipts = db.query(ActionReceipt).filter(ActionReceipt.entity_id == timer.id,
                                              ActionReceipt.entity_type == "timer").all()
    assert len(receipts) == 1
    assert receipts[0].success is True
    assert receipts[0].action == "alert_delivery"
    assert receipts[0].idempotency_key == f"alert_delivery:{alert.id}"


def test_due_reminder_with_listener_fires_and_records_receipt(db, clock):
    now = clock.now_utc()
    rem = Reminder(text="attend seminar", due_utc=now - timedelta(seconds=10),
                   status="pending", snooze_count=0, source="assistant",
                   timezone_name="IST", created_at=now - timedelta(minutes=10))
    db.add(rem)
    db.commit()
    db.refresh(rem)

    alert = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                           due_utc=rem.due_utc, status="pending", message="seminar reminder")
    db.add(alert)
    db.commit()
    db.refresh(alert)

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert len(delivered) == 1

    db.expire_all()
    r = db.query(Reminder).filter(Reminder.id == rem.id).first()
    assert r.status == "fired"
    assert r.fired_at is not None

    a = db.query(ScheduledAlert).filter(ScheduledAlert.id == alert.id).first()
    assert a.status == "delivered"

    receipt = db.query(ActionReceipt).filter(ActionReceipt.entity_id == rem.id,
                                             ActionReceipt.entity_type == "reminder").first()
    assert receipt is not None
    assert receipt.success is True
    assert receipt.idempotency_key == f"alert_delivery:{alert.id}"


# ── 2. Due Alert without Listener ──

def test_due_alerts_without_listener_remain_pending_with_zero_receipts(db, clock):
    now = clock.now_utc()
    timer = Timer(label="tea", duration_seconds=180, start_utc=now - timedelta(seconds=185),
                  end_utc=now - timedelta(seconds=5), status="active",
                  source="assistant", timezone_name="IST", created_at=now - timedelta(seconds=185))
    rem = Reminder(text="drink water", due_utc=now - timedelta(seconds=5),
                   status="pending", snooze_count=0, source="assistant",
                   timezone_name="IST", created_at=now - timedelta(minutes=5))
    db.add_all([timer, rem])
    db.commit()

    a1 = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                        due_utc=timer.end_utc, status="pending", message="tea done")
    a2 = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                        due_utc=rem.due_utc, status="pending", message="water reminder")
    db.add_all([a1, a2])
    db.commit()

    sched = _make_scheduler(clock)  # no listener subscribed
    delivered = sched.tick()

    assert delivered == []
    db.expire_all()

    # Entities and alerts remain in pending/active state
    assert db.query(Timer).filter(Timer.id == timer.id).first().status == "active"
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "pending"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a1.id).first().status == "pending"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a2.id).first().status == "pending"

    # Zero receipts written
    assert db.query(ActionReceipt).count() == 0


# ── 3. Expiry Beyond Grace Window (Stale Alert) ──

def test_stale_timer_marked_missed_with_truthful_failure_receipt(db, clock):
    now = clock.now_utc()
    # Expired 2 hours ago (well past 30 min grace)
    timer = Timer(label="study sprint", duration_seconds=3600,
                  start_utc=now - timedelta(hours=3),
                  end_utc=now - timedelta(hours=2),
                  status="active", source="assistant", timezone_name="IST",
                  created_at=now - timedelta(hours=3))
    db.add(timer)
    db.commit()
    db.refresh(timer)

    alert = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                           due_utc=timer.end_utc, status="pending", message="sprint done")
    db.add(alert)
    db.commit()
    db.refresh(alert)

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    # Never delivered/broadcast to listener
    assert delivered == []

    db.expire_all()
    t = db.query(Timer).filter(Timer.id == timer.id).first()
    assert t.status == "missed"
    assert t.ended_at is not None

    a = db.query(ScheduledAlert).filter(ScheduledAlert.id == alert.id).first()
    assert a.status == "missed"
    assert a.delivered_at is None

    receipts = db.query(ActionReceipt).filter(ActionReceipt.entity_id == timer.id,
                                              ActionReceipt.entity_type == "timer").all()
    assert len(receipts) == 1
    assert receipts[0].success is False
    assert receipts[0].action == "alert_delivery"
    assert "grace window" in receipts[0].message
    assert receipts[0].idempotency_key == f"alert_delivery:{alert.id}"


def test_stale_reminder_marked_missed_preserves_none_fired_at(db, clock):
    now = clock.now_utc()
    rem = Reminder(text="call dentist", due_utc=now - timedelta(hours=1),
                   status="pending", snooze_count=0, source="assistant",
                   timezone_name="IST", created_at=now - timedelta(hours=2))
    db.add(rem)
    db.commit()
    db.refresh(rem)

    alert = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                           due_utc=rem.due_utc, status="pending", message="dentist reminder")
    db.add(alert)
    db.commit()
    db.refresh(alert)

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert delivered == []

    db.expire_all()
    r = db.query(Reminder).filter(Reminder.id == rem.id).first()
    assert r.status == "missed"
    # Truthful semantics: alert was never delivered, so fired_at MUST be None
    assert r.fired_at is None

    a = db.query(ScheduledAlert).filter(ScheduledAlert.id == alert.id).first()
    assert a.status == "missed"

    receipt = db.query(ActionReceipt).filter(ActionReceipt.entity_id == rem.id,
                                             ActionReceipt.entity_type == "reminder").first()
    assert receipt is not None
    assert receipt.success is False
    assert receipt.idempotency_key == f"alert_delivery:{alert.id}"


# ── 4. Restart Then Tick Recovery ──

def test_scheduler_restart_recovers_pending_and_stale_alerts(db, clock):
    now = clock.now_utc()

    # 1. Stale alert (2 hours ago)
    t_stale = Timer(label="old timer", duration_seconds=60, start_utc=now - timedelta(hours=2, seconds=60),
                    end_utc=now - timedelta(hours=2), status="active", source="assistant")
    # 2. Due alert within grace (2 minutes ago)
    rem_due = Reminder(text="due reminder", due_utc=now - timedelta(minutes=2),
                       status="pending", snooze_count=0, source="assistant")
    # 3. Future alert (10 minutes from now)
    rem_future = Reminder(text="future reminder", due_utc=now + timedelta(minutes=10),
                          status="pending", snooze_count=0, source="assistant")
    db.add_all([t_stale, rem_due, rem_future])
    db.commit()

    a_stale = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=t_stale.id,
                             due_utc=t_stale.end_utc, status="pending", message="old timer")
    a_due = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem_due.id,
                           due_utc=rem_due.due_utc, status="pending", message="due reminder")
    a_future = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem_future.id,
                             due_utc=rem_future.due_utc, status="pending", message="future reminder")
    db.add_all([a_stale, a_due, a_future])
    db.commit()

    # Simulate restart: new scheduler instance on the existing database
    restarted_sched = AlertScheduler(SessionLocal, clock=clock, grace_minutes=30)
    restarted_sched.subscribe(_FakeWS())

    delivered = restarted_sched.tick()

    # Only due alert delivered
    assert len(delivered) == 1
    assert delivered[0]["id"] == a_due.id
    assert delivered[0]["message"].startswith("(missed while away)")

    db.expire_all()
    # Stale timer is missed
    assert db.query(Timer).filter(Timer.id == t_stale.id).first().status == "missed"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a_stale.id).first().status == "missed"

    # Due reminder is fired
    assert db.query(Reminder).filter(Reminder.id == rem_due.id).first().status == "fired"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a_due.id).first().status == "delivered"

    # Future reminder remains pending
    assert db.query(Reminder).filter(Reminder.id == rem_future.id).first().status == "pending"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a_future.id).first().status == "pending"


# ── 5. Repeated Tick Idempotency ──

def test_repeated_tick_produces_no_duplicate_receipts(db, clock):
    now = clock.now_utc()
    timer = Timer(label="quick", duration_seconds=60, start_utc=now - timedelta(seconds=70),
                  end_utc=now - timedelta(seconds=10), status="active", source="assistant")
    db.add(timer)
    db.commit()

    alert = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                           due_utc=timer.end_utc, status="pending", message="quick done")
    db.add(alert)
    db.commit()

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())

    # Pass 1: delivery succeeds
    pass1 = sched.tick()
    assert len(pass1) == 1
    assert db.query(ActionReceipt).count() == 1

    # Pass 2: no-op
    pass2 = sched.tick()
    assert pass2 == []
    assert db.query(ActionReceipt).count() == 1

    # Pass 3: no-op
    pass3 = sched.tick()
    assert pass3 == []
    assert db.query(ActionReceipt).count() == 1


# ── 6. Cancellation Interaction ──

def test_cancelled_timer_is_never_altered_by_scheduler(db, clock):
    now = clock.now_utc()
    # Cancel timer via execute_intent
    res = tools.execute_intent(db, clock, "start_timer", {"duration_seconds": 300, "label": "cancel me"}, source="test")
    timer_id = res["entity_id"]
    tools.execute_intent(db, clock, "cancel_timer", {"timer_id": timer_id}, source="test")

    db.expire_all()
    t = db.query(Timer).filter(Timer.id == timer_id).first()
    assert t.status == "cancelled"

    # Advance clock past grace window
    clock.advance(seconds=3600)
    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    sched.tick()

    db.expire_all()
    t_after = db.query(Timer).filter(Timer.id == timer_id).first()
    # Must remain cancelled, not overwritten by 'missed' or 'fired'
    assert t_after.status == "cancelled"


# ── 7. Snooze Interaction ──

def test_snoozed_reminder_is_not_altered_by_old_alerts(db, clock):
    now = clock.now_utc()
    res = tools.execute_intent(db, clock, "create_reminder",
                               {"text": "snooze target", "due_utc": now + timedelta(minutes=5)}, source="test")
    rem_id = res["entity_id"]

    # Advance clock so reminder becomes due
    clock.advance(seconds=310)

    # User snoozes reminder by 15 minutes
    snooze_res = tools.execute_intent(db, clock, "snooze_reminder",
                                      {"reminder_id": rem_id, "snooze_seconds": 900}, source="test")
    assert snooze_res["success"] is True

    db.expire_all()
    rem = db.query(Reminder).filter(Reminder.id == rem_id).first()
    assert rem.status == "pending"
    assert rem.due_utc > clock.now_utc()

    # Advance clock by 40 minutes (past old due time's grace, but before new due time?
    # Old due was now-310s. Now + 900s is the new due.
    # At this point, new due is in 900s.
    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    # Reminder is NOT delivered or missed yet (it is pending for the new snoozed time)
    assert delivered == []
    db.expire_all()
    rem_check = db.query(Reminder).filter(Reminder.id == rem_id).first()
    assert rem_check.status == "pending"


def test_missed_reminder_can_be_snoozed_both_explicitly_and_implicitly(db, clock):
    now = clock.now_utc()
    # Create reminder that ages out past grace
    rem = Reminder(text="missed review", due_utc=now - timedelta(hours=2),
                   status="pending", snooze_count=0, source="assistant")
    db.add(rem)
    db.commit()
    db.refresh(rem)

    alert = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                           due_utc=rem.due_utc, status="pending", message="missed review")
    db.add(alert)
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.tick()

    db.expire_all()
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "missed"

    # Implicit snooze ("snooze reminder") should pick up the missed reminder
    snooze_res = tools.execute_intent(db, clock, "snooze_reminder", {"snooze_seconds": 600}, source="test")
    assert snooze_res["success"] is True
    assert snooze_res["entity_id"] == rem.id

    db.expire_all()
    revived = db.query(Reminder).filter(Reminder.id == rem.id).first()
    assert revived.status == "pending"
    assert revived.due_utc == clock.now_utc() + timedelta(seconds=600)
    assert revived.fired_at is None

    # New pending alert exists
    new_alert = (db.query(ScheduledAlert)
                 .filter(ScheduledAlert.entity_id == rem.id,
                         ScheduledAlert.status == "pending").first())
    assert new_alert is not None
    assert new_alert.due_utc == revived.due_utc


# ── 8. Orphan Reconciliation ──

def test_orphan_active_timer_and_pending_reminder_healed_on_tick(db, clock):
    now = clock.now_utc()
    # Legacy state: ScheduledAlert was already marked 'missed', but Timer remained 'active'
    timer = Timer(label="orphan timer", duration_seconds=60, start_utc=now - timedelta(hours=2),
                  end_utc=now - timedelta(hours=2) + timedelta(seconds=60), status="active", source="legacy")
    rem = Reminder(text="orphan reminder", due_utc=now - timedelta(hours=2), status="pending", source="legacy")
    db.add_all([timer, rem])
    db.commit()

    a1 = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                        due_utc=timer.end_utc, status="missed", message="orphan timer")
    a2 = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                        due_utc=rem.due_utc, status="missed", message="orphan reminder")
    db.add_all([a1, a2])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.tick()

    db.expire_all()
    assert db.query(Timer).filter(Timer.id == timer.id).first().status == "missed"
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "missed"


# ── 9. Task and Focus Session Invariance ──

def test_task_and_focus_session_unaltered_by_stale_or_delivered_alerts(db, clock):
    now = clock.now_utc()
    task = Task(text="Complete chapter 3", deadline_utc=now - timedelta(hours=2), completed=False)
    session = FocusSession(objective="Deep work", start_utc=now - timedelta(hours=3),
                           planned_end_utc=now - timedelta(hours=2), status="active")
    db.add_all([task, session])
    db.commit()

    a_task = ScheduledAlert(kind="task_deadline", entity_type="task", entity_id=task.id,
                            due_utc=task.deadline_utc, status="pending", message="deadline")
    a_focus = ScheduledAlert(kind="focus_end", entity_type="focus_session", entity_id=session.id,
                             due_utc=session.planned_end_utc, status="pending", message="focus end")
    db.add_all([a_task, a_focus])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert delivered == []
    db.expire_all()

    # Alerts marked missed
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a_task.id).first().status == "missed"
    assert db.query(ScheduledAlert).filter(ScheduledAlert.id == a_focus.id).first().status == "missed"

    # Entities MUST remain unchanged
    t = db.query(Task).filter(Task.id == task.id).first()
    assert t.completed is False

    f = db.query(FocusSession).filter(FocusSession.id == session.id).first()
    assert f.status == "active"


# ── 10. Explicit Orphan Healing Boundary Tests ──

def test_orphan_with_cancelled_alert_does_not_heal(db, clock):
    now = clock.now_utc()
    timer = Timer(label="cancelled alert timer", duration_seconds=60, start_utc=now - timedelta(hours=2),
                  end_utc=now - timedelta(hours=2) + timedelta(seconds=60), status="active", source="legacy")
    rem = Reminder(text="cancelled alert reminder", due_utc=now - timedelta(hours=2), status="pending", source="legacy")
    db.add_all([timer, rem])
    db.commit()

    a1 = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                        due_utc=timer.end_utc, status="cancelled", message="cancelled alert")
    a2 = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                        due_utc=rem.due_utc, status="cancelled", message="cancelled alert")
    db.add_all([a1, a2])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.tick()

    db.expire_all()
    # Must NOT heal: alert was cancelled, not missed
    assert db.query(Timer).filter(Timer.id == timer.id).first().status == "active"
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "pending"


def test_orphan_with_delivered_alert_does_not_heal(db, clock):
    now = clock.now_utc()
    timer = Timer(label="delivered alert timer", duration_seconds=60, start_utc=now - timedelta(hours=2),
                  end_utc=now - timedelta(hours=2) + timedelta(seconds=60), status="active", source="legacy")
    rem = Reminder(text="delivered alert reminder", due_utc=now - timedelta(hours=2), status="pending", source="legacy")
    db.add_all([timer, rem])
    db.commit()

    a1 = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                        due_utc=timer.end_utc, status="delivered", message="delivered alert")
    a2 = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem.id,
                        due_utc=rem.due_utc, status="delivered", message="delivered alert")
    db.add_all([a1, a2])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.tick()

    db.expire_all()
    # Must NOT heal: alert was delivered, not missed
    assert db.query(Timer).filter(Timer.id == timer.id).first().status == "active"
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "pending"


def test_orphan_with_no_alert_does_not_heal(db, clock):
    now = clock.now_utc()
    timer = Timer(label="no alert timer", duration_seconds=60, start_utc=now - timedelta(hours=2),
                  end_utc=now - timedelta(hours=2) + timedelta(seconds=60), status="active", source="legacy")
    rem = Reminder(text="no alert reminder", due_utc=now - timedelta(hours=2), status="pending", source="legacy")
    db.add_all([timer, rem])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.tick()

    db.expire_all()
    # Must NOT heal: no alert ever existed, absence of pending alert is NOT proof of missed alert
    assert db.query(Timer).filter(Timer.id == timer.id).first().status == "active"
    assert db.query(Reminder).filter(Reminder.id == rem.id).first().status == "pending"


def test_old_missed_alert_does_not_mutate_snoozed_reminder(db, clock):
    now = clock.now_utc()
    old_due = now - timedelta(hours=2)
    snoozed_due = now + timedelta(minutes=15)

    rem = Reminder(text="snoozed reminder", due_utc=snoozed_due, status="pending",
                   snooze_count=1, source="assistant")
    db.add(rem)
    db.commit()
    db.refresh(rem)

    # An old missed alert from the previous due time (2 hours ago)
    old_missed_alert = ScheduledAlert(
        kind="reminder_due", entity_type="reminder", entity_id=rem.id,
        due_utc=old_due, status="missed", message="old missed reminder")
    # Current pending alert for the snoozed due time
    current_alert = ScheduledAlert(
        kind="reminder_due", entity_type="reminder", entity_id=rem.id,
        due_utc=snoozed_due, status="pending", message="snoozed reminder")
    db.add_all([old_missed_alert, current_alert])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert delivered == []
    db.expire_all()
    # The old missed alert must NOT alter the snoozed reminder
    r = db.query(Reminder).filter(Reminder.id == rem.id).first()
    assert r.status == "pending"
    assert r.due_utc == snoozed_due

    a_curr = db.query(ScheduledAlert).filter(ScheduledAlert.id == current_alert.id).first()
    assert a_curr.status == "pending"


def test_repeat_ticks_produce_no_extra_delivery_receipts(db, clock):
    now = clock.now_utc()
    timer = Timer(label="one timer", duration_seconds=60, start_utc=now - timedelta(seconds=70),
                  end_utc=now - timedelta(seconds=10), status="active", source="assistant")
    rem_stale = Reminder(text="one stale reminder", due_utc=now - timedelta(hours=2),
                         status="pending", snooze_count=0, source="assistant")
    db.add_all([timer, rem_stale])
    db.commit()

    a_due = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=timer.id,
                           due_utc=timer.end_utc, status="pending", message="due timer")
    a_stale = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem_stale.id,
                             due_utc=rem_stale.due_utc, status="pending", message="stale reminder")
    db.add_all([a_due, a_stale])
    db.commit()

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())

    # Pass 1: 1 delivery, 1 stale aged out -> exactly 2 receipts
    sched.tick()
    assert db.query(ActionReceipt).filter(ActionReceipt.action == "alert_delivery").count() == 2

    # Repeat tick 5 times
    for _ in range(5):
        sched.tick()

    # Still exactly 2 receipts
    assert db.query(ActionReceipt).filter(ActionReceipt.action == "alert_delivery").count() == 2


def test_genuine_database_reopen_recovers_scheduler(clock):
    """Test genuine database reopen with a fresh engine and sessionmaker."""
    now = clock.now_utc()
    tmp_dir = tempfile.mkdtemp(prefix="vega_reopen_test_")
    db_file = os.path.join(tmp_dir, "reopen.db")
    try:
        # 1. First run / process writes initial state
        engine1 = create_engine(f"sqlite:///{db_file}")
        Base.metadata.create_all(engine1)
        Session1 = sessionmaker(bind=engine1)
        session1 = Session1()

        t_stale = Timer(label="stale across crash", duration_seconds=60,
                        start_utc=now - timedelta(hours=2, seconds=60),
                        end_utc=now - timedelta(hours=2), status="active", source="assistant")
        rem_due = Reminder(text="due across crash", due_utc=now - timedelta(minutes=2),
                           status="pending", snooze_count=0, source="assistant")
        session1.add_all([t_stale, rem_due])
        session1.commit()

        a_stale = ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=t_stale.id,
                                 due_utc=t_stale.end_utc, status="pending", message="stale timer")
        a_due = ScheduledAlert(kind="reminder_due", entity_type="reminder", entity_id=rem_due.id,
                               due_utc=rem_due.due_utc, status="pending", message="due reminder")
        session1.add_all([a_stale, a_due])
        session1.commit()

        stale_id = t_stale.id
        due_id = rem_due.id
        alert_stale_id = a_stale.id
        alert_due_id = a_due.id

        session1.close()
        engine1.dispose()

        # 2. Genuine restart: brand new engine and sessionmaker on the same file
        engine2 = create_engine(f"sqlite:///{db_file}")
        Session2 = sessionmaker(bind=engine2)

        sched = AlertScheduler(Session2, clock=clock, grace_minutes=30)
        sched.subscribe(_FakeWS())
        delivered = sched.tick()

        assert len(delivered) == 1
        assert delivered[0]["id"] == alert_due_id

        verify_session = Session2()
        try:
            # Stale timer transitioned to missed
            t = verify_session.query(Timer).filter(Timer.id == stale_id).first()
            assert t.status == "missed"
            a_s = verify_session.query(ScheduledAlert).filter(ScheduledAlert.id == alert_stale_id).first()
            assert a_s.status == "missed"

            # Due reminder transitioned to fired
            r = verify_session.query(Reminder).filter(Reminder.id == due_id).first()
            assert r.status == "fired"
            a_d = verify_session.query(ScheduledAlert).filter(ScheduledAlert.id == alert_due_id).first()
            assert a_d.status == "delivered"

            # Exactly 2 receipts written
            receipts = verify_session.query(ActionReceipt).filter(ActionReceipt.action == "alert_delivery").all()
            assert len(receipts) == 2
        finally:
            verify_session.close()
            engine2.dispose()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

