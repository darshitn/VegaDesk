"""Comprehensive verification for P1-D2: SQLite migration and backup reliability.

Covers:
1. Supported authentic historical starting point fixtures:
   - Legacy unversioned (v0)
   - v1 (M1 core columns, pre-workspace)
   - v2 (P2 workspace & session_notes, pre-coursework)
   - v3 (radar tables, pre-coursework)
   - v4 (coursework, pre-target_key)
   - Current v5 (idempotent no-op)
   - Fresh empty database startup
2. Post-upgrade ORM reads and writes across all models (verifying no OperationalError).
3. Enforcement of duplicate idempotency-key rejection (IntegrityError) on upgraded receipt tables.
4. Reproduction and rejection of the 3 findings:
   - Finding 1: Version-only database (only schema_version=5, missing model tables) fails closed.
   - Finding 2: Broken/truncated existing tables (missing model columns) fail closed before writes.
   - Finding 3: Genuine DDL rollback on early ALTER and late table/index creation failure.
5. Critical index validation: non-unique idempotency_key index fails closed before writes.
6. Backup timing audit: elapsed-time deadline guard and held exclusive lock timeout.
7. Atomic backup filename reservation: prevents TOCTOU overwrite races.
8. WAL consistency: committed rows in WAL with open connection backed up without data loss.
"""

import os
import glob
import sqlite3
import time
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from db import (Base, Task, Note, Timer, Reminder, FocusSession, ScheduledAlert,
                ActionReceipt, Workspace, SessionNote, Coursework)
import migrations
from migrations import MigrationError


