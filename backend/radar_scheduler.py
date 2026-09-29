"""Daily AI Radar scheduler.

One asyncio task inside the FastAPI app process (single owner), mirroring
scheduler.py. It polls periodically and runs ai_radar.run_radar at most once per
local day around the configured time (default 09:00 local), plus:
- a missed-run catch-up after restart when the last success is older than a day;
- duplicate-job prevention via an in-process flag AND a DB guard that refuses to
  start while a recent 'running' row exists (uvicorn --reload / relaunch);
- blocking network work is pushed to a worker thread so the event loop is free.

User-invoked refreshes bypass the schedule (see POST /api/ai-radar/refresh).
"""

import asyncio
import os
from datetime import timedelta, timezone

try:
    from db import AIRadarRun
    import timeutil
    import ai_radar
except ImportError:  # pragma: no cover - package import path
    from .db import AIRadarRun
    from . import timeutil
    from . import ai_radar

POLL_SECONDS = int(os.getenv("VEGA_RADAR_POLL_SECONDS", "300"))
STALE_RUN_MINUTES = int(os.getenv("VEGA_RADAR_STALE_MIN", "120"))


def scheduled_hour_minute():
    h = (os.getenv("VEGA_RADAR_HOUR") or "").strip()
    m = (os.getenv("VEGA_RADAR_MINUTE") or "").strip()
    hour = int(h) if h.isdigit() and 0 <= int(h) <= 23 else 9
    minute = int(m) if m.isdigit() and 0 <= int(m) <= 59 else 0
    return hour, minute


def _has_recent_running(db, now_utc):
    cutoff = now_utc - timedelta(minutes=STALE_RUN_MINUTES)
    return (db.query(AIRadarRun)
            .filter(AIRadarRun.status == "running", AIRadarRun.started_utc >= cutoff)
            .first() is not None)


def due_for_scheduled_run(db, clock):
    """True when the daily job should run now (schedule reached, or a missed
    check is owed). Refuses while a recent run is still in flight."""
    now_utc = clock.now_utc()
    if _has_recent_running(db, now_utc):
        return False

    now_local = timeutil.local_now(clock)
    hour, minute = scheduled_hour_minute()
    try:
        sched_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return False
    sched_utc = timeutil.naive_utc(sched_local.astimezone(timezone.utc))

    last_success = (db.query(AIRadarRun)
                    .filter(AIRadarRun.status.in_(["success", "partial"]))
                    .order_by(AIRadarRun.id.desc()).first())
    last_finished = last_success.finished_utc if last_success else None

    # Reached today's scheduled time and no success since it.
    if now_utc >= sched_utc and (last_finished is None or last_finished < sched_utc):
        return True
    # Never succeeded -> run once (first launch / restart catch-up).
    if last_finished is None:
        return True
    # Last success older than a day -> owed a missed check.
    return (now_utc - last_finished) >= timedelta(hours=24)


class RadarScheduler:
    def __init__(self, session_factory, clock=None, poll_seconds=POLL_SECONDS):
        self._session_factory = session_factory
        self._clock = clock or timeutil.SystemClock()
        self._poll_seconds = poll_seconds
        self._running = False

    async def run(self):
        while True:
            try:
                await self._maybe_run()
            except Exception as e:  # keep the loop alive; log observably
                print(f"[RADAR] scheduler error: {e}", flush=True)
            await asyncio.sleep(self._poll_seconds)

    async def _maybe_run(self):
        if self._running:
            return
        db = self._session_factory()
        try:
            should = due_for_scheduled_run(db, self._clock)
        except Exception:
            should = False
        finally:
            db.close()
        if not should:
            return
        self._running = True
        try:
            await asyncio.to_thread(self._run_sync)
        finally:
            self._running = False

    def _run_sync(self):
        db = self._session_factory()
        try:
            ai_radar.run_radar(db, self._clock, trigger="scheduled")
        finally:
            db.close()

    def run_now_sync(self, trigger="manual"):
        """Synchronous run for the user-invoked refresh endpoint (called from a
        FastAPI threadpool worker)."""
        db = self._session_factory()
        try:
            return ai_radar.run_radar(db, self._clock, trigger=trigger)
        finally:
            db.close()
