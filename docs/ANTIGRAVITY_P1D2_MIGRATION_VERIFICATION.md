# Antigravity implementation guide: P1-D2 migration and backup reliability

Updated 1 October 2026 after repository cleanup. This is the next active milestone.
P1-D1 and its correction are reported complete. P1-D2 is not recorded as complete;
current migration code still uses a raw database file copy for backups. Verify
current source at the start of the run. Historical prompts are reference material.

## Outcome and scope

Protect existing SQLite records during a supported upgrade, create a usable
backup before mutation, and refuse unsupported or inconsistent databases safely.
Keep schema version 5 and reuse the current migration path. Complete P1-D2 in
this run; leave P1-E readiness and supervised desktop acceptance for later runs.

## Ready-to-paste prompt

Paste into Gemini 3.8 Flash in Antigravity with High reasoning:

```text
Continue VEGA AgentOS in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read AGENTS.md, README.md, docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md entry. Record branch, HEAD, and staged/unstaged/untracked baseline. Expected branch is main, but recheck it; do not change branches automatically. Work on P1-D2 only: verify and fix the additive SQLite migration and backup path. Do not repeat completed P1-A through P1-D1 work or perform another repository cleanup.

Preserve all existing changes, including staged cleanup deletions and renames, .env, jarvis.db and its backups/WAL files, personal data, and builds. Do not reset, clean, stash, stage, commit, push, or test against the personal database. Use one editing agent. If subagents are available, assign one read-only review of migration/backup failure modes and one read-only review of historical fixtures/test coverage. Each should give file/line evidence, concrete invariants, and uncertainties. Verify their findings yourself. If delegation is unavailable, do the reviews sequentially and report that limitation.

Inspect backend/migrations.py, db.py startup, main.py startup, and existing tests/test_migrations.py before editing. Check tests/conftest.py and environment handling before importing application modules so tests cannot initialize the personal database. Build synthetic file-backed SQLite fixtures for each supported starting point (legacy unversioned, v1, v2, v3, v4, and current v5). Derive fixtures from actual historical migration contracts; do not label a fresh current schema as an older version merely by changing its stamp. Preserve sentinel task/note/receipt rows where their tables exist. Verify expected tables, columns, index definitions (including uniqueness), schema_version, idempotent rerun, and fresh empty startup.

Verify that a complete pre-migration backup exists before the first migration mutation of an existing database. Include a WAL fixture with committed rows still in its WAL and an open connection. Current _backup_db_file uses shutil.copy2; investigate whether that preserves all committed data and use SQLite's consistent backup mechanism if needed. Plan backup ordering before acquiring a migration write transaction so the backup cannot deadlock against the migrator's own lock. Open the backup separately and verify integrity, sentinel values, and original schema. Allocate backup names without overwriting an existing backup, even for two runs in the same second. If backup creation fails, fail before schema/data mutation. Do not automatically restore or repair a user's database.

Test partial/inconsistent fixtures: a column already added with an older version stamp, a version stamp ahead of actual columns, a malformed schema_version table, a corrupt file, and an unsupported version newer than v5. Resume only known additive partial upgrades that can preserve data; reject unexplained inconsistencies clearly before mutation. Verify final schema before reporting migration success. Inspect the current ordering of version stamps and create_all: a failure creating remaining tables must not leave a database falsely accepted as fully migrated on next startup. Inject a representative mid-migration failure and prove the resulting state is either rolled back or safely resumable with its original backup intact. Avoid broad schema repair, row deletion, dropped tables, new dependencies, or a new migration framework.

Run focused migration tests from backend (python -m pytest tests/test_migrations.py, plus any new focused test file) and the full backend suite (python -m pytest), then git diff --check. Use a workspace-owned pytest temp directory if system temp is inaccessible; report the exact command substitution. Inspect both tracked diffs and newly created files. Do not rerun frontend builds for backend-only changes. Update docs/IMPLEMENTATION_LOG.md with exact commands/results, fixture versions, backup/failure behavior, files changed, and remaining limits. Refresh active documentation only where this milestone changes facts. Never reuse an earlier test result as a new run. Report synthetic SQLite evidence separately from the untested personal database. Stop after P1-D2; propose P1-E service/provider readiness as the next bounded run, without implementing it.
```

## Acceptance checklist

- Supported old fixtures reach v5 with original rows preserved and required schema validated.
- Current v5 rerun is a no-op; fresh database startup works.
- A separately readable backup preserves committed WAL data and original schema.
- Backup failure, corrupt input, and unsupported newer versions fail without migration writes.
- Known partial upgrades resume safely; unexplained inconsistent schemas are refused.
- Interrupted upgrade does not silently claim a complete schema on the next run.
- Focused/full backend checks and diff review are recorded with fresh results.
- Personal databases, credentials, existing staged changes, and generated builds remain preserved.

## Handoff format

Return: outcome; files changed; migration/backup choices and reasons; a compact
fixture/result matrix; exact test commands and results; unverified behavior;
remaining risks; and the next bounded milestone. If a data-safety design issue
blocks completion, record the specific evidence and safe state instead of
claiming the gate passed.