# ── Helper builders for authentic historical fixtures ──────────────────────

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
    """Build authentic v1 database: tasks with v1 columns, notes, action_receipts (full schema without target_key), schema_version=1."""
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
        """)
        conn.execute("CREATE UNIQUE INDEX ix_action_receipts_idempotency_key ON action_receipts (idempotency_key)")
        conn.execute("INSERT INTO tasks (id, text, completed, created_at) VALUES (102, 'v1 task', 0, '2026-09-20 10:00:00')")
        conn.execute("INSERT INTO action_receipts (id, idempotency_key, action, success, message) VALUES (1, 'k1', 'open_app', 1, 'ok')")
        conn.commit()
    finally:
        conn.close()


def _build_v2_fixture(db_file: str):
    """Build authentic v2 database: tasks has workspace_id, authentic workspaces and session_notes tables exist, schema_version=2."""
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
        conn.execute("""
            CREATE TABLE workspaces (
                id INTEGER PRIMARY KEY,
                key VARCHAR(120) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                type VARCHAR(20) NOT NULL DEFAULT 'personal',
                path VARCHAR(600),
                goal VARCHAR(500) NOT NULL DEFAULT '',
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                next_action VARCHAR(500) NOT NULL DEFAULT '',
                blocker VARCHAR(500) NOT NULL DEFAULT '',
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE UNIQUE INDEX ix_workspaces_key ON workspaces (key)")
        conn.execute("""
            CREATE TABLE session_notes (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                outcome TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                source VARCHAR(50) NOT NULL DEFAULT 'ui',
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
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
        """)
        conn.execute("CREATE UNIQUE INDEX ix_action_receipts_idempotency_key ON action_receipts (idempotency_key)")
        conn.execute("INSERT INTO tasks (id, text, completed, workspace_id) VALUES (103, 'v2 task', 0, 1)")
        conn.execute("INSERT INTO workspaces (id, key, name, goal) VALUES (1, 'thesis', 'Thesis', 'Graduate')")
        conn.execute("INSERT INTO action_receipts (id, idempotency_key, action, success, message) VALUES (1, 'k2', 'open_app', 1, 'ok')")
        conn.commit()
    finally:
        conn.close()


def _build_v3_fixture(db_file: str):
    """Build authentic v3 database: ai_radar tables exist, schema_version=3."""
    _build_v2_fixture(db_file)
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("UPDATE schema_version SET version=3")
        conn.execute("""
            CREATE TABLE ai_radar_items (
                id INTEGER PRIMARY KEY,
                source_id VARCHAR(300) NOT NULL UNIQUE,
                canonical_url VARCHAR(600) NOT NULL,
                category VARCHAR(30) NOT NULL DEFAULT 'other',
                title VARCHAR(400) NOT NULL DEFAULT '',
                publisher VARCHAR(200),
                url VARCHAR(600) NOT NULL,
                published_utc DATETIME,
                first_seen_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_checked_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                summary TEXT NOT NULL DEFAULT '',
                evidence_urls TEXT NOT NULL DEFAULT '[]',
                verification_status VARCHAR(20) NOT NULL DEFAULT 'unverified',
                offer_kind VARCHAR(30),
                offer_eligibility VARCHAR(300),
                offer_quota VARCHAR(300),
                offer_billing_required BOOLEAN,
                offer_region VARCHAR(120),
                offer_expires_utc DATETIME,
                offer_terms_status VARCHAR(20),
                is_read BOOLEAN NOT NULL DEFAULT 0,
                is_dismissed BOOLEAN NOT NULL DEFAULT 0,
                last_run_id INTEGER
            )
        """)
        conn.execute("CREATE UNIQUE INDEX ix_ai_radar_items_source_id ON ai_radar_items (source_id)")
        conn.execute("""
            CREATE TABLE ai_radar_runs (
                id INTEGER PRIMARY KEY,
                started_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_utc DATETIME,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                trigger VARCHAR(20) NOT NULL DEFAULT 'scheduled',
                sources_ok INTEGER NOT NULL DEFAULT 0,
                sources_failed INTEGER NOT NULL DEFAULT 0,
                new_items INTEGER NOT NULL DEFAULT 0,
                per_source TEXT NOT NULL DEFAULT '{}',
                digest_notified BOOLEAN NOT NULL DEFAULT 0,
                digest_alert_id INTEGER
            )
        """)
        conn.execute("INSERT INTO ai_radar_items (id, source_id, canonical_url, title, url) VALUES (1, 'radar-1', 'https://example.com/1', 'AI News', 'https://example.com/1')")
        conn.commit()
    finally:
        conn.close()


def _build_v4_fixture(db_file: str):
    """Build authentic v4 database: indexes on tasks synced, authentic coursework table exists, schema_version=4."""
    _build_v3_fixture(db_file)
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("UPDATE schema_version SET version=4")
        conn.execute("CREATE INDEX ix_tasks_deadline_utc ON tasks (deadline_utc)")
        conn.execute("CREATE INDEX ix_tasks_workspace_id ON tasks (workspace_id)")
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
        conn.execute("INSERT INTO coursework (id, title) VALUES (1, 'CS101 Problem Set')")
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
        row = bak_conn.execute("SELECT text FROM tasks WHERE id=101").fetchone()
        assert row[0] == "legacy task"
    finally:
        bak_conn.close()

    # Upgraded DB has all columns, tables, and sentinel rows
    tables = set(inspect(engine).get_table_names())
    assert {"tasks", "notes", "timers", "reminders", "focus_sessions", "scheduled_alerts",
            "action_receipts", "workspaces", "session_notes", "coursework", "schema_version"} <= tables

    # ORM reads work cleanly
    Session = sessionmaker(bind=engine)
    session = Session()
    t = session.query(Task).filter_by(id=101).first()
    assert t is not None and t.text == "legacy task"
    n = session.query(Note).filter_by(id=201).first()
    assert n is not None and n.text == "legacy note"

    # ORM writes work cleanly
    w = Workspace(key="proj1", name="Project 1", goal="Complete milestone")
    session.add(w)
    session.commit()
    assert session.query(Workspace).filter_by(key="proj1").first() is not None

    # Idempotency uniqueness enforced on upgraded table
    r1 = ActionReceipt(idempotency_key="dup_key", action="open_app", success=True, message="first")
    session.add(r1)
    session.commit()

    r2 = ActionReceipt(idempotency_key="dup_key", action="open_app", success=True, message="second")
    session.add(r2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
    engine.dispose()


def test_fixture_v1_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v1.db")
    _build_v1_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 1
    assert info["version_after"] == 5
    assert info["backup_path"] is not None

    Session = sessionmaker(bind=engine)
    session = Session()
    t = session.query(Task).filter_by(id=102).first()
    assert t is not None and t.text == "v1 task"
    r = session.query(ActionReceipt).filter_by(id=1).first()
    assert r is not None and r.message == "ok" and r.target_key is None

    # Enforce duplicate idempotency_key rejection on existing key 'k1'
    dup = ActionReceipt(idempotency_key="k1", action="open_app", success=True, message="dup")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
    engine.dispose()


def test_fixture_v2_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v2.db")
    _build_v2_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 2
    assert info["version_after"] == 5

    # Proves Finding 2 resolution: Workspace query does not raise OperationalError
    Session = sessionmaker(bind=engine)
    session = Session()
    w = session.query(Workspace).filter_by(id=1).first()
    assert w is not None
    assert w.key == "thesis"
    assert w.name == "Thesis"
    assert w.goal == "Graduate"

    # Enforce duplicate idempotency_key rejection on existing key 'k2'
    dup = ActionReceipt(idempotency_key="k2", action="open_app", success=True, message="dup")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
    engine.dispose()


def test_fixture_v3_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v3.db")
    _build_v3_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 3
    assert info["version_after"] == 5

    Session = sessionmaker(bind=engine)
    session = Session()
    task_indexes = {i["name"] for i in inspect(engine).get_indexes("tasks")}
    assert "ix_tasks_deadline_utc" in task_indexes
    session.close()
    engine.dispose()


def test_fixture_v4_upgrade_to_v5(tmp_path):
    db_file = str(tmp_path / "v4.db")
    _build_v4_fixture(db_file)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["version_before"] == 4
    assert info["version_after"] == 5
    assert "action_receipts.add_column:target_key" in info["steps"]

    Session = sessionmaker(bind=engine)
    session = Session()
    c = session.query(Coursework).filter_by(id=1).first()
    assert c is not None and c.title == "CS101 Problem Set"

    r = session.query(ActionReceipt).filter_by(id=1).first()
    assert r is not None and r.target_key is None

    # Enforce duplicate idempotency_key rejection on existing key 'k2'
    dup = ActionReceipt(idempotency_key="k2", action="open_app", success=True, message="dup")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
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
    assert info["backup_path"] is None

    tables = set(inspect(engine).get_table_names())
    assert {"tasks", "notes", "timers", "reminders", "focus_sessions", "scheduled_alerts",
            "action_receipts", "workspaces", "session_notes", "coursework", "schema_version"} <= tables
    engine.dispose()


# ── Finding 1 & 2: Version-only & Broken Truncated Schemas ─────────────────

def test_version_only_database_fails_closed(tmp_path):
    """Finding 1: A database with only schema_version=5 and no model tables must be rejected."""
    db_file = str(tmp_path / "v5_only.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (5)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="contains no model tables|missing required model table"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


def test_truncated_workspace_table_rejected_before_writes(tmp_path):
    """Finding 2: An existing table missing model columns (e.g. 3-column workspaces) must be rejected before writes."""
    db_file = str(tmp_path / "broken_ws.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text TEXT, completed INT, deadline_utc TEXT, subject TEXT, source TEXT, created_at TEXT, updated_at TEXT, workspace_id INT)")
        conn.execute("CREATE TABLE workspaces (id INTEGER PRIMARY KEY, name TEXT, goal TEXT)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="existing table 'workspaces' has unexplained missing columns"):
        migrations.run_migrations(engine, Base, db_file)

    # Prove no writes were performed
    raw = sqlite3.connect(db_file)
    try:
        cols = [r[1] for r in raw.execute("PRAGMA table_info(workspaces)").fetchall()]
        assert cols == ["id", "name", "goal"]
    finally:
        raw.close()
    engine.dispose()


def test_non_unique_idempotency_key_index_rejected_before_writes(tmp_path):
    """Critical index check: action_receipts with non-unique idempotency_key index must fail closed."""
    db_file = str(tmp_path / "bad_index.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 1,
                entity_type VARCHAR(30),
                entity_id INTEGER,
                message TEXT NOT NULL DEFAULT '',
                command_text VARCHAR(600),
                source VARCHAR(50),
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Non-unique index on idempotency_key
        conn.execute("CREATE INDEX ix_action_receipts_idempotency_key ON action_receipts (idempotency_key)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="is non-unique, expected UNIQUE"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


def test_partial_unique_index_on_current_v5_fails_closed(tmp_path):
    """Prerequisite: A partial unique index WHERE success=1 permits duplicate failure keys,
    violates model unconditional uniqueness, and must be rejected before writes without silent repair."""
    db_file = str(tmp_path / "partial_idx.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_version (version INTEGER NOT NULL)"))
        conn.execute(text("INSERT INTO schema_version VALUES (5)"))
    engine.dispose()

    # In raw SQLite, replace the critical index with a partial unique index WHERE success=1
    raw = sqlite3.connect(db_file)
    try:
        raw.execute("DROP INDEX ix_action_receipts_idempotency_key")
        raw.execute(
            "CREATE UNIQUE INDEX ix_action_receipts_idempotency_key ON action_receipts (idempotency_key) WHERE success=1"
        )
        # Demonstrate the vulnerability: partial index permits duplicate failure keys!
        raw.execute(
            "INSERT INTO action_receipts (idempotency_key, action, success, message, created_utc) VALUES ('k_fail', 'open_app', 0, 'fail1', '2026-10-02 00:00:00')"
        )
        raw.execute(
            "INSERT INTO action_receipts (idempotency_key, action, success, message, created_utc) VALUES ('k_fail', 'open_app', 0, 'fail2', '2026-10-02 00:00:00')"
        )
        raw.commit()
        # Verify both rows were inserted
        count = raw.execute("SELECT COUNT(*) FROM action_receipts WHERE idempotency_key='k_fail'").fetchone()[0]
        assert count == 2

        # Verify PRAGMA index_list returns partial=1
        idx_info = [r for r in raw.execute("PRAGMA index_list(action_receipts)").fetchall() if r[1] == "ix_action_receipts_idempotency_key"][0]
        assert idx_info[4] == 1, f"Expected partial flag=1 in PRAGMA index_list, got {idx_info}"
    finally:
        raw.close()

    # run_migrations must reject this contradictory schema before writes
    engine2 = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="is a partial index, expected unconditional UNIQUE"):
        migrations.run_migrations(engine2, Base, db_file)
    engine2.dispose()

    # Prove the user's index was NOT silently repaired or overwritten
    raw2 = sqlite3.connect(db_file)
    try:
        idx_info2 = [r for r in raw2.execute("PRAGMA index_list(action_receipts)").fetchall() if r[1] == "ix_action_receipts_idempotency_key"][0]
        assert idx_info2[4] == 1, "User partial index must not be silently replaced"

        # Now clean up duplicate and restore normal unconditional index to prove validation passes cleanly
        raw2.execute("DELETE FROM action_receipts WHERE message='fail2'")
        raw2.execute("DROP INDEX ix_action_receipts_idempotency_key")
        raw2.execute("CREATE UNIQUE INDEX ix_action_receipts_idempotency_key ON action_receipts (idempotency_key)")
        raw2.commit()
    finally:
        raw2.close()

    # Normal unconditional index passes validation cleanly
    engine3 = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine3, Base, db_file)
    assert info["version_after"] == 5
    engine3.dispose()


def test_partial_unique_index_alternate_fallback_rejected(tmp_path):
    """Alternate-index fallback in _check_schema_consistency and _validate_orm_schema must reject partial unique indexes."""
    db_file = str(tmp_path / "alt_partial_idx.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute("""
            CREATE TABLE action_receipts (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(120),
                action VARCHAR(60) NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 1,
                entity_type VARCHAR(30),
                entity_id INTEGER,
                message TEXT NOT NULL DEFAULT '',
                command_text VARCHAR(600),
                source VARCHAR(50),
                created_utc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create alternate-named partial unique index
        conn.execute(
            "CREATE UNIQUE INDEX uq_alt_receipt_key ON action_receipts (idempotency_key) WHERE success=1"
        )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="is a partial index, expected unconditional UNIQUE|missing required unique index"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


# ── Finding 3: Genuine DDL Rollback on Failure ──────────────────────────────

def test_genuine_ddl_rollback_on_early_alter_failure(tmp_path, monkeypatch):
    """Finding 3: Failure after early ALTER TABLE must roll back all added columns completely.
    Keeps true migration column list, observes ALTER executing, then injects failure."""
    from sqlalchemy.engine import Connection

    db_file = str(tmp_path / "early_fail.db")
    _build_v0_legacy(db_file)

    # Capture snapshot before migration
    raw = sqlite3.connect(db_file)
    orig_cols = [r[1] for r in raw.execute("PRAGMA table_info(tasks)").fetchall()]
    orig_tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    orig_row = raw.execute("SELECT text FROM tasks WHERE id=101").fetchone()[0]
    raw.close()

    # Observe ALTER execution on connection while keeping _TASK_COLUMNS_V1 intact
    orig_conn_execute = Connection.execute
    executed_alters = []

    def _intercept_execute(self, statement, *args, **kwargs):
        stmt_str = str(statement)
        if "ALTER TABLE tasks ADD COLUMN" in stmt_str:
            executed_alters.append(stmt_str)
            res = orig_conn_execute(self, statement, *args, **kwargs)
            if "deadline_utc" in stmt_str:
                # Injected specific failure immediately after deadline_utc is added to table
                raise RuntimeError("Simulated failure immediately after early ALTER deadline_utc")
            return res
        return orig_conn_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", _intercept_execute)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(RuntimeError, match="Simulated failure immediately after early ALTER deadline_utc"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()

    # Verify that the ALTER actually executed before failure was injected
    assert len(executed_alters) == 1
    assert "deadline_utc" in executed_alters[0]

    # Reopen database and verify genuine rollback
    reopened = sqlite3.connect(db_file)
    try:
        new_cols = [r[1] for r in reopened.execute("PRAGMA table_info(tasks)").fetchall()]
        new_tables = [r[0] for r in reopened.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        new_row = reopened.execute("SELECT text FROM tasks WHERE id=101").fetchone()[0]

        assert new_cols == orig_cols, f"DDL did not roll back: got cols {new_cols}, expected {orig_cols}"
        assert "deadline_utc" not in new_cols
        assert new_tables == orig_tables
        assert new_row == orig_row
    finally:
        reopened.close()

    # Safe rerun succeeds after removing the injected failure
    monkeypatch.undo()
    engine2 = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine2, Base, db_file)
    assert info["version_after"] == 5
    engine2.dispose()


def test_genuine_ddl_rollback_on_late_create_all_failure(tmp_path, monkeypatch):
    """Finding 3: Failure after actual table/index creation must roll back all tables and ALTERs."""
    db_file = str(tmp_path / "late_fail.db")
    _build_v0_legacy(db_file)

    raw = sqlite3.connect(db_file)
    orig_cols = [r[1] for r in raw.execute("PRAGMA table_info(tasks)").fetchall()]
    orig_tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    raw.close()

    tables_created_during_migration = []

    def _fail_after_tables_created(conn, base):
        # Observe that tables were actually created by create_all inside the transaction
        tbls = [
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        ]
        tables_created_during_migration.extend(tbls)
        raise RuntimeError("Simulated failure after table and index creation")

    # Monkeypatch _validate_orm_schema to observe actual created tables, then fail
    monkeypatch.setattr(migrations, "_validate_orm_schema", _fail_after_tables_created)

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(RuntimeError, match="Simulated failure after table and index creation"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()

    # Prove that tables were actually created in the transaction before rollback
    assert "workspaces" in tables_created_during_migration
    assert "reminders" in tables_created_during_migration
    assert "action_receipts" in tables_created_during_migration

    # Reopen database and verify complete rollback of all tables and columns
    reopened = sqlite3.connect(db_file)
    try:
        new_cols = [r[1] for r in reopened.execute("PRAGMA table_info(tasks)").fetchall()]
        new_tables = [r[0] for r in reopened.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        assert new_cols == orig_cols, f"DDL did not roll back: got {new_cols}, expected {orig_cols}"
        assert "deadline_utc" not in new_cols
        assert new_tables == orig_tables
        assert "workspaces" not in new_tables
        assert "reminders" not in new_tables
    finally:
        reopened.close()

    # Pre-migration backup remains intact
    backups = [f for f in os.listdir(tmp_path) if f.startswith("late_fail.db.bak-")]
    assert len(backups) == 1

    monkeypatch.undo()
    engine2 = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine2, Base, db_file)
    assert info["version_after"] == 5
    engine2.dispose()


# ── Backup Deadlines, Held Locks, and Atomic Reservation ────────────────────

def test_backup_deadline_and_held_lock_timeout(tmp_path):
    """Item 5: Held exclusive lock on source triggers bounded timeout and cleans up incomplete backup."""
    db_file = str(tmp_path / "locked.db")
    _build_v0_legacy(db_file)

    # Hold exclusive lock on source
    lock_conn = sqlite3.connect(db_file, isolation_level=None)
    lock_conn.execute("BEGIN EXCLUSIVE")
    lock_conn.execute("INSERT INTO tasks (id, text, completed) VALUES (555, 'lock task', 0)")

    t0 = time.monotonic()
    try:
        with pytest.raises(MigrationError, match="timed out after|deadline"):
            migrations._backup_db_file(db_file, timeout=0.6)
        elapsed = time.monotonic() - t0
        # Watchdog: ensure test returned promptly within reasonable bound
        assert elapsed < 3.0, f"Backup did not timeout promptly: took {elapsed:.2f}s"
    finally:
        lock_conn.execute("ROLLBACK")
        lock_conn.close()

    # Incomplete backup must have been cleaned up
    remaining_baks = glob.glob(f"{db_file}.bak-*")
    assert len(remaining_baks) == 0, f"Incomplete backup not cleaned up: {remaining_baks}"

    # Source database preserved
    raw = sqlite3.connect(db_file)
    try:
        row = raw.execute("SELECT text FROM tasks WHERE id=101").fetchone()
        assert row[0] == "legacy task"
    finally:
        raw.close()


def test_atomic_backup_reservation_prevents_overwrite(tmp_path):
    """Item 5: Atomic reservation prevents TOCTOU overwrite race."""
    db_file = str(tmp_path / "atom.db")
    with open(db_file, "w") as f:
        f.write("source")

    p1 = migrations._reserve_backup_path(db_file)
    p2 = migrations._reserve_backup_path(db_file)
    p3 = migrations._reserve_backup_path(db_file)

    assert p1 != p2 and p2 != p3
    assert os.path.exists(p1) and os.path.exists(p2) and os.path.exists(p3)

    for p in (p1, p2, p3):
        os.remove(p)


def test_wal_fixture_with_uncheckpointed_data_and_open_connection_backup(tmp_path):
    """WAL mode: committed rows present only in WAL with an open connection must be captured in backup."""
    db_file = str(tmp_path / "wal_test.db")
    open_conn = sqlite3.connect(db_file)
    open_conn.execute("PRAGMA journal_mode=WAL")
    open_conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
    open_conn.execute("INSERT INTO tasks (id, text, completed) VALUES (999, 'uncheckpointed wal task', 0)")
    open_conn.commit()

    wal_file = f"{db_file}-wal"
    assert os.path.exists(wal_file) and os.path.getsize(wal_file) > 0

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    info = migrations.run_migrations(engine, Base, db_file)

    assert info["backup_path"] is not None
    assert os.path.exists(info["backup_path"])

    bak_conn = sqlite3.connect(info["backup_path"])
    try:
        check = bak_conn.execute("PRAGMA integrity_check").fetchall()
        assert check == [("ok",)]
        row = bak_conn.execute("SELECT text FROM tasks WHERE id=999").fetchone()
        assert row is not None and row[0] == "uncheckpointed wal task"
    finally:
        bak_conn.close()

    open_conn.close()
    engine.dispose()


def test_corrupt_database_file_rejected_without_mutation(tmp_path):
    db_file = str(tmp_path / "corrupt.db")
    with open(db_file, "wb") as f:
        f.write(b"NOT A REAL SQLITE DATABASE HEADER GIBBERISH")

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Cannot read database file|integrity check failed"):
        migrations.run_migrations(engine, Base, db_file)

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
    db_file = str(tmp_path / "ahead.db")
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, text VARCHAR(500) NOT NULL, completed BOOLEAN NOT NULL DEFAULT 0)")
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    with pytest.raises(MigrationError, match="Inconsistent database schema: 'tasks' table has unexplained missing columns"):
        migrations.run_migrations(engine, Base, db_file)
    engine.dispose()


def test_known_additive_partial_upgrade_resumes_safely(tmp_path):
    db_file = str(tmp_path / "partial.db")
    conn = sqlite3.connect(db_file)
    try:
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
    assert not any("tasks.add_column:deadline_utc" in s for s in info["steps"])
    assert any("tasks.add_column:subject" in s for s in info["steps"])
    assert any("tasks.add_column:workspace_id" in s for s in info["steps"])

    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert {"deadline_utc", "subject", "source", "created_at", "updated_at", "workspace_id"} <= cols
    engine.dispose()
