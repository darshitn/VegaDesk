# Next run: P1-E2A interaction correctness and local onboarding

Prepared 2 October 2026. Use Gemini 3.8 Flash with High reasoning.

Codex independently reran the migration and P1-E1 suites: **42 passed, 2
warnings**. Full 407-backend and 19-frontend results remain agent-reported.
The next useful step is honest interaction behavior before a visual redesign.
P1-E2 is split into A (this run) and B (notification correctness, later).

## Paste this prompt

```text
Continue VEGA in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read AGENTS.md, README.md, docs/PHASE1_PLAN.md, docs/POLISHED_BETA_ROADMAP.md and the latest docs/IMPLEMENTATION_LOG.md entry. Recheck branch/HEAD and staged/unstaged/untracked baseline. Work only on P1-E2A: honest mutation/stale feedback, local onboarding, and narrowly related readiness presentation. Stop before native notification work, broad layout redesign, packaging or desktop acceptance.

Preserve all existing changes, .env, personal database/backups/WAL files, Electron profile and generated builds. No reset, clean, stash, stage, commit, push, real model calls/downloads or installer runs. Keep one editing agent. Use bounded read-only coding reviewers for frontend failure paths and onboarding/readiness when available; otherwise review sequentially and record it. Use synthetic data/temp profiles for tests.

Inspect frontend/src/components/ProductivityHub.jsx, SetupWizard.jsx, App.jsx, hooks/useChat.js, frontend tests, backend/main.py /health, scheduler.py and voice_service.py. Reuse current code and API contracts.

1. Honest mutations: task deletion and timer cancellation currently ignore HTTP failure. Audit the existing hub mutation paths for non-2xx, failed network, malformed success response and ambiguous timeout handling. Do not remove an entity or clear entered content as if saved unless the response confirms the result. If optimistic updates are retained, implement reliable rollback/error feedback and reconciliation. Add pending states per action to stop duplicate submissions, preserve typed input after failure, and avoid unrelated request refactoring. Do not automatically retry a timed-out write with a new idempotency key. Retry safely only through existing supported idempotency contracts; otherwise reconcile authoritative state and explain uncertainty.
2. Read freshness: tasks/hub/Today/projects can currently retain last data silently after network failure. Track loading, last successful refresh and stale/error per dataset. Keep cached information visible with an understandable stale label; never turn a failed load into an empty list or a 'nothing due' success. Clear stale state on successful recovery; prevent older responses overwriting newer state. Show errors accessibly without stealing focus on every background poll.
3. First-run onboarding: SetupWizard currently defaults to Gemini and persists that override. Offer useful deterministic-only operation without credentials or an installed model. Distinguish using backend configuration, choosing an already installed local model, and explicit cloud selection. Preserve existing user choices and private .env; no silent cloud fallback, credentials collection or automatic model installation. Check actual provider='none'/null behavior before designing deterministic-only selection: no-provider mode must refuse unfamiliar model-dependent requests truthfully without contacting the configured cloud provider. Supported deterministic commands must still execute. Implement the smallest explicit setting/contract if needed. Local metadata diagnostics are opt-in and bounded; installed model is not proof of inference quality or readiness. Do not label local mode fully ready before checking availability.
4. Readiness presentation corrections: scheduler._last_error and voice_service._voice_error currently reach /health as raw/truncated strings. Replace user-facing details with stable safe reasons; preserve diagnostics in appropriate local logs without displaying secrets/paths. Test sensitive-looking exception text across every exposed service. Voice failure/user-disabled state should not be labelled starting simply because its queue exists. Remove the duplicate is_voice_active definition only after checking callers. Align frontend retry timing with actual degraded status (current resolved degraded response still waits 15s), check malformed health payloads, and prove polling cleanup does not leave timers or requests alive. Keep /health legacy compatibility.
5. Add meaningful automated coverage for write rejection/rollback, ambiguous write timeout, pending/double submit, stale read then recovery, out-of-order refresh, health startup/outage/degraded recovery, no-provider deterministic success and no-provider unfamiliar refusal, preserved provider choice and explicit cloud opt-in. Existing 19 frontend tests cover chat storage and voice state, not App polling/components; do not reuse their count as evidence for new interactions. Prefer the existing Node test runner and focused testable helpers; use a rendered UI check with an isolated mock/temp backend if browser tools are available. Do not claim component/desktop testing from helper tests alone.
6. Run targeted tests, full backend tests (python -m pytest) if backend contracts change, npm test --prefix frontend, npm run lint --prefix frontend and git diff --check. A frontend production build is appropriate for JSX integration but must use only generated outputs authorized for this run and preserve prior artifacts; if preservation cannot be guaranteed, record the limitation. Report exact commands and fresh outcomes. Keep microphone, OS notifications, live providers and personal records unobserved during hermetic tests.

Update docs/IMPLEMENTATION_LOG.md with files, behavior, tests, skipped checks and remaining risks. Correct the earlier claim that polling/components had Node coverage if no corresponding tests existed; append an evidence correction rather than deleting history. Update current start/status docs from evidence. Stop after P1-E2A. Next is P1-E2B: controlled notification channel, browser/Electron permission handling and deduplication, then BETA-UI daily-work layout polish and later supervised Windows acceptance.
```

## Acceptance and stop point

Users can see whether data is saved, pending, stale or failed; failed requests
retain content and recover on refresh. Fresh setup works for supported commands
without choosing cloud. Health reports safe, accurate service state. Automated,
rendered browser and real desktop evidence are listed separately.

Do not add notifications or redesign every theme in this session. Return changed
files, exact tests, evidence limits and a precise P1-E2B handoff.
