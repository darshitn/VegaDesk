"""Explicit, non-destructive schema migrations.

SQLAlchemy ``create_all()`` creates missing tables but never alters existing
ones, so the tasks-table extension needs real ALTER steps. Rules:

- Every migration step is idempotent (checks state before acting).
- Before the first migration that ALTERs an existing user database, the DB file
  is backed up to ``<db>.bak-<timestamp>`` using SQLite's online backup API to
  guarantee consistent preservation of committed WAL pages and open connections.
  Backups are verified with PRAGMA integrity_check and never committed (see .gitignore).
- Rollback = stop the backend and copy the backup over the DB file.
- The current schema version is tracked in a ``schema_version`` table.
- Unknown or unexplained inconsistent schemas (version ahead of columns) and
  unsupported newer versions fail closed before schema mutation.
"""

import os
import sqlite3
import time

from sqlalchemy import inspect, text

CURRENT_SCHEMA_VERSION = 5


class MigrationError(Exception):
    """Raised when migration validation, backup, or schema consistency checks fail."""


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


def _read_and_validate_version(conn) -> int:
    """Read schema_version, refusing malformed tables and unsupported newer versions."""
    tables = [
        r[0]
        for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        ).fetchall()
    ]
    if not tables:
        return 0

    cols = conn.execute(text("PRAGMA table_info(schema_version)")).fetchall()
    col_names = [c[1] for c in cols]
    if "version" not in col_names:
        raise MigrationError("Malformed schema_version table: missing 'version' column")

    rows = conn.execute(text("SELECT version FROM schema_version")).fetchall()
    if not rows:
        return 0

    versions = []
    for r in rows:
        val = r[0]
        if val is None or not isinstance(val, int):
            raise MigrationError(f"Malformed schema_version table: non-integer version value: {val!r}")
        versions.append(val)

    max_v = max(versions)
    if max_v > CURRENT_SCHEMA_VERSION:
        raise MigrationError(
            f"Database schema version {max_v} is newer than supported version {CURRENT_SCHEMA_VERSION}"
        )
    return max_v


def _check_schema_consistency(conn, version: int) -> None:
    """Refuse unexplained inconsistent schemas where the version stamp is ahead of actual columns."""
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }

    if "tasks" in tables:
        task_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()}
        if version >= 1:
            expected_v1 = {"deadline_utc", "subject", "source", "created_at", "updated_at"}
            missing_v1 = expected_v1 - task_cols
            if missing_v1:
                raise MigrationError(
                    f"Inconsistent database schema: version stamp is {version}, but 'tasks' table is missing columns: {sorted(missing_v1)}"
                )
        if version >= 2:
            if "workspace_id" not in task_cols:
                raise MigrationError(
                    f"Inconsistent database schema: version stamp is {version}, but 'tasks' table is missing 'workspace_id'"
                )
        if version >= 4:
            task_indexes = {r[1] for r in conn.execute(text("PRAGMA index_list(tasks)")).fetchall()}
            if "ix_tasks_deadline_utc" not in task_indexes:
                raise MigrationError(
                    f"Inconsistent database schema: version stamp is {version}, but 'tasks' table is missing index 'ix_tasks_deadline_utc'"
                )

    if "action_receipts" in tables:
        receipt_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(action_receipts)")).fetchall()}
        if version >= 5:
            if "target_key" not in receipt_cols:
                raise MigrationError(
                    f"Inconsistent database schema: version stamp is {version}, but 'action_receipts' is missing 'target_key'"
                )


