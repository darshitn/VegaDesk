# Antigravity: current run and resume guide

**Agent Checkpoint, 3 October 2026 (Beta Poller Live Health Timeout Revocation Complete):**
The active health timeout handling fix defined in [ANTIGRAVITY_BETA_TIMEOUT_FIX.md](ANTIGRAVITY_BETA_TIMEOUT_FIX.md) is **complete**:
- **Active Timeout vs Stop/Supersession Discard Separation:** `verifyBackendSession()` in `frontend/src/lib/apiConfig.js` now uses `isDiscarded()` (evaluating `options.isCancelled` and sequence epoch) rather than conflating abort signal abortion with cancellation. An active probe timing out revokes session verification (`setSessionVerified(false, { error: 'Backend health check timed out' })`), publishes a truthful timeout/offline reason, and notifies all subscribers (causing WebSockets to disconnect and `verifiedFetch` to block mutations fail-closed).
- **State Invariance on Stop/Supersession:** When a probe is stopped (`poller.stop()`) or superseded by a newer probe check, `isDiscarded()` returns `true`, guaranteeing that late delayed responses or abort rejections cannot alter the current session state.
- **Production Poller Regression Tests:** Added regression tests in `frontend/tests/interactionHelpers.test.js`:
  1. `healthPoller: active health timeout revokes verification, blocks mutations, and recovers on healthy probe`: verified → timeout → blocked mutation → successful recovery through production `createHealthPoller`.
  2. `healthPoller: stopped or superseded probe does not change current verified session state`: confirms a stopped probe does not touch verified state.
- **Automated Verification:**
  - `npm test --prefix frontend`: **63 passed**, 0 failed (all 63 tests passed in 739ms, up from 61).
  - `npm run lint --prefix frontend`: **0 errors**, 21 warnings (oxlint).
  - `git -c core.safecrlf=false diff --check`: passed (0 whitespace errors).
  - Backend regression suite: preserved (425 passed in prior run; zero backend files modified).

**Next step (Supervised Desktop Acceptance Session by Owner):**
The owner will run the supervised desktop verification using:
1. `npm run seed:beta`
2. `npm run dev:beta`
3. Evaluate and fill out the 32-case results matrix in [docs/BETA_ACCEPTANCE_CHECKLIST.md](BETA_ACCEPTANCE_CHECKLIST.md).

No further autonomous coding or unprompted GUI/mic starts should take place. All 32 checklist rows remain NOT RUN.

## Current references

- [Session integration specification](ANTIGRAVITY_BETA_SESSION_INTEGRATION.md)
- [Beta acceptance checklist](BETA_ACCEPTANCE_CHECKLIST.md)
- [Product reality report](VEGA_PROJECT_REALITY_REPORT_2026-10-01.md)
- [Polished beta roadmap](POLISHED_BETA_ROADMAP.md)
- [Active Phase 1 plan](PHASE1_PLAN.md)
- [Architecture brief](VEGA_AGENTOS_BRIEF.md)
- [Implementation log](IMPLEMENTATION_LOG.md)

The original P1-A/P1-B startup instructions are preserved in
[the historical start guide](history/2026-antigravity/ANTIGRAVITY_START_ORIGINAL_2026-10-01.md).
