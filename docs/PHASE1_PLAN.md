# Phase 1 — core foundation plan

Status: implementation guide; current source must be checked at each session. Do not mark an item complete because a historical handoff says so.

## First assessment

1. Capture Git status, current branch/HEAD, local changes, and the commands that run backend/frontend tests. Preserve the dirty baseline.
2. Trace `/chat` and `/api/assistant/command` through `backend/main.py`, `dispatcher.py`, `tool_registry.py`, `tools.py`, `system_actions.py`, `db.py`, `scheduler.py`, and `providers/`. Record where policy, verification, and audit actually occur.
3. Map existing endpoints, tool names, schemas, storage tables/migrations, and dependencies to the brief. Identify reused, modified, and deferred parts. Inspect tests for evidence quality.
4. Write the assessment in `docs/IMPLEMENTATION_LOG.md`: current structure, actual data flow, core interfaces, dependency reasons, gaps, security risks, and a bounded first edit.

## Milestones

| Stage | Result | Acceptance evidence |
| --- | --- | --- |
| P1-A | Repository-grounded architecture and gap assessment | Source paths and tests cited; existing work preserved. |
| P1-B | Smallest safe vertical slice through registry, policy, executor, verifier, audit, and response | Start with existing `open_website`/`system.open_url` behavior or a read-only system-info tool; choose whichever can be safely integrated without rewriting. Unit/API test shows permitted action, denial, failure, and verified receipt. |
| P1-C | Consistent risk policy and confirmation contract across existing tools | Typed classifications, scoped approvals, fail-closed invalid inputs, tests including untrusted proposal attempts. |
| P1-D | Persistence and scheduler recovery | Additive migration, test with temporary SQLite database, restart/idempotency cases. |
| P1-E | Service/provider contracts and documentation | Health and representative API integration checks; provider failures do not block deterministic commands. |

Implement only one row per run unless it is finished and verified and the next row is plainly safe. The plan can be revised with evidence. Do not duplicate a feature already implemented merely to match a proposed module name.

## Design questions to answer before P1-B edits

- Does `tool_registry.py` validate only model proposals, or all commands? Where can an action bypass it?
- Which operations currently have a risk level, explicit policy decision, and denial audit? Are confirmations bound to the exact target/action and expiration?
- What proves `open_website`/`open_app` actually opened, and how should failures be reported? Avoid a false success when OS launch succeeds but target verification is unavailable.
- Are retries and idempotency safe across HTTP, voice, scheduler, and provider paths?
- Which SQLite records are authoritative for a reminder after restart? Is there a single scheduler owner in development and packaged runs?
- Can `/health` truthfully distinguish a live service from optional model/voice/scheduler readiness?

## Quality gate

For each stage: inspect targeted source and tests, implement the smallest cohesive change, run targeted tests and any relevant integration test, inspect the diff, run `git diff --check`, and update the log. Verify Windows-only behavior on a real desktop before claiming it. Note skipped checks with reasons.
