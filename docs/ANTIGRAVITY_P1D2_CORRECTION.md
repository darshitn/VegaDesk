# Next Antigravity run: P1-D2 correction

Prepared 1 October 2026. Use Gemini 3.8 Flash with High reasoning.

## Why this run comes first

Codex independently reran the two migration suites: 23 tests passed. Additional
temporary SQLite probes exposed three gaps those tests do not cover:

- A file containing only `schema_version=5` is accepted without required tables.
- `_build_v2_fixture` upgrades to v5 with eight Workspace model columns missing;
  a normal SQLAlchemy Workspace query raises `OperationalError`.
- An injected failure at `create_all` leaves `tasks.deadline_utc` added to a
  legacy database. The version is not advanced, but DDL was not rolled back.

The full 388-test result is Antigravity-reported, not independently rerun here.
This is a targeted correction, not authorization to repair personal data.

## Paste this prompt

```text
Continue VEGA in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read AGENTS.md, README.md, docs/PHASE1_PLAN.md, docs/IMPLEMENTATION_LOG.md, and docs/POLISHED_BETA_ROADMAP.md. Record current branch/HEAD and staged/unstaged/untracked changes; do not assume an old commit. Work only on the P1-D2 correction below, then stop.

Preserve every existing change, .env, personal jarvis.db, backups/WAL files, and generated builds. No reset, clean, stash, stage, commit, push, personal database inspection/migration, or installer execution. Keep one editing agent. If coding subagents exist, use one bounded read-only migration reviewer and one bounded read-only test reviewer. Browser subagents alone do not count as coding reviewers; use sequential review if necessary.

1. Reproduce the three findings in this guide using temporary file-backed databases. Inspect backend/migrations.py and both migration test suites. Do not equate passing current tests with closure.
2. Validate the required ORM schema even when version == 5. Check required table/column presence and the definitions of critical indexes, particularly action_receipts.idempotency_key uniqueness and indexed columns. A version-only database, missing required model columns, or a same-named nonunique/wrong-column index must fail clearly before writes. Derive requirements from the actual Base metadata; permit harmless additional columns/indexes. Do not blindly alter unexplained broken tables.
3. Correct historical fixture definitions using real migration/source history. Current fixtures truncate Workspace, SessionNote, ActionReceipt, and Coursework schemas. A simplified broken fixture is an inconsistent-schema rejection case, not a successful historical upgrade. Add representative ORM reads/writes after valid upgrades and enforce duplicate idempotency-key rejection on an upgraded existing receipt table. Preserve sentinel rows.
4. Make SQLite transaction behavior explicit for the migration connection, considering sqlite3 legacy transaction mode and supported Python versions. engine.begin() alone does not prove DDL rollback. Inject failures after early ALTER and late table/index creation; snapshot and compare tables, columns, indexes, version and rows after disposing/reopening the database. Verify genuine rollback and safe rerun. Keep backup creation before the migration write transaction; avoid changing unrelated runtime transaction behavior.
5. Audit backup timing: connect(timeout=10) is not an overall backup deadline. src.backup(dst) currently has no progress/deadline guard. Add a bounded elapsed-time policy if needed and verify a held lock with temporary connections/subprocesses and a watchdog so the test cannot hang. Raise a clear MigrationError on expiry, close handles, preserve source, and clean up only this attempt's incomplete backup. Reserve backup filenames atomically to prevent an exists-check race overwriting another process's backup. Do not claim guarantees beyond the tests.
6. Run python -m pytest tests/test_migrations.py tests/test_p1_d2_migration_verification.py (plus any new focused suite), then python -m pytest from backend. Use a fresh workspace-owned --basetemp and TEMP/TMP if system temp is inaccessible. Run git diff --check and inspect new files. Record exact commands, fresh outcomes, negative cases and database-vs-desktop limits in docs/IMPLEMENTATION_LOG.md. Correct the earlier report's inaccurate historical version mapping and rollback/timeout claims in an appended correction entry. Update current start instructions only after the gate passes.

Stop after this correction. Do not implement P1-E or UI polish yet. Return files changed, fixes and reasons, fixture/result matrix, exact tests, limitations and the next step: P1-E1 readiness/fault isolation, followed by the beta milestones in docs/POLISHED_BETA_ROADMAP.md.
```

## Primary references

- [SQLAlchemy SQLite transaction control](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#transactions-with-sqlite-and-the-sqlite3-driver): legacy sqlite3 mode does not automatically BEGIN for DDL.
- [Python SQLite backup interface](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup): progress callbacks and retry sleep are separate from connection timeout.
- [SQLite backup API](https://www.sqlite.org/backup.html): online backups provide a consistent snapshot; lock/retry behavior needs explicit handling.
