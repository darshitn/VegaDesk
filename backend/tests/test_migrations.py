"""Versioned, non-destructive migration tests against a synthetic OLD-schema DB.

We build a pre-M1 database (tasks without the new columns, plus notes), run
run_migrations(), and assert the columns are added, existing rows survive, a
backup file is written, the version is stamped, and a rerun is a no-op.
"""

import os
import sqlite3
import glob

from sqlalchemy import create_engine, inspect, text

from db import Base
import migrations


def _build_old_db(path: str):
    """Create a pre-M1 schema: tasks(id,text,completed) + notes(id,text)."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO tasks (id, text, completed) VALUES (1, 'old task', 0)")
        conn.execute("INSERT INTO notes (id, text) VALUES (1, 'old note')")
        conn.commit()
    finally:
        conn.close()


def test_migration_adds_columns_and_preserves_rows(tmp_path):
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 0
    assert info["version_after"] == 5
    assert any(s.startswith("tasks.add_column:deadline_utc") for s in info["steps"])
    for col in ("deadline_utc", "subject", "source", "created_at", "updated_at"):
        assert f"tasks.add_column:{col}" in info["steps"]
    # v2 (P2) adds the workspace link column.
    assert "tasks.add_column:workspace_id" in info["steps"]

    # New columns present.
    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert {"deadline_utc", "subject", "source", "created_at", "updated_at",
            "workspace_id"} <= cols

    # Old rows preserved.
    with engine.begin() as conn:
        row = conn.execute(text("SELECT text FROM tasks WHERE id=1")).first()
        assert row[0] == "old task"
        note = conn.execute(text("SELECT text FROM notes WHERE id=1")).first()
        assert note[0] == "old note"
        # created_at backfilled, not left NULL.
        created = conn.execute(text("SELECT created_at FROM tasks WHERE id=1")).scalar()
        assert created is not None

    # New M1 tables created.
    tables = set(inspect(engine).get_table_names())
    assert {"timers", "reminders", "focus_sessions", "scheduled_alerts",
            "action_receipts", "schema_version"} <= tables
    # New P2 tables created (additive; no existing row modified).
    assert {"workspaces", "session_notes"} <= tables
    # New Stage 3 table created (additive).
    assert "coursework" in tables

    engine.dispose()


def test_migration_writes_backup_before_alter(tmp_path):
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["backup_path"] is not None
    assert os.path.exists(info["backup_path"])
    assert info["backup_path"].startswith(db_file + ".bak-")

    # Backup holds the ORIGINAL schema (no new columns) — a real rollback point.
    bak_cols = sqlite3.connect(info["backup_path"])
    try:
        names = {r[1] for r in bak_cols.execute("PRAGMA table_info(tasks)")}
        assert "deadline_utc" not in names
        assert "text" in names
    finally:
        bak_cols.close()

    engine.dispose()


def test_migration_is_idempotent_on_rerun(tmp_path):
    db_file = str(tmp_path / "old.db")
    _build_old_db(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    first = migrations.run_migrations(engine, Base, db_file)
    assert first["version_after"] == 5

    backups_after_first = len(glob.glob(db_file + ".bak-*"))

    second = migrations.run_migrations(engine, Base, db_file)
    assert second["version_before"] == 5
    assert second["version_after"] == 5
    assert second["steps"] == []          # nothing to do
    assert second["backup_path"] is None   # no new backup on a no-op rerun
    assert len(glob.glob(db_file + ".bak-*")) == backups_after_first

    engine.dispose()


def test_migration_on_fresh_empty_db(tmp_path):
    """Brand-new install: no tasks table yet, nothing to back up or alter."""
    db_file = str(tmp_path / "fresh.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 0
    assert info["version_after"] == 5
    assert info["backup_path"] is None      # no pre-existing user data to protect
    assert not any(s.startswith("tasks.add_column") for s in info["steps"])

    tables = set(inspect(engine).get_table_names())
    assert {"tasks", "timers", "reminders", "scheduled_alerts", "action_receipts",
            "workspaces", "session_notes", "coursework"} <= tables
    engine.dispose()


def test_upgraded_db_matches_fresh_db_on_task_indexes(tmp_path):
    """An upgraded DB must not quietly differ from the one the tests run on.

    create_all() skips existing tables, so columns brought in by ALTER got no
    index on upgraded databases while fresh installs always had one — including
    the user's real jarvis.db, found by inspection on 2026-09-22.
    """
    old_file = str(tmp_path / "old.db")
    _build_old_db(old_file)
    upgraded = create_engine(f"sqlite:///{old_file}", connect_args={"check_same_thread": False})
    migrations.run_migrations(upgraded, Base, old_file)

    fresh_file = str(tmp_path / "fresh.db")
    fresh = create_engine(f"sqlite:///{fresh_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(fresh)

    got = {i["name"] for i in inspect(upgraded).get_indexes("tasks")}
    want = {i["name"] for i in inspect(fresh).get_indexes("tasks")}
    assert want - got == set(), f"upgraded DB is missing indexes a fresh DB has: {sorted(want - got)}"

    # The step is a no-op once the indexes exist, even at a lower version stamp.
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM schema_version WHERE version >= 4"))
    second = migrations.run_migrations(upgraded, Base, old_file)
    assert not any(s.startswith("tasks.create_index") for s in second["steps"])
    assert second["version_after"] == 5

    upgraded.dispose()
    fresh.dispose()


def test_v5_adds_receipt_target_key_without_losing_old_receipts(tmp_path):
    db_file = str(tmp_path / "v4.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version VALUES (4)"))
        conn.execute(text("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120) UNIQUE,
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 1,
                entity_type VARCHAR(30),
                entity_id INTEGER,
                message TEXT NOT NULL DEFAULT '',
                command_text VARCHAR(600),
                source VARCHAR(50),
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("INSERT INTO action_receipts (id, idempotency_key, action, success, message) VALUES (1, 'old-key', 'open_app', 1, 'old receipt')"))
    info = migrations.run_migrations(engine, Base, db_file)
    assert info["version_before"] == 4 and info["version_after"] == 5
    assert "action_receipts.add_column:target_key" in info["steps"]
    assert info["backup_path"] and os.path.exists(info["backup_path"])
    with engine.begin() as conn:
        row = conn.execute(text("SELECT message, target_key FROM action_receipts WHERE id=1")).one()
        assert row == ("old receipt", None)
    engine.dispose()
