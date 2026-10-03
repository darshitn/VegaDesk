"""Single-owner alert scheduler for VEGA M1.

The scheduler is an asyncio task inside the FastAPI app process (one owner).
Delivery is at-most-once even if overlapping processes exist (uvicorn --reload,
packaged relaunch): each due alert row is claimed with an atomic
``UPDATE ... WHERE status='pending'``; only the winner delivers.

Restart reconciliation grace rule (documented):
- Alerts that came due within VEGA_ALERT_GRACE_MIN minutes (default 30) are
  delivered once, prefixed with "(missed while away)" when late by >60s.
- Alerts older than the grace window are marked 'missed' WITHOUT notification
  (no bursts of stale alerts) and get a success=False delivery receipt so the
  miss stays observable in the hub/receipt log.
- Alerts are only claimed when at least one /ws/alerts listener is connected;
  otherwise they stay pending until delivered or aged into 'missed'.

Notification path: the backend broadcasts the alert over /ws/alerts. The
renderer (App.jsx, which stays mounted while the overlay is hidden) raises the
OS notification plus a visible in-app fallback. Delivery failures are recorded
as receipts and never re-execute the underlying action.
"""

import asyncio
import os
import threading
from datetime import timedelta
from typing import Optional

from sqlalchemy import update

try:
    from db import ScheduledAlert, ActionReceipt, Timer, Reminder
    import timeutil
except ImportError:
    from .db import ScheduledAlert, ActionReceipt, Timer, Reminder
    from . import timeutil

GRACE_MINUTES = int(os.getenv("VEGA_ALERT_GRACE_MIN", "30"))
LATE_THRESHOLD_SECONDS = 60


