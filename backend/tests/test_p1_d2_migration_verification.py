"""Comprehensive verification for P1-D2: SQLite migration and backup reliability.

Covers:
1. Supported historical starting point fixtures:
   - Legacy unversioned (v0)
   - v1 (M1 core columns, pre-workspace)
   - v2 (P2 workspace link, pre-coursework)
   - v3 (pre-index sync)
   - v4 (pre-target_key)
   - Current v5 (idempotent no-op)
   - Fresh empty database startup
2. WAL consistency: committed rows in WAL with open connection backed up without data loss.
3. Backup collision avoidance in the same second.
4. Backup failure fails before schema or data mutation.
5. Corrupt database file rejected cleanly.
6. Unsupported newer version (e.g. v6) fails closed.
7. Malformed schema_version table rejected cleanly.
8. Unexplained inconsistent schemas (version ahead of actual columns) rejected cleanly.
9. Known additive partial upgrades resume safely.
10. Mid-migration failure rolls back cleanly and preserves the pre-migration backup intact.
11. Unique indexes are synced as UNIQUE indexes.
"""

import os
import sqlite3
import pytest
from sqlalchemy import create_engine, inspect, text

from db import Base
import migrations
from migrations import MigrationError


# ── Helper builders for synthetic historical fixtures ──────────────────────

