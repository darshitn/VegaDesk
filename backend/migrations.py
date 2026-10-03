"""Explicit, non-destructive schema migrations.

SQLAlchemy ``create_all()`` creates missing tables but never alters existing
ones, so the tasks-table extension needs real ALTER steps. Rules:

- Every migration step is idempotent (checks state before acting).
- Before the first migration that ALTERs an existing user database, the DB file
  is backed up to ``<db>.bak-<timestamp>`` using SQLite's online backup API to
  guarantee consistent preservation of committed WAL pages and open connections.
  Backups are allocated atomically to avoid TOCTOU races, guarded by an overall
  elapsed-time deadline, verified with PRAGMA integrity_check, and never committed (see .gitignore).
- Transaction behavior: the migration connection uses autocommit on the raw DBAPI
  connection with explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``, ensuring
  DDL (ALTER TABLE, CREATE TABLE, CREATE INDEX) genuinely rolls back on any mid-migration failure.
- Rollback = stop the backend and copy the backup over the DB file.
- The current schema version is tracked in a ``schema_version`` table.
- Unknown or unexplained inconsistent schemas (version ahead of columns, broken/truncated tables,
  missing model columns, contradictory indexes) and unsupported newer versions fail closed before writes.
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


def _check_schema_consistency(conn, base, version: int) -> None:
    """Refuse unexplained inconsistent schemas before any writes or backups.

    - Rejects version stamp ahead of actual columns.
    - Rejects version-only database claiming v5 without model tables.
    - Rejects existing broken tables with unexplained missing columns.
    - Rejects existing same-named indexes with contradictory definitions.
    """
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }

    # Version-only database check
    if version >= CURRENT_SCHEMA_VERSION and not (tables - {"schema_version"}):
        raise MigrationError(
            f"Inconsistent database schema: version stamp is {version}, but database contains no model tables"
        )

    # Check every table that already exists against Base metadata
    for table_name in tables:
        if table_name == "schema_version":
            continue
        table = base.metadata.tables.get(table_name)
        if table is None:
            continue  # Harmless extra tables permitted

        db_cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
        model_cols = {c.name for c in table.columns}
        missing_cols = model_cols - db_cols

        # For known migration tables, subtract columns that the migration adds if version < target
        if table_name == "tasks":
            migratable = set()
            if version < 1:
                migratable.update(c[0] for c in _TASK_COLUMNS_V1)
            if version < 2:
                migratable.update(c[0] for c in _TASK_COLUMNS_V2)
            unexplained_missing = missing_cols - migratable
            if unexplained_missing:
                raise MigrationError(
                    f"Inconsistent database schema: 'tasks' table has unexplained missing columns: {sorted(unexplained_missing)}"
                )
        elif table_name == "action_receipts":
            migratable = {"target_key"} if version < 5 else set()
            unexplained_missing = missing_cols - migratable
            if unexplained_missing:
                raise MigrationError(
                    f"Inconsistent database schema: 'action_receipts' has unexplained missing columns: {sorted(unexplained_missing)}"
                )
        else:
            # For any other existing table (e.g. workspaces, coursework), migrations do not alter them.
            # If it exists but is missing required model columns, it is an unexplained broken table!
            if missing_cols:
                raise MigrationError(
                    f"Inconsistent database schema: existing table '{table_name}' has unexplained missing columns: {sorted(missing_cols)}"
                )

        # Check existing indexes on this table for contradictory definitions
        index_list = conn.execute(text(f"PRAGMA index_list({table_name})")).fetchall()
        indexes_by_name = {
            r[1]: (bool(r[2]), bool(r[4]) if len(r) > 4 else False)
            for r in index_list
        }
        for idx in table.indexes:
            expected_cols = [c.name for c in idx.expressions]
            if idx.name in indexes_by_name:
                is_unique, is_partial = indexes_by_name[idx.name]
                idx_cols = [
                    r[2] for r in conn.execute(text(f"PRAGMA index_info('{idx.name}')")).fetchall()
                ]
                if idx.unique and not is_unique:
                    raise MigrationError(
                        f"Inconsistent database schema: index '{idx.name}' on '{table_name}' is non-unique, expected UNIQUE"
                    )
                if idx.unique and is_partial:
                    raise MigrationError(
                        f"Inconsistent database schema: index '{idx.name}' on '{table_name}' is a partial index, expected unconditional UNIQUE"
                    )
                if idx_cols != expected_cols:
                    raise MigrationError(
                        f"Inconsistent database schema: index '{idx.name}' on '{table_name}' has wrong columns {idx_cols}, expected {expected_cols}"
                    )
            elif idx.unique:
                # Check if any alternate index on these columns is contradictory (e.g. non-unique or partial)
                for r in index_list:
                    idx_name = r[1]
                    cols = [
                        info_r[2]
                        for info_r in conn.execute(text(f"PRAGMA index_info('{idx_name}')")).fetchall()
                    ]
                    if cols == expected_cols:
                        is_u = bool(r[2])
                        is_p = bool(r[4]) if len(r) > 4 else False
                        if not is_u:
                            raise MigrationError(
                                f"Inconsistent database schema: index '{idx_name}' on '{table_name}' is non-unique, expected UNIQUE"
                            )
                        if is_p:
                            raise MigrationError(
                                f"Inconsistent database schema: index '{idx_name}' on '{table_name}' is a partial index, expected unconditional UNIQUE"
                            )


def _reserve_backup_path(db_path: str) -> str:
    """Atomically allocate and create an exclusive backup file to prevent TOCTOU races."""
    base_name = f"{db_path}.bak-{time.strftime('%Y%m%d%H%M%S')}"
    candidate = base_name
    counter = 0
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return candidate
        except FileExistsError:
            counter += 1
            candidate = f"{base_name}_{counter}"


def _backup_db_file(db_path: str, timeout: float = 10.0) -> str | None:
    """Create a consistent, verified SQLite backup before modifying an existing database.

    Uses SQLite's online backup API (sqlite3.Connection.backup) so that all committed
    pages — including those in active WAL files or held by open connections — are flushed
    and preserved consistently into a single standalone database file.

    Atomically allocates collision-free names (with counter suffixes for multiple calls in the same second).
    Guards backup execution with an overall elapsed-time deadline.
    Verifies backup integrity before returning. If backup fails or times out, cleans up the
    attempt's incomplete backup and raises MigrationError.
    """
    if not db_path or not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None

    backup_path = _reserve_backup_path(db_path)

    t0 = time.monotonic()

    def _progress(status, remaining, total):
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"Backup exceeded overall deadline of {timeout}s")

    src = None
    dst = None
    try:
        src = sqlite3.connect(db_path, timeout=min(timeout, 2.0))
        dst = sqlite3.connect(backup_path, timeout=min(timeout, 2.0))
        src.backup(dst, pages=250, progress=_progress, sleep=0.05)
    except (TimeoutError, Exception) as e:
        if dst is not None:
            try:
                dst.close()
            except Exception:
                pass
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        if isinstance(e, TimeoutError):
            raise MigrationError(f"Pre-migration backup timed out after {timeout}s for '{db_path}': {e}") from e
        raise MigrationError(f"Pre-migration backup failed for '{db_path}': {e}") from e
    finally:
        if dst is not None:
            try:
                dst.close()
            except Exception:
                pass
        if src is not None:
            try:
                src.close()
            except Exception:
                pass

    # Verify backup integrity separately
    try:
        verify_conn = sqlite3.connect(backup_path, timeout=timeout)
        try:
            res = verify_conn.execute("PRAGMA integrity_check").fetchall()
            if not res or res[0][0] != "ok":
                raise MigrationError(
                    f"Pre-migration backup integrity check failed on {backup_path}: {res}"
                )
        finally:
            verify_conn.close()
    except MigrationError:
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        raise
    except Exception as e:
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
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


def _validate_orm_schema(conn, base) -> None:
    """Verify that all Base metadata tables, columns, and critical indexes exist and match model requirements.

    Permits harmless additional columns/indexes.
    """
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }

    if "schema_version" not in tables:
        raise MigrationError("Migration validation failed: missing 'schema_version' table")

    for table_name, table in base.metadata.tables.items():
        if table_name not in tables:
            raise MigrationError(
                f"Migration validation failed: missing required model table '{table_name}'"
            )

        pragma_cols = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        db_cols = {r[1] for r in pragma_cols}
        required_cols = {c.name for c in table.columns}
        missing_cols = required_cols - db_cols
        if missing_cols:
            raise MigrationError(
                f"Migration validation failed: table '{table_name}' is missing required columns: {sorted(missing_cols)}"
            )

        # Validate declared indexes and critical uniqueness
        index_list = conn.execute(text(f"PRAGMA index_list({table_name})")).fetchall()
        indexes_by_name = {
            r[1]: (bool(r[2]), bool(r[4]) if len(r) > 4 else False)
            for r in index_list
        }

        for idx in table.indexes:
            expected_cols = [c.name for c in idx.expressions]
            if idx.name in indexes_by_name:
                is_unique, is_partial = indexes_by_name[idx.name]
                idx_cols = [
                    r[2] for r in conn.execute(text(f"PRAGMA index_info('{idx.name}')")).fetchall()
                ]
                if idx.unique and not is_unique:
                    raise MigrationError(
                        f"Migration validation failed: index '{idx.name}' on '{table_name}' is not UNIQUE as required by model"
                    )
                if idx.unique and is_partial:
                    raise MigrationError(
                        f"Migration validation failed: index '{idx.name}' on '{table_name}' is a partial index, expected unconditional UNIQUE"
                    )
                if idx_cols != expected_cols:
                    raise MigrationError(
                        f"Migration validation failed: index '{idx.name}' on '{table_name}' has wrong columns {idx_cols}, expected {expected_cols}"
                    )
            elif idx.unique:
                # If not under idx.name, check if another UNIQUE index covers expected_cols
                matching_unique = False
                for r in index_list:
                    is_u = bool(r[2])
                    is_p = bool(r[4]) if len(r) > 4 else False
                    if is_u and not is_p:  # Must be unique AND not partial
                        u_cols = [
                            info_r[2]
                            for info_r in conn.execute(text(f"PRAGMA index_info('{r[1]}')")).fetchall()
                        ]
                        if u_cols == expected_cols:
                            matching_unique = True
                            break
                if not matching_unique:
                    raise MigrationError(
                        f"Migration validation failed: missing required unique index for '{table_name}' on columns {expected_cols}"
                    )


def run_migrations(engine, base, db_path: str | None = None, backup_timeout: float = 10.0) -> dict:
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
            pre_conn = sqlite3.connect(db_path, timeout=min(backup_timeout, 2.0))
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
        _check_schema_consistency(check_conn, base, version)

        tables = {
            r[0]
            for r in check_conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        has_existing_tables = bool(tables - {"schema_version"})

        # If already at CURRENT_SCHEMA_VERSION, validate full schema before accepting (no-op)
        if version == CURRENT_SCHEMA_VERSION:
            _validate_orm_schema(check_conn, base)
            info["version_after"] = CURRENT_SCHEMA_VERSION
            return info

    # If migration is needed and existing user data exists, create backup BEFORE acquiring write transaction
    if is_file_db and has_existing_tables and version < CURRENT_SCHEMA_VERSION:
        info["backup_path"] = _backup_db_file(db_path, timeout=backup_timeout)

    # Execute migrations inside an explicit atomic transaction with genuine DDL rollback
    with engine.connect() as conn:
        raw_conn = getattr(conn.connection, "driver_connection", getattr(conn.connection, "dbapi_connection", None))
        orig_isolation = getattr(raw_conn, "isolation_level", "")
        if raw_conn is not None:
            raw_conn.isolation_level = None

        try:
            conn.execute(text("BEGIN IMMEDIATE"))

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
            for tbl in base.metadata.tables:
                _sync_indexes(conn, base, tbl, info)

            # Validate complete ORM schema before committing the version stamp
            _validate_orm_schema(conn, base)

            # Stamp the final schema version
            conn.execute(text("DELETE FROM schema_version"))
            conn.execute(
                text("INSERT INTO schema_version (version) VALUES (:v)"),
                {"v": CURRENT_SCHEMA_VERSION},
            )
            info["steps"].append(f"schema_version:={CURRENT_SCHEMA_VERSION}")
            info["version_after"] = CURRENT_SCHEMA_VERSION

            conn.execute(text("COMMIT"))
        except Exception:
            try:
                conn.execute(text("ROLLBACK"))
            except Exception:
                pass
            raise
        finally:
            if raw_conn is not None:
                try:
                    raw_conn.isolation_level = orig_isolation
                except Exception:
                    pass

    return info
