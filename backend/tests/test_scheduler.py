"""Alert scheduler: at-most-once delivery, restart reconciliation, entity state.

These exercise the single scheduler owner directly (no WebSocket / event loop):
tick() is the sync pass the async loop delegates to. A sentinel object stands in
for a connected /ws/alerts client so delivery is gated the same way it is in
production (alerts are only claimed when someone can show them).
"""

from datetime import datetime, timedelta

from db import SessionLocal, ScheduledAlert, ActionReceipt, Timer, Reminder
from scheduler import AlertScheduler
import timeutil


class _FakeWS:
    """Sentinel subscriber — presence is all the scheduler checks."""
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _make_scheduler(clock, grace_minutes=30):
    sched = AlertScheduler(SessionLocal, clock=clock, grace_minutes=grace_minutes)
    return sched


def _add_alert(db, kind, entity_id, due_utc, message="due", entity_type="timer"):
    alert = ScheduledAlert(kind=kind, entity_type=entity_type, entity_id=entity_id,
                           due_utc=due_utc, status="pending", message=message)
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def test_due_alert_delivered_once(db, clock):
    now = clock.now_utc()
    _add_alert(db, "timer_due", 1, now - timedelta(seconds=5))

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())

    first = sched.tick()
    assert len(first) == 1

    # Second pass must not re-deliver (at-most-once).
    second = sched.tick()
    assert second == []

    db.expire_all()
    alert = db.query(ScheduledAlert).filter(ScheduledAlert.entity_id == 1).first()
    assert alert.status == "delivered"
    assert alert.delivered_at is not None


def test_no_delivery_without_listener(db, clock):
    now = clock.now_utc()
    _add_alert(db, "timer_due", 1, now - timedelta(seconds=5))

    sched = _make_scheduler(clock)  # nobody subscribed
    assert sched.tick() == []

    db.expire_all()
    alert = db.query(ScheduledAlert).first()
    assert alert.status == "pending"  # stays claimable until a listener appears


def test_late_within_grace_prefixed(db, clock):
    now = clock.now_utc()
    _add_alert(db, "reminder_due", 7, now - timedelta(seconds=120),
               message="revise trees", entity_type="reminder")

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert len(delivered) == 1
    assert delivered[0]["message"].startswith("(missed while away)")


def test_on_time_not_prefixed(db, clock):
    now = clock.now_utc()
    _add_alert(db, "reminder_due", 7, now - timedelta(seconds=10),
               message="revise trees", entity_type="reminder")

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert delivered[0]["message"] == "revise trees"


def test_stale_alert_marked_missed_silently(db, clock):
    now = clock.now_utc()
    # Due 2 hours ago, well outside the 30-min grace window.
    _add_alert(db, "timer_due", 3, now - timedelta(hours=2))

    sched = _make_scheduler(clock, grace_minutes=30)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    # Stale alerts are never broadcast (no bursts), even with a listener.
    assert delivered == []

    db.expire_all()
    alert = db.query(ScheduledAlert).filter(ScheduledAlert.entity_id == 3).first()
    assert alert.status == "missed"

    receipt = (db.query(ActionReceipt)
               .filter(ActionReceipt.entity_id == 3,
                       ActionReceipt.action == "alert_delivery").first())
    assert receipt is not None
    assert receipt.success is False
    assert "grace window" in receipt.message


def test_timer_entity_fires_on_delivery(db, clock):
    now = clock.now_utc()
    timer = Timer(label="eggs", duration_seconds=60, start_utc=now - timedelta(seconds=65),
                  end_utc=now - timedelta(seconds=5), status="active",
                  source="assistant", timezone_name="IST", created_at=now)
    db.add(timer)
    db.commit()
    db.refresh(timer)
    _add_alert(db, "timer_due", timer.id, now - timedelta(seconds=5),
               message="Timer done", entity_type="timer")

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    assert len(sched.tick()) == 1

    db.expire_all()
    t = db.query(Timer).filter(Timer.id == timer.id).first()
    assert t.status == "fired"
    assert t.ended_at is not None


def test_reminder_entity_fires_on_delivery(db, clock):
    now = clock.now_utc()
    rem = Reminder(text="revise trees", due_utc=now - timedelta(seconds=5),
                   status="pending", snooze_count=0, source="assistant",
                   timezone_name="IST", created_at=now)
    db.add(rem)
    db.commit()
    db.refresh(rem)
    _add_alert(db, "reminder_due", rem.id, now - timedelta(seconds=5),
               message="revise trees", entity_type="reminder")

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    assert len(sched.tick()) == 1

    db.expire_all()
    r = db.query(Reminder).filter(Reminder.id == rem.id).first()
    assert r.status == "fired"
    assert r.fired_at is not None


def test_task_deadline_leaves_entity_open(db, clock):
    now = clock.now_utc()
    _add_alert(db, "task_deadline", 42, now - timedelta(seconds=5),
               message="DBMS due", entity_type="task")

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    delivered = sched.tick()

    assert len(delivered) == 1
    # No task row exists / would change; assert the scheduler didn't error and
    # the alert moved to delivered.
    db.expire_all()
    alert = db.query(ScheduledAlert).filter(ScheduledAlert.entity_id == 42).first()
    assert alert.status == "delivered"


def test_successful_delivery_writes_receipt(db, clock):
    now = clock.now_utc()
    _add_alert(db, "timer_due", 9, now - timedelta(seconds=5), message="done")

    sched = _make_scheduler(clock)
    sched.subscribe(_FakeWS())
    sched.tick()

    db.expire_all()
    receipt = (db.query(ActionReceipt)
               .filter(ActionReceipt.entity_id == 9,
                       ActionReceipt.action == "alert_delivery").first())
    assert receipt is not None
    assert receipt.success is True
    assert receipt.source == "scheduler"