def _backup_db_file(db_path: str) -> str | None:
    """Create a consistent, verified SQLite backup before modifying an existing database.

    Uses SQLite's online backup API (sqlite3.Connection.backup) so that all committed
    pages — including those in active WAL files or held by open connections — are flushed
    and preserved consistently into a single standalone database file.

    Allocates collision-free names (with counter suffixes for multiple calls in the same second).
    Verifies backup integrity before returning. If backup fails, raises MigrationError.
    """
    if not db_path or not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None

    base_name = f"{db_path}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    backup_path = base_name
    counter = 1
    while os.path.exists(backup_path):
        backup_path = f"{base_name}_{counter}"
        counter += 1

    try:
        src = sqlite3.connect(db_path, timeout=10.0)
        try:
            dst = sqlite3.connect(backup_path, timeout=10.0)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception as e:
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        raise MigrationError(f"Pre-migration backup failed for '{db_path}': {e}") from e

    # Verify backup integrity separately
    try:
        verify_conn = sqlite3.connect(backup_path, timeout=10.0)
        try:
            res = verify_conn.execute("PRAGMA integrity_check").fetchall()
            if not res or res[0][0] != "ok":
                raise MigrationError(
                    f"Pre-migration backup integrity check failed on {backup_path}: {res}"
                )
        finally:
            verify_conn.close()
    except MigrationError:
        raise
    except Exception as e:
        raise MigrationError(f"Failed to verify pre-migration backup '{backup_path}': {e}") from e

    return backup_path


def _sync_indexes(conn, base, table_name: str, info: dict) -> None:
    """Create indexes the model declares but an already-existing table lacks.

    Names and uniqueness come from the Table object, so the upgraded DB converges
    on exactly what create_all() would have produced. Live PRAGMA is used instead of the
    inspector, whose reflection cache is stale for columns ALTERed in this same transaction.
    """
    table = base.metadata.tables.get(table_name)
    if table is None:
        return
    pragma_cols = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    if not pragma_cols:
        return
    table_cols = {r[1] for r in pragma_cols}
    have = {r[1] for r in conn.execute(text(f"PRAGMA index_list({table_name})"))}
    for idx in table.indexes:
        cols = [c.name for c in idx.expressions]
        if idx.name in have or not cols:
            continue
        # Only create index if all referenced columns actually exist on the table
        if not set(cols).issubset(table_cols):
            continue
        unique_clause = "UNIQUE " if idx.unique else ""
        conn.execute(
            text(f"CREATE {unique_clause}INDEX {idx.name} ON {table_name} ({', '.join(cols)})")
        )
        info["steps"].append(f"{table_name}.create_index:{idx.name}")


def _validate_final_schema(conn, base) -> None:
    """Verify that all required v5 tables and core columns exist before finalizing migration."""
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    required_tables = {
        "tasks",
        "notes",
        "timers",
        "reminders",
        "focus_sessions",
        "scheduled_alerts",
        "action_receipts",
        "workspaces",
        "session_notes",
        "coursework",
        "schema_version",
    }
    missing_tables = required_tables - tables
    if missing_tables:
        raise MigrationError(
            f"Migration validation failed: missing tables after migration: {sorted(missing_tables)}"
        )

    task_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()}
    required_task_cols = {
        "deadline_utc",
        "subject",
        "source",
        "created_at",
        "updated_at",
        "workspace_id",
    }
    missing_cols = required_task_cols - task_cols
    if missing_cols:
        raise MigrationError(
            f"Migration validation failed: 'tasks' table missing columns: {sorted(missing_cols)}"
        )

    receipt_cols = {
        r[1] for r in conn.execute(text("PRAGMA table_info(action_receipts)")).fetchall()
    }
    if "target_key" not in receipt_cols:
        raise MigrationError(
            "Migration validation failed: 'action_receipts' missing 'target_key' column"
        )


