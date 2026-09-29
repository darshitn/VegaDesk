# Antigravity prompt: P1-D1 scheduler recovery

Paste the following into Gemini 3.8 Flash in Antigravity with High reasoning:

```text
Continue VEGA AgentOS on branch main. Read AGENTS.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md entry. Inspect current source and Git status before trusting the handoff. Work on P1-D1 only: scheduler restart recovery, stale alert entity state, and serial idempotency. Stop before broader persistence redesign, new reminder features, Risk 2/3 confirmation UI/API, or desktop automation.

Preserve every existing change, .env, the local user database, and build artifacts. Do not reset, clean, stash, commit, push, or use the personal database for tests. Use one editing agent. If subagents are available, give one bounded read-only review of scheduler/entity state semantics and another bounded read-only review of persistence and tests; require file/line evidence and uncertainties. Treat their output as leads and verify it yourself.

The specific gap to investigate: backend/scheduler.py marks an expired ScheduledAlert as missed and writes a failure delivery receipt, but its Timer can remain active or Reminder pending. Compare this with the delivered-alert path and inspect all API/UI consumers of these statuses. Define and implement a consistent terminal state for an expired timer/reminder. Preserve truthful semantics: never claim a desktop notification was delivered when an alert only aged out. Keep task deadlines and focus-session behavior unchanged unless a source-backed invariant requires a narrowly scoped fix.

Use the smallest cohesive change. Make transitions and receipt writes idempotent across repeated tick calls and process/database reopen. Keep the stale-alert claim, related entity update, and delivery receipt transactionally coherent. Check cancellation and snooze behavior so an old alert cannot alter the current entity. Avoid a schema migration unless source inspection proves it necessary; if one is needed, make it additive and test upgrade from a temporary older schema.

Test with temporary SQLite databases, a synthetic clock, and a fake notification listener. Include both timer and reminder cases: due alert with listener, due alert without listener, expiry beyond grace, restart then tick, repeated tick without duplicate receipts, cancellation/snooze interaction, and unaffected task/focus states where relevant. Do not trigger real desktop notifications. Run targeted tests, the full backend suite (python -m pytest), and git diff --check. If the standard temp directory is unavailable, use a workspace-owned test temp directory and report that substitution.

Update docs/IMPLEMENTATION_LOG.md with exact commands/results, the chosen status semantics, files changed, remaining risks, and a clear distinction between SQLite integration evidence, mocked notification delivery, and unobserved desktop behavior. Inspect the final diff for unrelated changes and secrets. Stop after P1-D1 and give one precise next milestone.
```