class AlertScheduler:
    def __init__(self, session_factory, clock=None, poll_seconds=1.0,
                 grace_minutes=GRACE_MINUTES):
        self._session_factory = session_factory
        self._clock = clock or timeutil.SystemClock()
        self._poll_seconds = poll_seconds
        self._grace = timedelta(minutes=grace_minutes)
        self._clients: set = set()
        self._lock = threading.Lock()
        self._last_tick_utc = None
        self._last_error = None
        self._is_running = False
        self._loop_task = None

    # ── subscriber management (called from the event loop) ──
    def subscribe(self, ws):
        with self._lock:
            self._clients.add(ws)

    def unsubscribe(self, ws):
        with self._lock:
            self._clients.discard(ws)

    def has_clients(self) -> bool:
        with self._lock:
            return bool(self._clients)

    def get_status(self) -> dict:
        is_alive = self._is_running
        if self._loop_task is not None:
            is_alive = not self._loop_task.done() and not self._loop_task.cancelled()
        safe_error = self._sanitize_error(self._last_error)
        status = "ready" if is_alive and not safe_error else ("degraded" if safe_error else ("starting" if self._loop_task else "stopped"))
        return {
            "status": status,
            "running": is_alive,
            "last_tick": self._last_tick_utc.isoformat() if self._last_tick_utc else None,
            "error": safe_error,
            "client_count": len(self._clients),
        }

    @staticmethod
    def _sanitize_error(err: Any) -> Optional[str]:
        if err is None:
            return None
        err_str = str(err)
        if not err_str:
            return None
        import re
        # If the error string contains paths, strip them to a stable safe reason
        if re.search(r"[A-Za-z]:[\\/]|/(?:Users|home|root|tmp)/", err_str):
            m = re.match(r"^([A-Za-z0-9_]+Error):", err_str)
            exc_type = m.group(1) if m else "Error"
            return f"{exc_type}: scheduler tick failed"
        # Redact secrets if any
        err_str = re.sub(r"(AIza[0-9A-Za-z-_]{35})", "<redacted>", err_str)
        err_str = re.sub(r"(sk-[0-9A-Za-z]{20,})", "<redacted>", err_str)
        err_str = re.sub(r"\b[0-9a-fA-F]{24,}\b", "<redacted>", err_str)
        return err_str[:120]

    # ── main loop ──
    async def run(self):
        self._is_running = True
        try:
            while True:
                try:
                    delivered = await asyncio.to_thread(self.tick)
                    self._last_tick_utc = self._clock.now_utc()
                    self._last_error = None
                    for alert in delivered:
                        await self._broadcast(alert)
                except Exception as e:  # keep the scheduler alive; log observably
                    self._last_error = f"{type(e).__name__}: scheduler tick failed"
                    print(f"[SCHEDULER] tick error: {e}", flush=True)
                await asyncio.sleep(self._poll_seconds)
        finally:
            self._is_running = False

    async def _broadcast(self, alert: dict):
        dead = []
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json({"type": "alert", "alert": alert})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(ws)

    # ── one scheduler pass (sync; runs in a worker thread) ──
    def tick(self):
        """Claim and return due alerts, mark stale ones missed. Returns a list
        of alert dicts to broadcast (empty when nothing is due or no listener)."""
        db = self._session_factory()
        try:
            now = self._clock.now_utc()
            grace_cutoff = now - self._grace
            delivered = []

            # 1. Age out stale alerts (older than grace) — observable, silent.
            stale = (db.query(ScheduledAlert)
                     .filter(ScheduledAlert.status == "pending",
                             ScheduledAlert.due_utc < grace_cutoff).all())
            for alert in stale:
                claimed = db.execute(
                    update(ScheduledAlert)
                    .where(ScheduledAlert.id == alert.id,
                           ScheduledAlert.status == "pending")
                    .values(status="missed")
                ).rowcount
                if claimed:
                    self._apply_stale_entity_state(db, alert, now)
                    db.add(ActionReceipt(
                        idempotency_key=f"alert_delivery:{alert.id}",
                        action="alert_delivery", success=False,
                        entity_type=alert.entity_type, entity_id=alert.entity_id,
                        message=(f"Alert missed (older than {int(self._grace.total_seconds() // 60)} min "
                                 f"grace window): {alert.message}"),
                        source="scheduler", created_utc=now))

            # 1b. Reconcile any orphan active timers or pending reminders older than grace
            # only when an exact matching missed ScheduledAlert exists for their current due time.
            stale_timers = (db.query(Timer)
                            .filter(Timer.status == "active",
                                    Timer.end_utc < grace_cutoff).all())
            for t in stale_timers:
                missed_alerts = (
                    db.query(ScheduledAlert)
                    .filter(ScheduledAlert.entity_type == "timer",
                            ScheduledAlert.entity_id == t.id,
                            ScheduledAlert.status == "missed")
                    .all()
                )
                matching_missed = next(
                    (a for a in missed_alerts if abs((a.due_utc - t.end_utc).total_seconds()) < 1),
                    None
                )
                if matching_missed:
                    has_pending = (
                        db.query(ScheduledAlert)
                        .filter(ScheduledAlert.entity_type == "timer",
                                ScheduledAlert.entity_id == t.id,
                                ScheduledAlert.status == "pending")
                        .first()
                    )
                    if not has_pending:
                        t.status = "missed"
                        t.ended_at = now

            stale_reminders = (db.query(Reminder)
                               .filter(Reminder.status == "pending",
                                       Reminder.due_utc < grace_cutoff).all())
            for r in stale_reminders:
                missed_alerts = (
                    db.query(ScheduledAlert)
                    .filter(ScheduledAlert.entity_type == "reminder",
                            ScheduledAlert.entity_id == r.id,
                            ScheduledAlert.status == "missed")
                    .all()
                )
                matching_missed = next(
                    (a for a in missed_alerts if abs((a.due_utc - r.due_utc).total_seconds()) < 1),
                    None
                )
                if matching_missed:
                    has_pending = (
                        db.query(ScheduledAlert)
                        .filter(ScheduledAlert.entity_type == "reminder",
                                ScheduledAlert.entity_id == r.id,
                                ScheduledAlert.status == "pending")
                        .first()
                    )
                    if not has_pending:
                        r.status = "missed"

            # 2. Deliver due alerts only when someone can show them.
            if self.has_clients():
                due = (db.query(ScheduledAlert)
                       .filter(ScheduledAlert.status == "pending",
                               ScheduledAlert.due_utc <= now,
                               ScheduledAlert.due_utc >= grace_cutoff)
                       .order_by(ScheduledAlert.due_utc)
                       .limit(20).all())
                for alert in due:
                    claimed = db.execute(
                        update(ScheduledAlert)
                        .where(ScheduledAlert.id == alert.id,
                               ScheduledAlert.status == "pending")
                        .values(status="delivered", delivered_at=now)
                    ).rowcount
                    if not claimed:
                        continue  # another owner/process won the claim
                    self._apply_entity_state(db, alert, now)
                    lateness = (now - alert.due_utc).total_seconds()
                    message = alert.message
                    if lateness > LATE_THRESHOLD_SECONDS:
                        message = f"(missed while away) {message}"
                    db.add(ActionReceipt(
                        idempotency_key=f"alert_delivery:{alert.id}",
                        action="alert_delivery", success=True,
                        entity_type=alert.entity_type, entity_id=alert.entity_id,
                        message=f"Alert delivered: {alert.message}",
                        source="scheduler", created_utc=now))
                    payload = alert.to_dict()
                    payload["message"] = message
                    delivered.append(payload)

            db.commit()
            return delivered
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _apply_entity_state(self, db, alert, now):
        """Move the underlying record to its fired lifecycle state (once)."""
        if alert.kind == "timer_due":
            timer = db.query(Timer).filter(Timer.id == alert.entity_id).first()
            if (timer and timer.status == "active"
                    and abs((timer.end_utc - alert.due_utc).total_seconds()) < 1):
                timer.status = "fired"
                timer.ended_at = now
        elif alert.kind == "reminder_due":
            reminder = db.query(Reminder).filter(Reminder.id == alert.entity_id).first()
            if (reminder and reminder.status == "pending"
                    and abs((reminder.due_utc - alert.due_utc).total_seconds()) < 1):
                reminder.status = "fired"
                reminder.fired_at = now
        # task_deadline / focus_end intentionally leave their entity unchanged:
        # the task stays open until completed; the focus session stays active
        # until the user ends it.

    def _apply_stale_entity_state(self, db, alert, now):
        """Move the underlying record to its missed lifecycle state (once)."""
        if alert.kind == "timer_due":
            timer = db.query(Timer).filter(Timer.id == alert.entity_id).first()
            if (timer and timer.status == "active"
                    and abs((timer.end_utc - alert.due_utc).total_seconds()) < 1):
                timer.status = "missed"
                timer.ended_at = now
        elif alert.kind == "reminder_due":
            reminder = db.query(Reminder).filter(Reminder.id == alert.entity_id).first()
            if (reminder and reminder.status == "pending"
                    and abs((reminder.due_utc - alert.due_utc).total_seconds()) < 1):
                reminder.status = "missed"
                # fired_at intentionally remains None (never fired / delivered)
        # task_deadline / focus_end intentionally leave their entity unchanged:
        # the task stays open until completed; the focus session stays active
        # until the user ends it.
