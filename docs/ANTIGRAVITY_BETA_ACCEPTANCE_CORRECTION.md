# BETA-ACCEPTANCE correction: isolation before desktop testing

## Paste-ready instruction

Continue VEGA AgentOS from the current branch and dirty worktree. Read AGENTS.md,
docs/ANTIGRAVITY_START.md, docs/ANTIGRAVITY_BETA_ACCEPTANCE.md,
docs/BETA_ACCEPTANCE_CHECKLIST.md, and docs/IMPLEMENTATION_LOG.md.

Work on **BETA-ACCEPTANCE preparation correction only**. Implement the smallest
cohesive fix for the issues below. Stop before starting the real GUI, microphone,
models, or installer. The owner will perform desktop acceptance later.

Preserve all existing changes, personal .env, databases, WAL/backups, normal
Electron profile, and generated builds. Do not reset, clean, stash, commit, or
push. Never use personal data or the actual AppData beta profile as test fixtures.
Use one editing agent and bounded read-only subagents for path safety and
process/session ownership review. Record if delegation is unavailable.

## Findings confirmed in source on 3 October 2026

1. scripts/launch_beta.js inherits JARVIS_DB_PATH and VEGA_PROFILE_ROOT unchanged.
   backend/db.py gives JARVIS_DB_PATH precedence over VEGA_PROFILE=beta. An inherited
   personal database override can therefore bypass beta isolation.
2. seed_beta.verify_beta_target accepts either the beta environment flag OR a
   substring containing beta. With VEGA_PROFILE=beta it accepts a non-beta
   jarvis.db path. This was reproduced by calling the guard only; no DB was opened.
3. seed_database deletes ALL ScheduledAlert rows and active timers/pending
   reminders on every run. A repeated seed can erase acceptance session data.
4. Electron accepts an occupied port when /health merely reports profile=beta.
   It does not compare run_id. The launcher does not generate/share a run_id;
   backend and Electron can have different identities. Packaged startup also
   spawns a backend despite an already occupied accepted port.
5. Renderer profile checking is optional and missing fields are accepted;
   polling status is not an authorization gate for mutation requests.
6. Launcher starts shell wrappers and kills only those handles. Frontend exit
   does not stop the backend, failed backend startup does not stop the frontend,
   and process creation errors have no dedicated handler.
7. notificationService sets observed_by_user=true when it merely calls onToast.
   Enqueuing a banner does not prove that a person saw it.

## Required changes

### A. One validated beta target

- Establish one shared beta configuration contract across seed, launcher,
  backend, and Electron. Use the documented default beta root; test-only custom
  roots must be explicit, resolved, and validated before any DB import/migration.
- Override or reject inherited JARVIS_DB_PATH/VEGA_PROFILE_ROOT when launching
  beta. A flag or filename substring alone must never authorize a database.
- Validate the resolved target against the exact approved beta root and expected
  filename, including path traversal and existing symlink/junction escapes.
  Fail closed on conflicting overrides, production paths, and invalid roots.
  Keep normal-mode behavior intact.
- Seed only its synthetic records. Reseeding must preserve unrelated rows,
  user-created alerts/timers/reminders, and subsequent edits. Do not implement
  reset by silently deleting entities. A fresh fixture starts without alerts.

### B. One owned launch session

- Launcher generates a fresh opaque run_id and passes the same value to every
  process and renderer profile bridge. This is a local session consistency
  check, not a security authentication token.
- Refuse a pre-existing occupied beta backend port before starting children.
  After spawning the owned backend, wait with a bounded deadline for successful
  /health with exact profile AND run_id and healthy DB readiness. Check HTTP
  status and malformed/missing identity fields. Fail closed on mismatch.
- Ensure Electron packaged and development paths follow the same ownership
  rule. Avoid double spawning a backend. Renderer mutations and WebSockets
  must remain unavailable until the expected session is verified; recheck
  identity after reconnects and block writes on mismatch. Centralize the gate.
- Handle child error/exit, backend readiness failure, Electron exit, Ctrl+C,
  and repeat launches. Terminate only processes created by this launch and
  wait for teardown. On Windows, killing a shell handle alone is insufficient;
  use explicit owned process-tree handling without global process-name kills.
- Resolve Python/npm executables deliberately; support an explicit Python
  executable override and actionable failure if Python is unavailable.
  Do not assume the Windows Store alias is a working interpreter.

### C. Truthful evidence and owner instructions

- Keep observed_by_user=false for native dispatch and banner enqueue/render.
  Only explicit user interaction can supply observation evidence. If fallback
  enqueue fails too, report failure and allow a retry instead of deduping it as
  delivered. Add meaningful tests for these outcomes.
- Correct the checklist: actual Quit vs hide-on-close, verified PowerShell
  syntax/artifact existence for packaged launch, and safe teardown before any
  explicit synthetic-profile reset. Do not advise deleting a live SQLite DB.
- Test stale loopback data by stopping the owned synthetic backend or injecting
  request failures; disconnecting Wi-Fi alone does not sever localhost.
- Distinguish receipt-producing actions from read-only intents. Keep cloud/mic
  steps disabled or explicitly deferred. Record CPU measurements with context
  instead of inventing a universal <1% acceptance threshold.
- Label package commands as conditional/unverified if no current artifact has
  been inspected. A source fix or Vite build does not verify an old installer.

## Verification gate

Use temporary SQLite files and injected process/HTTP adapters. No real desktop
launches or real profile changes. Add regression coverage for:

- Inherited personal DB override with VEGA_PROFILE=beta rejected before writes;
  sentinel database unchanged. Reject substring-only beta and escaped roots.
- Fresh seed counts, repeat seed deduplication, and unrelated/user-edited records
  plus active timers/reminders/alerts surviving reseed.
- Occupied port, same-profile wrong-run backend, missing identity, timeout,
  child spawn failure, and owned cleanup after child exit.
- Mutations blocked before identity verification and after mismatch/reconnect;
  no request reaches the wrong backend.
- Notification acceptance vs observation and failed fallback retry.

Run focused new tests first, then:

```text
python -m pytest
npm test --prefix frontend
npm run lint --prefix frontend
git diff --check
```

Use the project's actual supported Python executable if `python` is an alias.
Use temporary build output paths if build verification is needed; preserve
existing dist/release artifacts. Report exact commands, counts, warnings, and
mock boundaries. Fix test fixture global/module reload leakage if it affects
the full suite; a focused pass alone does not establish isolation.

Update the implementation log and current start guide. Return changed files,
test outcomes, a concise description of the validated data/session paths,
remaining limits, and owner launch commands. Stop after the correction and
leave every desktop acceptance row NOT RUN until actually observed.

## Independent review evidence

Codex ran the existing beta tests from backend using Python 3.14 and a dedicated
temporary directory: `python3.14.exe -m pytest tests/test_beta_profile.py -q
-p no:cacheprovider --basetemp <review-temp>/pytest`: **5 passed, 1 warning**.
These tests did not cover the blockers above. The guard-only negative probe
accepted a synthetic non-beta `jarvis.db` path under VEGA_PROFILE=beta without
opening any database. `git diff --check` passed with line-ending warnings.
The reported 420-test full suite was not independently rerun in this review.
No GUI, microphone, live backend, installer, or personal database was opened.
