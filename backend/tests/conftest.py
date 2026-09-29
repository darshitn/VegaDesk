"""Pytest setup for VEGA M1 backend tests.

All tests run against a temporary synthetic SQLite database (never the real
jarvis.db) with voice disabled. The env vars must be set before db.py is
imported, which happens at conftest import time.
"""

import os
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

_TMP_DIR = tempfile.mkdtemp(prefix="vega_pytest_")
os.environ["JARVIS_DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ["VEGA_DISABLE_VOICE"] = "1"
os.environ["VEGA_DISABLE_RADAR"] = "1"  # keep the daily AI Radar network job off in tests

import pytest  # noqa: E402


@pytest.fixture()
def db():
    """Session on the shared temp DB with all tables emptied first."""
    from db import (engine, Base, SessionLocal, Task, Note, Timer, Reminder,
                    FocusSession, ScheduledAlert, ActionReceipt, AIRadarItem,
                    AIRadarRun, Workspace, SessionNote, Coursework)
    # Ensure schema exists for db-only tests (the client fixture creates it via
    # lifespan migrations, but tests using just `db` never boot the app).
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    for model in (ActionReceipt, ScheduledAlert, FocusSession, Reminder, Timer,
                  Note, Task, AIRadarItem, AIRadarRun, SessionNote, Workspace,
                  Coursework):
        session.query(model).delete()
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def clock():
    """Injectable fake clock: Monday 2026-09-21 10:00 local (IST, UTC+05:30)."""
    from datetime import datetime
    import timeutil
    return timeutil.FakeClock(datetime(2026, 9, 21, 4, 30, 0))


@pytest.fixture()
def client():
    """FastAPI TestClient. Lifespan runs (validates scheduler startup wiring);
    voice stays disabled via VEGA_DISABLE_VOICE=1 and no /ws/alerts client
    connects, so the scheduler never delivers during HTTP-only tests."""
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as c:
        yield c


def pytest_sessionfinish(session, exitstatus):
    import shutil
    # The pooled SQLite connection to `_TMP_DIR/test.db` is still open here, and on
    # Windows an open handle makes os.remove() fail — which `ignore_errors=True` used
    # to swallow, leaking one temp directory per DB-touching test run. Closing the
    # engine first is what lets the cleanup actually delete anything.
    import db
    db.engine.dispose()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
    if os.path.isdir(_TMP_DIR):
        print(f"\n[conftest] could not remove test temp dir: {_TMP_DIR}")