def _build_v0_legacy(db_file: str):
    """Build pre-version legacy database: tasks(id, text, completed) + notes(id, text)."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO tasks (id, text, completed) VALUES (101, 'legacy task', 0)")
        conn.execute("INSERT INTO notes (id, text) VALUES (201, 'legacy note')")
        conn.commit()
    finally:
        conn.close()


def _build_v1_fixture(db_file: str):
    """Build v1 database: tasks with v1 columns, notes, action_receipts (without target_key), schema_version=1."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (1)")
        conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                text VARCHAR(500) NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT 0,
                deadline_utc DATETIME,
                subject VARCHAR(200),
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_at DATETIME,
                updated_at DATETIME
            )
        """)
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL DEFAULT '')")
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO tasks (id, text, completed, created_at) VALUES (102, 'v1 task', 0, '2026-09-20 10:00:00')")
        conn.execute("INSERT INTO action_receipts (id, idempotency_key, action, success, message) VALUES (1, 'k1', 'open_app', 1, 'ok')")
        conn.commit()
    finally:
        conn.close()


def _build_v2_fixture(db_file: str):
    """Build v2 database: tasks has workspace_id, workspaces and session_notes exist, schema_version=2."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                text VARCHAR(500) NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT 0,
                deadline_utc DATETIME,
                subject VARCHAR(200),
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_at DATETIME,
                updated_at DATETIME,
                workspace_id INTEGER
            )
        """)
        conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, goal TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE session_notes (id INTEGER PRIMARY KEY, outcome TEXT NOT NULL, blocker TEXT NOT NULL, next_action TEXT NOT NULL)")
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO tasks (id, text, completed, workspace_id) VALUES (103, 'v2 task', 0, 1)")
        conn.execute("INSERT INTO workspaces (id, name, goal) VALUES (1, 'Thesis', 'Graduate')")
        conn.commit()
    finally:
        conn.close()


def _build_v3_fixture(db_file: str):
    """Build v3 database: coursework table exists, schema_version=3."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (3)")
        conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                text VARCHAR(500) NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT 0,
                deadline_utc DATETIME,
                subject VARCHAR(200),
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_at DATETIME,
                updated_at DATETIME,
                workspace_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE coursework (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                kind VARCHAR(30) NOT NULL DEFAULT 'assignment',
                title VARCHAR(300) NOT NULL,
                due_utc DATETIME,
                effort_minutes INTEGER,
                completed BOOLEAN NOT NULL DEFAULT 0,
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO coursework (id, title) VALUES (1, 'CS101 Problem Set')")
        conn.commit()
    finally:
        conn.close()


def _build_v4_fixture(db_file: str):
    """Build v4 database: indexes on tasks synced, schema_version=4, action_receipts lacks target_key."""
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                text VARCHAR(500) NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT 0,
                deadline_utc DATETIME,
                subject VARCHAR(200),
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_at DATETIME,
                updated_at DATETIME,
                workspace_id INTEGER
            )
        """)
        conn.execute("CREATE INDEX ix_tasks_deadline_utc ON tasks (deadline_utc)")
        conn.execute("CREATE INDEX ix_tasks_workspace_id ON tasks (workspace_id)")
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO action_receipts (id, idempotency_key, action, success, message) VALUES (10, 'key4', 'open_url', 1, 'opened')")
        conn.commit()
    finally:
        conn.close()


# ── Test Cases ─────────────────────────────────────────────────────────────

def test_fixture_legacy_unversioned_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "legacy.db")
    _build_v0_legacy(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 0
    assert info["version_after"] == 5
    assert info["backup_path"] is not None
    assert os.path.exists(info["backup_path"])

    # Backup has original schema only
    bak_conn = sqlite3.connect(info["backup_path"])
    try:
        cols = {r[1] for r in bak_conn.execute("PRAGMA table_info(tasks)")}
        assert "deadline_utc" not in cols
        assert "workspace_id" not in cols
        # Sentinel row exists in backup
        row = bak_conn.execute("SELECT text FROM tasks WHERE id=101").fetchone()
        assert row[0] == "legacy task"
    finally:
        bak_conn.close()

    # Upgraded DB has all columns, tables, and sentinel row
    with engine.begin() as conn:
        row = conn.execute(text("SELECT text, completed, created_at, workspace_id FROM tasks WHERE id=101")).fetchone()
        assert row[0] == "legacy task"
        assert row[2] is not None  # backfilled created_at

        note = conn.execute(text("SELECT text FROM notes WHERE id=201")).fetchone()
        assert note[0] == "legacy note"

    tables = set(inspect(engine).get_table_names())
    assert {"tasks", "notes", "timers", "reminders", "focus_sessions", "scheduled_alerts",
            "action_receipts", "workspaces", "session_notes", "coursework", "schema_version"} <= tables

    task_cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert {"deadline_utc", "subject", "source", "created_at", "updated_at", "workspace_id"} <= task_cols

    receipt_cols = {c["name"] for c in inspect(engine).get_columns("action_receipts")}
    assert "target_key" in receipt_cols

    engine.dispose()


def test_fixture_v1_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v1.db")
    _build_v1_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 1
    assert info["version_after"] == 5
    assert info["backup_path"] is not None

    with engine.begin() as conn:
        t = conn.execute(text("SELECT text FROM tasks WHERE id=102")).fetchone()
        assert t[0] == "v1 task"
        r = conn.execute(text("SELECT message, target_key FROM action_receipts WHERE id=1")).fetchone()
        assert r[0] == "ok"
        assert r[1] is None

    tables = set(inspect(engine).get_table_names())
    assert {"workspaces", "session_notes", "coursework"} <= tables
    engine.dispose()


def test_fixture_v2_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v2.db")
    _build_v2_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 2
    assert info["version_after"] == 5

    with engine.begin() as conn:
        w = conn.execute(text("SELECT name, goal FROM workspaces WHERE id=1")).fetchone()
        assert w == ("Thesis", "Graduate")
        t = conn.execute(text("SELECT workspace_id FROM tasks WHERE id=103")).fetchone()
        assert t[0] == 1

    tables = set(inspect(engine).get_table_names())
    assert "coursework" in tables
    engine.dispose()


def test_fixture_v3_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v3.db")
    _build_v3_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 3
    assert info["version_after"] == 5

    with engine.begin() as conn:
        c = conn.execute(text("SELECT title FROM coursework WHERE id=1")).fetchone()
        assert c[0] == "CS101 Problem Set"

    task_indexes = {i["name"] for i in inspect(engine).get_indexes("tasks")}
    assert "ix_tasks_deadline_utc" in task_indexes
    engine.dispose()


def test_fixture_v4_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v4.db")
    _build_v4_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 4
    assert info["version_after"] == 5
    assert "action_receipts.add_column:target_key" in info["steps"]

    with engine.begin() as conn:
        r = conn.execute(text("SELECT message, target_key FROM action_receipts WHERE id=10")).fetchone()
        assert r == ("opened", None)
    engine.dispose()


def test_fixture_v5_rerun_is_noop(tmp_path):
    db_file = str(tmp_path / "v5.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version VALUES (5)"))

    info = migrations.run_migrations(engine, Base, db_file)
    assert info["version_before"] == 5
    assert info["version_after"] == 5
    assert info["steps"] == []
    assert info["backup_path"] is None
    engine.dispose()


def test_fresh_empty_database_startup(tmp_path):
    db_file = str(tmp_path / "fresh.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 0
    assert info["version_after"] == 5
    assert info["backup_path"] is None  # no existing data to back up

    tables = set(inspect(engine).get_table_names())
    assert {"tasks", "notes", "timers", "reminders", "focus_sessions", "scheduled_alerts",
            "action_receipts", "workspaces", "session_notes", "coursework", "schema_version"} <= tables
    engine.dispose()


def test_wal_fixture_with_uncheckpointed_data_and_open_connection_backup(tmp_path):
    """WAL mode: committed rows present only in WAL with an open connection must be captured in backup."""
    db_file = str(tmp_path / "wal_test.db")
    # Set up DB in WAL mode with open connection
    open_conn = sqlite3.connect(db_file)
    open_conn.execute("PRAGMA journal_mode=WAL")
    open_conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
    open_conn.execute("INSERT INTO tasks (id, text, completed) VALUES (999, 'uncheckpointed wal task', 0)")
    open_conn.commit()

    # The WAL file must exist
    wal_file = f"{db_file}-wal"
    assert os.path.exists(wal_file) and os.path.getsize(wal_file) > 0

    # Run migration while open_conn remains open
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["backup_path"] is not None
    assert os.path.exists(info["backup_path"])

    # Verify backup is a valid standalone SQLite database containing the WAL rows
    bak_conn = sqlite3.connect(info["backup_path"])
    try:
        check = bak_conn.execute("PRAGMA integrity_check").fetchall()
        assert check == [("ok",)]
        row = bak_conn.execute("SELECT text FROM tasks WHERE id=999").fetchone()
        assert row is not None
        assert row[0] == "uncheckpointed wal task"
    finally:
        bak_conn.close()

    open_conn.close()
    engine.dispose()


def test_backup_name_collision_avoidance_in_same_second(tmp_path, monkeypatch):
    db_file = str(tmp_path / "collision.db")
    _build_v0_legacy(db_file)

    # Pin time.strftime to a constant value to simulate two backups in the same second
    fixed_time = "20261001120000"
    monkeypatch.setattr(migrations.time, "strftime", lambda fmt: fixed_time)

    # Pre-create the first expected backup file
    first_bak = f"{db_file}.bak-{fixed_time}"
    with open(first_bak, "w") as f:
        f.write("first backup contents")

    # Run migration
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["backup_path"] == f"{first_bak}_1"
    assert os.path.exists(info["backup_path"])
    assert os.path.exists(first_bak)

    # Verify first backup was NOT overwritten
    with open(first_bak, "r") as f:
        assert f.read() == "first backup contents"

    engine.dispose()


def test_backup_failure_aborts_without_mutation(tmp_path, monkeypatch):
    db_file = str(tmp_path / "backup_fail.db")
    _build_v0_legacy(db_file)

    def _failing_backup(path):
        raise MigrationError("Simulated backup failure: disk full or read-only volume")

    monkeypatch.setattr(migrations, "_backup_db_file", _failing_backup)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Simulated backup failure"):
        migrations.run_migrations(engine, Base, db_file)

    # Schema must remain unmodified
    raw_conn = sqlite3.connect(db_file)
    try:
        cols = {r[1] for r in raw_conn.execute("PRAGMA table_info(tasks)")}
        assert "deadline_utc" not in cols
        tables = [r[0] for r in raw_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert "schema_version" not in tables
    finally:
        raw_conn.close()
    engine.dispose()


def test_corrupt_database_file_rejected_without_mutation(tmp_path):
    db_file = str(tmp_path / "corrupt.db")
    with open(db_file, "wb") as f:
        f.write(b"NOT A REAL SQLITE DATABASE HEADER GIBBERISH")

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Cannot read database file|integrity check failed"):
        migrations.run_migrations(engine, Base, db_file)

    # File should not have been mutated into a new sqlite database
    with open(db_file, "rb") as f:
        assert f.read() == b"NOT A REAL SQLITE DATABASE HEADER GIBBERISH"
    engine.dispose()


def test_unsupported_newer_version_fails_closed(tmp_path):
    db_file = str(tmp_path / "v6.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (6)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="newer than supported version"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


def test_malformed_schema_version_table_fails_closed(tmp_path):
    # Case A: table exists without version column
    db_file = str(tmp_path / "bad_table.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (wrong_col TEXT)")
        conn.execute("INSERT INTO schema_version VALUES ('invalid')")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="missing 'version' column"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()

    # Case B: table has non-integer version
    db_file2 = str(tmp_path / "bad_val.db")
    conn2 = sqlite3.connect(db_file2)
    try:
        conn2.execute("CREATE TABLE schema_version (version TEXT)")
        conn2.execute("INSERT INTO schema_version VALUES ('two')")
        conn2.commit()
    finally:
        conn2.close()

    engine2 = create_engine(f"sqlite:///{db_file2}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="non-integer version value"):
        migrations.run_migrations(engine2, Base, db_file2)
    engine2.dispose()


def test_version_stamp_ahead_of_columns_fails_closed(tmp_path):
    """If version claims >= 1 or >= 2, but required columns are missing, refuse mutation."""
    db_file = str(tmp_path / "ahead.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        # tasks missing workspace_id and v1 columns despite version=2
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Inconsistent database schema: version stamp is 2"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


def test_known_additive_partial_upgrade_resumes_safely(tmp_path):
    """A database where an earlier run added deadline_utc but crashed before stamping version 1."""
    db_file = str(tmp_path / "partial.db")
    conn = sqlite3.connect(db_file)
    try:
        # tasks has deadline_utc already, but unversioned
        conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                text VARCHAR(500) NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT 0,
                deadline_utc DATETIME
            )
        """)
        conn.execute("INSERT INTO tasks (id, text, completed) VALUES (50, 'partial task', 0)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_after"] == 5
    # deadline_utc was skipped since it was already present
    assert not any("tasks.add_column:deadline_utc" in s for s in info["steps"])
    # other columns were added
    assert any("tasks.add_column:subject" in s for s in info["steps"])
    assert any("tasks.add_column:workspace_id" in s for s in info["steps"])

    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert {"deadline_utc", "subject", "source", "created_at", "updated_at", "workspace_id"} <= cols
    engine.dispose()


