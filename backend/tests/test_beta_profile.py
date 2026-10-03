"""Focused tests for VEGA Beta Acceptance Profile isolation and deterministic safety."""

import os
import sys
import importlib
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.dirname(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


@pytest.fixture
def beta_test_db(tmp_path):
    saved_env = dict(os.environ)
    beta_db = str(tmp_path / "jarvis-beta.db")

    os.environ["JARVIS_DB_PATH"] = beta_db
    os.environ["VEGA_PROFILE"] = "beta"
    os.environ["VEGA_PROFILE_ROOT"] = str(tmp_path)
    os.environ["VEGA_RUN_ID"] = "test-beta-run-42"
    os.environ["VEGA_PORT"] = "8005"

    import db
    importlib.reload(db)
    import migrations
    migrations.run_migrations(db.engine, db.Base, beta_db)
    db.Base.metadata.create_all(bind=db.engine)

    import main
    importlib.reload(main)

    yield beta_db

    db.engine.dispose()
    os.environ.clear()
    os.environ.update(saved_env)

    importlib.reload(db)
    importlib.reload(main)


def test_seed_beta_safety_refusal():
    """Verify that seed_beta.py refuses to run if pointed at a non-beta path without VEGA_PROFILE=beta."""
    from seed_beta import verify_beta_target

    old_profile = os.environ.pop("VEGA_PROFILE", None)
    try:
        with pytest.raises(SystemExit) as exc_info:
            verify_beta_target(r"C:\fake\path\jarvis.db")
        assert exc_info.value.code == 1
    finally:
        if old_profile:
            os.environ["VEGA_PROFILE"] = old_profile


def test_inherited_personal_db_override_rejected_before_writes(tmp_path):
    """Inherited personal jarvis.db with VEGA_PROFILE=beta must be rejected before writes; sentinel unchanged."""
    sentinel_db = tmp_path / "jarvis.db"
    sentinel_content = b"SENTINEL_USER_DATA_DO_NOT_CORRUPT"
    sentinel_db.write_bytes(sentinel_content)

    saved_env = dict(os.environ)
    try:
        os.environ["VEGA_PROFILE"] = "beta"
        os.environ["JARVIS_DB_PATH"] = str(sentinel_db)

        from beta_target import resolve_beta_db_path, validate_beta_db_path

        # 1. Direct validation raises ValueError
        with pytest.raises(ValueError, match="strictly named 'jarvis-beta.db'"):
            validate_beta_db_path(str(sentinel_db))

        # 2. Path resolver under VEGA_PROFILE=beta raises ValueError
        with pytest.raises(ValueError, match="strictly named 'jarvis-beta.db'"):
            resolve_beta_db_path()

        # 3. Sentinel file content remains byte-for-byte identical (no writes occurred)
        assert sentinel_db.read_bytes() == sentinel_content

    finally:
        os.environ.clear()
        os.environ.update(saved_env)


def test_reject_substring_only_beta_and_escaped_roots(tmp_path):
    """Verify rejection of substring-only filenames ('not-really-beta.db') and escaped paths."""
    saved_env = dict(os.environ)
    try:
        os.environ["VEGA_PROFILE"] = "beta"
        from beta_target import validate_beta_db_path

        # Substring-only pseudo-beta names
        for bad_name in ["beta.db", "fake_beta.db", "my_beta.sqlite", "beta-jarvis.db", "jarvis.db"]:
            with pytest.raises(ValueError, match="strictly named 'jarvis-beta.db'"):
                validate_beta_db_path(str(tmp_path / bad_name), custom_root=str(tmp_path))

        # Escaped root via directory traversal
        outside_path = str(tmp_path / "subdir" / ".." / ".." / "jarvis-beta.db")
        with pytest.raises(ValueError, match="escaped the designated root"):
            validate_beta_db_path(outside_path, custom_root=str(tmp_path / "subdir"))

    finally:
        os.environ.clear()
        os.environ.update(saved_env)


def test_seed_preserves_user_edits_and_alerts_on_reseed(beta_test_db):
    """Reseeding must preserve user-created rows, active timers/reminders/alerts, and user modifications."""
    import seed_beta
    import db
    from db import SessionLocal, Task, Workspace, Coursework, SessionNote, ScheduledAlert, Timer, Reminder
    from datetime import datetime, timezone, timedelta

    # 1. Initial seed
    res1 = seed_beta.seed_database(beta_test_db)
    assert res1["status"] == "success"

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 2. User edits existing data and adds new user-created rows
    with SessionLocal() as s:
        # Edit a seeded task: mark complete
        seeded_task = s.query(Task).filter_by(text="Implement SSA live range analysis pass").first()
        assert seeded_task is not None
        seeded_task.completed = True

        # Add user-created task
        user_task = Task(text="Personal research task", completed=False, source="ui")
        s.add(user_task)

        # Add user-created active timer, reminder, and scheduled alert
        alert = ScheduledAlert(
            kind="reminder_due",
            entity_type="reminder",
            entity_id=1,
            due_utc=now + timedelta(minutes=30),
            status="pending",
            message="Acceptance test alert",
        )
        s.add(alert)

        timer = Timer(
            label="Sprint timer",
            duration_seconds=1500,
            start_utc=now,
            end_utc=now + timedelta(minutes=25),
            status="active",
        )
        s.add(timer)

        reminder = Reminder(
            text="Submit evaluation form",
            due_utc=now + timedelta(hours=2),
            status="pending",
        )
        s.add(reminder)

        s.commit()

    # 3. Reseed database
    res2 = seed_beta.seed_database(beta_test_db)
    assert res2["status"] == "success"

    # 4. Verify user edits and user rows all survived!
    with SessionLocal() as s:
        # User edit preserved
        edited_task = s.query(Task).filter_by(text="Implement SSA live range analysis pass").first()
        assert edited_task.completed is True, "User edit must be preserved on reseed"

        # User-created task preserved
        user_task_check = s.query(Task).filter_by(text="Personal research task").first()
        assert user_task_check is not None, "User-created task must survive reseed"

        # Scheduled alert preserved
        alerts = s.query(ScheduledAlert).all()
        assert len(alerts) == 1, "User-created ScheduledAlert must survive reseed"
        assert alerts[0].message == "Acceptance test alert"

        # Active timer preserved
        timers = s.query(Timer).filter_by(status="active").all()
        assert len(timers) == 1, "Active Timer must survive reseed"
        assert timers[0].label == "Sprint timer"

        # Pending reminder preserved
        reminders = s.query(Reminder).filter_by(status="pending").all()
        assert len(reminders) == 1, "Pending Reminder must survive reseed"
        assert reminders[0].text == "Submit evaluation form"


def test_seed_beta_idempotence_and_content(beta_test_db):
    """Verify that seed_beta creates the synthetic entities and is completely idempotent on re-run."""
    import seed_beta

    # Run 1: initial seed
    res1 = seed_beta.seed_database(beta_test_db)
    assert res1["status"] == "success"
    assert res1["counts"]["tasks"] == 4
    assert res1["counts"]["workspaces"] == 1
    assert res1["counts"]["coursework"] == 2
    assert res1["counts"]["session_notes"] == 1
    assert res1["counts"]["notes"] == 1

    # Verify contents via DB queries
    import db
    from db import SessionLocal, Task, Workspace, Coursework, SessionNote, ScheduledAlert, Timer, Reminder

    with SessionLocal() as s:
        tasks = s.query(Task).all()
        assert len(tasks) == 4
        completed_tasks = [t for t in tasks if t.completed]
        open_tasks = [t for t in tasks if not t.completed]
        assert len(completed_tasks) == 2
        assert len(open_tasks) == 2

        # Verify workspace
        ws = s.query(Workspace).filter_by(key="compiler-optimizer").first()
        assert ws is not None
        assert "LLVM" in ws.goal
        assert ws.status == "active"
        assert ws.blocker != ""
        assert ws.next_action != ""

        # Verify coursework
        cw = s.query(Coursework).all()
        assert len(cw) == 2

        # Verify session note
        sn = s.query(SessionNote).first()
        assert sn is not None
        assert "control flow" in sn.outcome.lower()

        # Verify fresh seed starts with NO alerts
        alerts = s.query(ScheduledAlert).all()
        assert len(alerts) == 0
        timers = s.query(Timer).filter_by(status="active").all()
        assert len(timers) == 0
        reminders = s.query(Reminder).filter_by(status="pending").all()
        assert len(reminders) == 0

    # Run 2: idempotent re-seed
    res2 = seed_beta.seed_database(beta_test_db)
    assert res2["status"] == "success"

    with SessionLocal() as s:
        tasks = s.query(Task).all()
        assert len(tasks) == 4  # No duplicates!
        cw = s.query(Coursework).all()
        assert len(cw) == 2
        ws_count = s.query(Workspace).count()
        assert ws_count == 1


def test_beta_health_endpoint_reports_profile(beta_test_db):
    """Verify that /health reports non-secret profile identity and deterministic mode in beta."""
    from main import app
    client = TestClient(app)

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["profile"] == "beta"
    assert data["run_id"] == "test-beta-run-42"
    assert data["deterministic_only"] is True
    assert data["database"]["status"] == "ready"


def test_beta_chat_refuses_cloud_inference(beta_test_db):
    """Verify that in beta mode, chat requests do not route to cloud models even if requested."""
    from main import app
    client = TestClient(app)

    # Freeform chat that would normally hit cloud provider
    payload = {
        "message": "Write a 500 word essay on quantum computing algorithms.",
        "history": [],
        "provider": "gemini",
    }
    res = client.post("/chat", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data.get("executionMode") in ("none", "deterministic")
    assert "deterministic" in data.get("response", "").lower() or "offline" in data.get("response", "").lower()


def test_no_dotenv_loading_in_beta(beta_test_db):
    """Verify that when VEGA_PROFILE=beta, inherited cloud keys are scrubbed and LLM_PROVIDER is none."""
    os.environ["GEMINI_API_KEY"] = "inherited_fake_secret_key"
    os.environ["VEGA_PROFILE"] = "beta"

    import main
    importlib.reload(main)

    assert "GEMINI_API_KEY" not in os.environ
    assert main.LLM_PROVIDER == "none"


def test_seed_beta_preserves_user_edits_without_duplication(beta_test_db):
    """Verify that editing a seeded task title does not recreate the original task on reseed."""
    import seed_beta
    import db
    from db import SessionLocal, Task, Coursework

    # 1. Initial seed
    seed_beta.seed_database(beta_test_db)

    with SessionLocal() as s:
        tasks = s.query(Task).all()
        assert len(tasks) == 4
        # User edits task 0 title and marks it completed
        t0 = s.query(Task).filter_by(source="seed:task:0").first()
        assert t0 is not None
        t0.text = "Custom User Renamed Task 0"
        t0.completed = True

        # User edits coursework 0 title
        cw0 = s.query(Coursework).filter_by(source="seed:coursework:0").first()
        assert cw0 is not None
        cw0.title = "Custom Renamed Assignment 2"
        s.commit()

    # 2. Re-seed the database
    seed_beta.seed_database(beta_test_db)

    with SessionLocal() as s:
        tasks = s.query(Task).all()
        assert len(tasks) == 4, "Task count must remain 4 (no duplicate created from edited title)"
        t0 = s.query(Task).filter_by(source="seed:task:0").first()
        assert t0.text == "Custom User Renamed Task 0", "User-edited text must be preserved"
        assert t0.completed is True, "User-edited completion status must be preserved"

        cw0 = s.query(Coursework).filter_by(source="seed:coursework:0").first()
        assert cw0.title == "Custom Renamed Assignment 2", "User-edited coursework title must be preserved"
        assert s.query(Coursework).count() == 2


def test_seed_beta_entrypoint_subprocess(tmp_path):
    """Exercise scripts/seed_beta.js via subprocess with isolated synthetic root."""
    import subprocess
    repo_root = os.path.dirname(backend_dir)
    seed_script = os.path.join(repo_root, "scripts", "seed_beta.js")
    fake_appdata = tmp_path / "fake_appdata"
    fake_appdata.mkdir(parents=True, exist_ok=True)
    custom_root = tmp_path / "custom_beta_root"

    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "PYTHON_EXEC": sys.executable,
        "APPDATA": str(fake_appdata),
        "VEGA_PROFILE_ROOT": str(custom_root),
    }

    res = subprocess.run(
        ["node", seed_script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"seed_beta.js failed: {res.stderr}\n{res.stdout}"
    assert "VEGA BETA ACCEPTANCE PROFILE SEEDED SUCCESSFULLY" in res.stdout
    assert os.path.exists(custom_root / "db" / "jarvis-beta.db")

