# Next run: BETA session integration and seed entry point

## Paste-ready prompt

Continue VEGA AgentOS on the current branch and preserve the dirty worktree.
Read AGENTS.md, docs/ANTIGRAVITY_START.md, the beta preparation correction guide,
and docs/IMPLEMENTATION_LOG.md. Work on **beta session integration and the seed
entry point only**. No new features or visual redesign.

Preserve .env, all personal databases/WAL/backups, normal Electron data, existing
builds, and unrelated changes. No reset/clean/stash/commit/push. Use temporary
synthetic roots and injected network/process adapters. Do not start real GUI,
microphone, models, or installers. Use one editing agent and bounded read-only
reviewers; document if delegation is unavailable.

## Independently confirmed remaining problems

- verifyBackendSession is called only by its unit test. App's health poller
  updates a label but never verifies the session. mutationGuard now requires
  verification in browsers, so task completion/deletion stay blocked in both
  normal and beta use. Most other writes and WebSockets bypass that guard.
- apiConfig accepts missing run_id even when an expected runId exists because
  its mismatch condition requires data.run_id to be truthy.
- npm run seed:beta invokes Python directly without setting VEGA_PROFILE=beta.
  The strengthened validator rejects it in a fresh shell. Reproduced with a
  working interpreter and isolated temporary APPDATA; no database was opened.
- Launcher tests duplicate port/probe functions instead of importing production
  code. Session tests set no expected beta run ID and do not exercise app wiring.

## Required implementation

1. Wire startup verification into the actual application lifecycle. Require
   exact beta profile and nonempty matching run_id, healthy database readiness,
   and a bounded health timeout. Missing client identity must fail closed in
   beta. Normal mode must keep working under its documented identity contract.
2. Route every backend mutation through one shared verified request boundary:
   task create/complete/delete, timers/reminders/focus, workspace/session notes,
   coursework/notes, chat/tool execution, voice controls/transcription, radar
   mutations. Audit active call sites. Reads may remain available according to
   their existing contract; don't confuse health/read-only status with write
   authorization. Keep clear errors and preserve user input on blocked writes.
3. Gate relevant WebSocket creation and reconnection on that same session state.
   Invalidate verification on offline/missing/mismatched identity, close stale
   sockets, and restore safe operation only after the expected session verifies.
   Prevent an old overlapping health response from authorizing a newer session.
   Don't solve this by setting verified=true blindly or disabling the guard.
4. Make npm run seed:beta work from a fresh PowerShell environment: establish
   beta config BEFORE DB resolution/import, resolve a working Python executable
   consistently with the launcher, and validate the synthetic root. Do not
   require the owner to guess environment prerequisites. Reject unsafe inherited
   roots before creating files or touching Electron settings. Share configuration
   helpers where practical; normal-profile configuration must remain intact.
5. Test production functions, not copied implementations. Export side-effect-free
   launcher validation/lifecycle helpers or move them into an importable module.
   Importing a helper in a test must never launch the real application.
6. Keep reseeding non-destructive. Verify an edited seed task title does not
   silently recreate its original copy; text/title alone is not stable identity.
   Use a small durable seed identity/bootstrap record if needed rather than
   overwriting user edits or removing user rows.

## Acceptance tests and stop condition

- Test actual app startup wiring: valid synthetic beta identity enables a task
  completion/deletion and a representative chat/create route; normal mode still
  permits its existing workflows. Test missing/wrong profile/run_id, unready DB,
  offline/reconnect, and stale response with zero unauthorized writes/sockets.
- Exercise the actual seed entry point in a subprocess with temporary APPDATA,
  fresh environment, and explicit working Python override. Verify initial counts,
  repeat seed, edited title, preserved active alerts, and unsafe overrides.
- Test imported production launcher port/probe/lifecycle functions with injected
  adapters for occupied port, spawn failure, child exit, and owned teardown.
- Run focused tests, npm test --prefix frontend, npm run lint --prefix frontend,
  and git diff --check. Run python -m pytest if backend/seed/config changes affect
  it. Preserve existing build outputs; desktop and old packaged binary remain
  unverified. Do not claim source-level lifecycle tests prove Windows teardown.

Update the start guide, checklist prerequisites, and implementation log with
exact results and evidence boundaries. Return changed files, commands/results,
remaining limits, and corrected owner commands. Stop before desktop acceptance.

## Independent review results, 3 October 2026

- Full backend: Python 3.14, from backend, `-m pytest -q -p no:cacheprovider
  --basetemp <review-temp>/pytest`, scoped TEMP/TMP: **423 passed, 2 warnings,
  38.82s**. Conftest reported incomplete temp cleanup; review cleanup handled
  separately after process exit.
- `npm test --prefix frontend`: **51 passed**.
- `npm run lint --prefix frontend`: **0 errors, 19 warnings**.
- `git -c core.safecrlf=false diff --check`: passed.
- Direct production verifier probe with expected beta run ID and response
  lacking run_id returned true. No network or desktop calls made by this probe.
- Direct seed CLI with temporary APPDATA and no profile flag exited 1 with
  `Cannot validate beta target when VEGA_PROFILE is not 'beta'`.

Previous improvements are present: beta DB override refusal in backend,
non-deleting reseed, shared launcher run ID/readiness wait, owned tree termination
code, and truthful notification enqueue evidence. These passing tests do not
establish working renderer integration or real desktop acceptance.