def test_mid_migration_failure_rolls_back_and_preserves_backup(tmp_path, monkeypatch):
    """An exception thrown during migration rolls back the transaction, leaving backup intact."""
    db_file = str(tmp_path / "interrupted.db")
    _build_v0_legacy(db_file)

    def _broken_final_validation(conn, base):
        raise MigrationError("Simulated mid-migration failure: disk error or validation failure")

    monkeypatch.setattr(migrations, "_validate_final_schema", _broken_final_validation)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Simulated mid-migration failure"):
        migrations.run_migrations(engine, Base, db_file)

    # Verify backup exists
    backups = [f for f in os.listdir(tmp_path) if f.startswith("interrupted.db.bak-")]
    assert len(backups) == 1
    bak_path = str(tmp_path / backups[0])

    # Check that backup is readable and has original schema
    bak_conn = sqlite3.connect(bak_path)
    try:
        assert bak_conn.execute("SELECT text FROM tasks WHERE id=101").fetchone()[0] == "legacy task"
    finally:
        bak_conn.close()

    # Check that schema_version table in db_file was rolled back (either doesn't exist or is version 0)
    raw_conn = sqlite3.connect(db_file)
    try:
        tables = [r[0] for r in raw_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "schema_version" in tables:
            row = raw_conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            v = row[0] if row else None
            assert v is None or v == 0
    finally:
        raw_conn.close()

    # Now un-monkeypatch and rerun: should complete successfully
    monkeypatch.undo()
    info2 = migrations.run_migrations(engine, Base, db_file)
    assert info2["version_after"] == 5
    engine.dispose()


def test_index_uniqueness_synced(tmp_path):
    """Verify that unique constraints are created as UNIQUE indexes."""
    db_file = str(tmp_path / "uniq.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    migrations.run_migrations(engine, Base, db_file)

    # Check index list on action_receipts
    raw_conn = sqlite3.connect(db_file)
    try:
        indexes = raw_conn.execute("PRAGMA index_list(action_receipts)").fetchall()
        # Look for unique index
        unique_indexes = [idx for idx in indexes if idx[2] == 1]
        assert len(unique_indexes) > 0, f"Expected unique index on action_receipts, found: {indexes}"
    finally:
        raw_conn.close()
    engine.dispose()
