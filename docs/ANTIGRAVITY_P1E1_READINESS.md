# Next run: P1-E1 readiness and fault isolation

Prepared 2 October 2026. Use Gemini 3.8 Flash with High reasoning.

Codex independently ran 26 migration tests successfully and verified genuine
early ALTER rollback. The full 391-test result remains agent-reported.
One prerequisite remains: validation accepts a partial unique idempotency index
`WHERE success=1`, then permits duplicate keys for failure receipts. The prompt
first closes that narrow edge case, then proceeds to readiness only if it passes.

## Paste this prompt

```text
Continue VEGA in D:\Projects\Jarvis_Dashboard\jarvis-dashboard. Read AGENTS.md, README.md, docs/PHASE1_PLAN.md, docs/POLISHED_BETA_ROADMAP.md and the latest docs/IMPLEMENTATION_LOG.md entry. Record current branch/HEAD and staged/unstaged/untracked baseline. Preserve all existing changes, .env, personal databases/backups/WAL files and generated builds. No reset, clean, stash, stage, commit, push, personal database access, desktop launches or paid services. Keep one editing agent; use bounded read-only coding reviewers for migration and readiness if available, otherwise review sequentially and report the limitation.

First close this bounded migration prerequisite:
- In a temporary current-v5 database replace the critical index with UNIQUE ix_action_receipts_idempotency_key ON action_receipts(idempotency_key) WHERE success=1. Current validation accepts it and permits duplicate failure keys. Check PRAGMA index_list's partial flag in both validation paths and the alternate-index fallback. A partial unique index must not satisfy unconditional model uniqueness. Reject contradictory definitions before writes; do not silently repair user indexes. Add regression coverage and prove normal unconditional indexes still work.
- Fix early rollback test evidence: shortening _TASK_COLUMNS_V1 currently makes schema prevalidation reject before ALTER; broad pytest.raises(Exception) accepts that. Keep the true migration column list, observe an ALTER executing, then inject a specific failure and compare schema/rows after engine disposal/reopen. For late DDL coverage, inject after actual table/index creation rather than before create_all.
- Run focused migration suites and git diff --check, recording fresh evidence in the log. Stop if the prerequisite fails or needs broader persistence design. Only after this gate passes begin P1-E1 below.

P1-E1 goal: report which services are usable, recover the connection UI after startup/outage, and prove provider failures do not prevent deterministic local work. Reuse the existing architecture.

1. Inspect backend/main.py lifespan and /health, providers/base.py/gemini.py/ollama.py, model_lane.py, voice_service.py, scheduler.py, frontend/src/App.jsx health polling and frontend/electron/main.js health consumers. Preserve legacy /health fields/semantics for Electron callers. Add a separate readiness endpoint only if a consumer needs it; avoid duplicate APIs.
2. Separate core service/DB readiness from optional provider, voice and scheduler state. Use truthful ready/starting/disabled/degraded/unavailable/unknown states with checked_at, safe reasons and last successful activity where relevant. Configured credentials or a provider object do not prove reachability. Track scheduler task/loop state, last tick and error. Do not expose secrets, personal content/paths or raw exception dumps. Deterministic-only operation must stay usable without a model.
3. Health/readiness GETs must not generate tokens, contact cloud providers, download/load models, initialize microphones, launch apps or mutate records. Prefer cached/internal evidence. Explicit optional diagnostics may use bounded local Ollama metadata checks; installed model availability is distinct from generation readiness. No .env changes, silent provider switching or automatic downloads.
4. Add bounded frontend status refresh/retry with HTTP checks, timeout, cleanup and stale/checking feedback. Recover when the backend starts after the renderer and after an outage. Keep UI work confined to existing service status; defer Today/Focus/Resume layout polish.
5. Verify request deadlines: Gemini _call receives timeout_s without applying it to the SDK; queue waits may exceed remaining budget. Use supported installed SDK request-timeout configuration where needed, verifying official documentation before choosing it. A post-call timeout check or detached thread still executing is insufficient. Bound retries and never execute a late proposal after expiry.
6. Prove unavailable/rate-limited/malformed/timed-out providers do not prevent deterministic task/timer/reminder commands or read-only productivity requests. Include a slow in-flight provider alongside a local command, not only immediately thrown errors. Check synchronous event-loop blocking and fix narrowly if necessary. Use fake clients, temporary DBs, synthetic clocks and injected launchers; no real inference. Failed/expired proposals must not mutate entities or create success receipts.
7. Test readiness without inference side effects, optional services disabled/unknown, DB probe failure, scheduler stopped/error state and UI startup/outage recovery. Run targeted API/provider/model tests, full backend suite (python -m pytest), frontend tests (npm test --prefix frontend), lint when frontend changes, and git diff --check. Use workspace-owned test temp if required and report exact commands. Never describe mocks as desktop evidence.

Update docs/IMPLEMENTATION_LOG.md with separate prerequisite and P1-E1 outcomes, files, contract examples, fresh test results, real SQLite/mocked/unobserved boundaries and remaining risks. Update current start/status docs from evidence. Stop after P1-E1; next is P1-E2 honest mutation/stale feedback, local onboarding and bounded notification correctness. BETA-UI and supervised Windows acceptance follow in separate sessions.
```

Return files changed, synthetic API examples, regression/failure evidence, exact
tests and remaining limits. If only the prerequisite finishes, say so explicitly.
API tests alone do not establish a polished Windows beta.
