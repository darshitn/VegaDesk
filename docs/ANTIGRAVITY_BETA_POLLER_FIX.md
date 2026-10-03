# Beta acceptance: preserve run identity through the health poller

## Paste this into Antigravity

Continue from the current dirty worktree. Read AGENTS.md and this file.
Fix only the production health-poller identity handoff and its regression tests.
Preserve existing work, .env, personal databases/WAL/backups, normal profile, and
build artifacts. No reset/clean/stash/commit/push or real desktop/mic/model/installer
launch. Use one editing agent and a bounded read-only integration reviewer if
available; otherwise inspect sequentially and record that limitation.

### Confirmed failure

App creates createHealthPoller with default options. The poller reads the profile
but passes only `{profile, isBeta}` as options.profileInfo to verifyBackendSession.
That replaces the full Electron profile info, dropping runId. The verifier then
correctly refuses beta before making any health request. Consequently every gated
write and alert/voice socket remains unavailable despite the healthy backend.

### Smallest fix

- Preserve full current getProfileInfo(), especially runId and port, through
  createHealthPoller. When expectedProfile is supplied, override only the intended
  profile fields; do not discard session identity. Do not loosen beta validation.
- Add a regression test through production createHealthPoller using a simulated
  Electron profile bridge reporting beta, port 8005, and a nonempty runId, with
  default poller options exactly like App. A matching mocked health response must
  be fetched, authorize the session, and permit a representative verifiedFetch
  POST. Missing/wrong run_id must deny the session and that mutation. Restore all
  globals and stop the poller after each test.
- Verify poller stop/supersession cannot let a delayed response authorize a
  discarded session. Invalidate/cancel owned in-flight verification if needed;
  don't disable verification to satisfy this test.
- Run focused tests, npm test --prefix frontend, npm run lint --prefix frontend,
  and git -c core.safecrlf=false diff --check. Backend need not be changed or
  rerun for this frontend-only fix.
- Update the start guide and implementation log with exact tests and mocked
  boundaries. Stop with corrected owner commands and desktop rows NOT RUN.

## Independent review evidence, 3 October 2026

`npm test --prefix frontend`: 58 passed. Production poller probe with simulated
valid Electron beta identity: status=degraded, health fetch calls=0,
verified=false, error=Missing client run_id in beta mode. This uses the actual
poller and verifier modules; no backend, GUI, microphone, or cloud call launched.
`git -c core.safecrlf=false diff --check` passed. Backend beta tests were run with
temporary SQLite and isolated subprocess fixtures; results are in the log.

The wider integration improvements are present in source: centralized mutation
calls, session subscription/socket gates, stricter direct verifier checks,
production launcher helper imports, and the Node seed entry point. The reported
full 425-test backend suite has not been independently rerun during this review.
