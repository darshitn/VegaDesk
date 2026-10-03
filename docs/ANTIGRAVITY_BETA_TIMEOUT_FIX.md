# Final poller failure case: revoke verification on a live health timeout

## Paste-ready prompt

Read AGENTS.md and this file. Continue from the dirty working state. Fix only
health timeout handling in frontend/src/lib/healthPoller.js and apiConfig.js,
plus production-path regression tests. Preserve personal data/configuration,
normal profile, existing builds, and unrelated edits. Do not reset, clean, stash,
commit, push, or launch the real GUI/microphone/models/installer. One editing
agent; use a bounded read-only reviewer if available.

The run-ID handoff fix and stop/supersession safeguards are correct in the
reviewed paths. Keep them. The remaining failure is:

1. A healthy beta check sets verified=true.
2. The next fetch hangs until the poller's timeout aborts its controller.
3. The verifier treats that abort as cancellation, returning false without
   invalidating global verification.
4. The poller displays degraded, but verifiedFetch still permits mutations and
   App's session subscriber is not told to close sockets.

Distinguish an active probe timeout from owner stop/supersession. A current
probe timing out must set verified=false, publish a truthful timeout/offline
reason, notify subscribers, and retry on the existing bounded schedule. A
stopped or superseded probe must remain unable to modify current session state.
Do not loosen strict beta identity matching or simply remove cancellation checks.

Add a test through production createHealthPoller: first matching beta health
response verifies; second injected fetch respects AbortSignal and times out;
verification becomes false, subscribers observe revocation, and verifiedFetch
POST is blocked with zero mutation requests. A later matching response restores
authorization. Keep valid-beta, missing/wrong identity, and stopped/superseded
response tests passing; stop timers and restore test globals.

Run focused tests, npm test --prefix frontend, npm run lint --prefix frontend,
and git -c core.safecrlf=false diff --check. Backend is unchanged, so no full
backend rerun is needed. Update docs/ANTIGRAVITY_START.md and implementation log
with exact results and mocked evidence boundaries. Stop before owner desktop
testing; checklist rows remain NOT RUN.

## Independent evidence, 3 October 2026

- npm test --prefix frontend: 61 passed, 0 failed.
- npm run lint --prefix frontend: 0 errors, 20 warnings.
- git -c core.safecrlf=false diff --check: passed.
- Production poller probe with simulated Electron beta identity and injected
  fetch: statuses [ok, degraded], verifiedAfterTimeout=true, error=null.
  This is a synthetic network-failure reproduction, not a real desktop run.
- No real backend service, GUI, microphone, cloud model, installer, or personal
  data was accessed. The full backend suite was not rerun for this frontend fix.
