# Antigravity prompt: P1-D1 recovery correction

Paste into Gemini 3.8 Flash in Antigravity with High reasoning:

```text
Continue VEGA AgentOS on main. Read AGENTS.md, docs/VEGA_AGENTOS_BRIEF.md, docs/PHASE1_PLAN.md, and the latest docs/IMPLEMENTATION_LOG.md entry. Inspect source and Git status first. Correct P1-D1 before starting P1-D2.

Preserve all existing changes, .env, the personal SQLite database, and builds. Use one editing agent and, if available, one bounded read-only reviewer for scheduler state invariants. Use only temporary databases and fake listeners; do not trigger desktop notifications.

Investigate backend/scheduler.py: the orphan-healing loops currently mark an expired active Timer or pending Reminder as missed whenever no pending alert exists. That condition also holds when the related alert is cancelled or delivered, or when no related alert ever existed. Reconcile an entity only when there is a matching missed ScheduledAlert for its current due time. Check how snoozed reminders, cancelled timers/reminders, delivered alerts, and missing alerts should behave; require exact entity and due-time linkage rather than treating absence of a pending alert as proof of a missed alert. Keep the fix small and preserve existing stale and due claim behavior.

Add focused tests for: a matching missed alert heals a legacy orphan; a cancelled alert does not heal; a delivered alert does not heal; an entity with no alert does not heal; and an old missed alert does not mutate a reminder snoozed to a later due time. Where the schema permits, test a genuine database reopen with a fresh engine/session factory, not only a new scheduler instance using the original SessionLocal. Verify repeat ticks produce no extra delivery receipts.

Then run the focused scheduler tests, full backend suite (python -m pytest), and git diff --check. Record exact commands and results in docs/IMPLEMENTATION_LOG.md. Distinguish database-state tests and fake-listener acceptance from actual desktop notification rendering, which remains unobserved. Stop after this P1-D1 correction; P1-D2 migration verification is next.
```
