# Antigravity prompt: P1-D2 migration verification

Paste into Gemini 3.8 Flash in Antigravity with High reasoning:

```text
Continue VEGA AgentOS on main. Read AGENTS.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md entry. Inspect current source and Git status first. Work on P1-D2 only: verify and, where needed, fix the additive SQLite migration and backup path. Stop before new schema features or broader persistence redesign.

Preserve all existing changes, .env, jarvis.db, personal data, and builds. Do not reset, clean, stash, commit, push, or test against the personal database. Use one editing agent; if subagents are available, assign bounded read-only reviews of migration code and old-schema fixtures. Verify their findings yourself.

Inspect backend/migrations.py, db.py startup, and existing test_migrations.py. Build synthetic file-backed SQLite fixtures for each supported upgrade starting point (at least v1 through v4, plus the pre-version legacy schema). Keep sentinel rows and assert their values survive upgrade to v5. Verify expected columns, indexes, schema_version, repeat-run no-op behavior, and fresh empty database startup. Do not confuse create_all on a fresh database with an upgrade of an old one.

Verify that a complete, usable pre-migration backup is created before the first ALTER of an existing database, including when SQLite uses WAL mode or has an open connection. Inspect the backup by opening it with SQLite and checking sentinel rows and original schema. If the existing file-copy approach is unsafe, make the smallest safe backup change and test it. Do not overwrite an existing backup with a timestamp collision.

Test partial or inconsistent states with temporary fixtures: a column already added while schema_version is old, a version stamp ahead of the actual columns, and a malformed/corrupt database. Define safe outcomes from current application contracts: recover idempotently only when data can be preserved and the schema validated; otherwise fail clearly before startup writes or destructive repair. Also check a database version newer than supported v5 fails closed. Never silently stamp a broken schema as current. Avoid printing database content or secrets.

Run focused migration tests and the full backend suite (python -m pytest), then git diff --check. Use a workspace-owned pytest temp directory if the standard temp directory is inaccessible. Update docs/IMPLEMENTATION_LOG.md with exact commands/results, fixture versions, backup and failure behavior, files changed, and limits. Report synthetic SQLite evidence separately from any untested real user database. Stop after P1-D2 and name one precise next milestone.
```