def run_migrations(engine, base, db_path: str | None = None) -> dict:
    """Bring the database up to CURRENT_SCHEMA_VERSION. Safe to call on every
    startup and on brand-new (empty) databases. ``base`` is the SQLAlchemy
    declarative Base from db.py. Returns an info dict."""
    info = {"version_before": 0, "version_after": 0, "backup_path": None, "steps": []}

    if db_path is None:
        db_path = str(engine.url.database or "")

    is_file_db = bool(
        db_path
        and db_path != ":memory:"
        and os.path.exists(db_path)
        and os.path.getsize(db_path) > 0
    )

    if is_file_db:
        # Pre-check database integrity and corrupt files before anything else
        try:
            pre_conn = sqlite3.connect(db_path, timeout=10.0)
            try:
                chk = pre_conn.execute("PRAGMA quick_check(1)").fetchall()
                if not chk or chk[0][0] != "ok":
                    raise MigrationError(
                        f"Database integrity check failed on '{db_path}': {chk}"
                    )
            finally:
                pre_conn.close()
        except sqlite3.DatabaseError as e:
            raise MigrationError(f"Cannot read database file '{db_path}': {e}") from e

    # Check current version and schema consistency outside the write transaction
    with engine.connect() as check_conn:
        version = _read_and_validate_version(check_conn)
        info["version_before"] = version
        _check_schema_consistency(check_conn, version)

        tables = {
            r[0]
            for r in check_conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        has_existing_tables = bool(tables - {"schema_version"})

    # If migration is needed and existing user data exists, create backup BEFORE acquiring write transaction
    if is_file_db and has_existing_tables and version < CURRENT_SCHEMA_VERSION:
        info["backup_path"] = _backup_db_file(db_path)

    # If already at CURRENT_SCHEMA_VERSION, return info (no-op)
    if version == CURRENT_SCHEMA_VERSION:
        info["version_after"] = CURRENT_SCHEMA_VERSION
        return info

    # Execute migrations inside an atomic transaction
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        )

        has_tasks = "tasks" in tables
        if version < 1:
            task_cols = (
                {r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()}
                if has_tasks
                else set()
            )
            for col, decl in _TASK_COLUMNS_V1:
                if has_tasks and col not in task_cols:
                    conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {decl}"))
                    info["steps"].append(f"tasks.add_column:{col}")
                    task_cols.add(col)

            # Backfill timestamps for pre-existing rows so old tasks stay usable.
            if has_tasks:
                conn.execute(
                    text(
                        "UPDATE tasks SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
                    )
                )
                info["steps"].append("tasks.backfill_created_at")

        if version < 2:
            if has_tasks:
                task_cols = {
                    r[1] for r in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()
                }
                for col, decl in _TASK_COLUMNS_V2:
                    if col not in task_cols:
                        conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {decl}"))
                        info["steps"].append(f"tasks.add_column:{col}")
                        task_cols.add(col)

        if version < 5:
            action_tables = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='action_receipts'"
                    )
                ).fetchall()
            }
            if action_tables:
                columns = {
                    r[1]
                    for r in conn.execute(text("PRAGMA table_info(action_receipts)")).fetchall()
                }
                if "target_key" not in columns:
                    conn.execute(
                        text("ALTER TABLE action_receipts ADD COLUMN target_key VARCHAR(64)")
                    )
                    info["steps"].append("action_receipts.add_column:target_key")

        # Create any brand-new tables inside the transaction
        base.metadata.create_all(bind=conn)

        # Sync declared indexes across all core tables
        for tbl in (
            "tasks",
            "action_receipts",
            "scheduled_alerts",
            "workspaces",
            "session_notes",
            "coursework",
            "timers",
            "reminders",
        ):
            _sync_indexes(conn, base, tbl, info)

        # Validate final schema before committing the version stamp
        _validate_final_schema(conn, base)

        # Stamp the final schema version
        conn.execute(text("DELETE FROM schema_version"))
        conn.execute(
            text("INSERT INTO schema_version (version) VALUES (:v)"),
            {"v": CURRENT_SCHEMA_VERSION},
        )
        info["steps"].append(f"schema_version:={CURRENT_SCHEMA_VERSION}")
        info["version_after"] = CURRENT_SCHEMA_VERSION

    return info
