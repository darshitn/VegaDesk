"""Explicit, non-destructive schema migrations.

SQLAlchemy ``create_all()`` creates missing tables but never alters existing
ones, so the tasks-table extension needs real ALTER steps. Rules:

- Every migration step is idempotent (checks state before acting).
- Before the first migration that ALTERs an existing user database, the DB file
  is copied to ``<db>.bak-<timestamp>``. Rollback = stop the backend and copy
  the backup over the DB file. Backups are never committed (see .gitignore).
- The current schema version is tracked in a ``schema_version`` table.
"""

import os
import shutil
import time

from sqlalchemy import inspect, text

CURRENT_SCHEMA_VERSION = 5

# column name -> SQLite column declaration for tasks-table extension
_TASK_COLUMNS_V1 = [
    ("deadline_utc", "DATETIME"),
    ("subject", "VARCHAR(200)"),
    ("source", "VARCHAR(50) NOT NULL DEFAULT 'ui'"),
    ("created_at", "DATETIME"),
    ("updated_at", "DATETIME"),
]

# P2: link a task to a registered workspace. Nullable -> existing rows untouched.
_TASK_COLUMNS_V2 = [
    ("workspace_id", "INTEGER"),
]


def _get_version(conn) -> int:
    row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
    return int(row or 0)


def _backup_db_file(db_path: str) -> str | None:
    """Copy the live DB file aside before altering it. Returns backup path."""
    if not db_path or not os.path.exists(db_path):
        return None
    backup = f"{db_path}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(db_path, backup)
    return backup


def _sync_indexes(conn, base, table_name: str, info: dict) -> None:
    """Create indexes the model declares but an already-existing table lacks.

    Names come from the Table object, so the upgraded DB converges on exactly
    what create_all() would have produced. Live PRAGMA is used instead of the
    inspector, whose reflection cache is stale for columns ALTERed in this same
    transaction.
    """
    table = base.metadata.tables.get(table_name)
    if table is None:
        return
    if not conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall():
        return
    have = {r[1] for r in conn.execute(text(f"PRAGMA index_list({table_name})"))}
    for idx in table.indexes:
        cols = [c.name for c in idx.expressions]
        if idx.name in have or not cols:
            continue
        conn.execute(text(f"CREATE INDEX {idx.name} ON {table_name} ({', '.join(cols)})"))
        info["steps"].append(f"{table_name}.create_index:{idx.name}")


def run_migrations(engine, base, db_path: str | None = None) -> dict:
    """Bring the database up to CURRENT_SCHEMA_VERSION. Safe to call on every
    startup and on brand-new (empty) databases. ``base`` is the SQLAlchemy
    declarative Base from db.py. Returns an info dict."""
    info = {"version_before": 0, "version_after": 0, "backup_path": None, "steps": []}

    inspector = inspect(engine)
    has_tasks = "tasks" in inspector.get_table_names()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"))
        version = _get_version(conn)
        info["version_before"] = version

        if version < 1:
            # Back up the real user DB before any ALTER touches it.
            if has_tasks and db_path is None:
                db_path = str(engine.url.database or "")
            if has_tasks:
                info["backup_path"] = _backup_db_file(db_path)

            task_cols = {c["name"] for c in inspect(engine).get_columns("tasks")} if has_tasks else set()
            for col, decl in _TASK_COLUMNS_V1:
                if has_tasks and col not in task_cols:
                    conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {decl}"))
                    info["steps"].append(f"tasks.add_column:{col}")

            # Backfill timestamps for pre-existing rows so old tasks stay usable.
            if has_tasks:
                conn.execute(text(
                    "UPDATE tasks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
                ))
                info["steps"].append("tasks.backfill_created_at")

            conn.execute(text("INSERT INTO schema_version (version) VALUES (1)"))
            info["steps"].append("schema_version:=1")

        if version < 2:
            # P2: additive task<->workspace link. Back up before the ALTER, same
            # safety rule as v1. New workspaces/session_notes tables themselves
            # are created below by create_all (no existing row is modified).
            if has_tasks:
                _p = db_path or str(engine.url.database or "")
                info["backup_path"] = info["backup_path"] or _backup_db_file(_p)
                task_cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
                for col, decl in _TASK_COLUMNS_V2:
                    if col not in task_cols:
                        conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {decl}"))
                        info["steps"].append(f"tasks.add_column:{col}")
            conn.execute(text("INSERT INTO schema_version (version) VALUES (2)"))
            info["steps"].append("schema_version:=2")

        if version < 3:
            # Stage 3: `coursework` is a brand-new table created by create_all
            # below. No existing table is altered, so no backup is required.
            conn.execute(text("INSERT INTO schema_version (version) VALUES (3)"))
            info["steps"].append("schema_version:=3")

        if version < 4:
            # `create_all()` skips tables that already exist, so `tasks` kept
            # whatever index set it was created with: columns added later by ALTER
            # never got their declared index, and neither did any index declared
            # after the table first shipped. Fresh installs (including every test
            # DB) had them; the user's real jarvis.db did not. Verified 2026-09-22.
            _sync_indexes(conn, base, "tasks", info)
            conn.execute(text("INSERT INTO schema_version (version) VALUES (4)"))
            info["steps"].append("schema_version:=4")

        if version < 5:
            # Bind launch receipts to their requested target. Existing receipts
            # remain readable but cannot authorize target-specific replay.
            if "action_receipts" in inspect(engine).get_table_names():
                columns = {c["name"] for c in inspect(engine).get_columns("action_receipts")}
                if "target_key" not in columns:
                    _p = db_path or str(engine.url.database or "")
                    info["backup_path"] = info["backup_path"] or _backup_db_file(_p)
                    conn.execute(text("ALTER TABLE action_receipts ADD COLUMN target_key VARCHAR(64)"))
                    info["steps"].append("action_receipts.add_column:target_key")
            conn.execute(text("INSERT INTO schema_version (version) VALUES (5)"))
            info["steps"].append("schema_version:=5")

        info["version_after"] = _get_version(conn)

    # Create any brand-new tables (timers, reminders, focus_sessions,
    # scheduled_alerts, action_receipts, workspaces, session_notes, coursework,
    # schema_version). Never alters existing tables.
    base.metadata.create_all(bind=engine)
    return info
