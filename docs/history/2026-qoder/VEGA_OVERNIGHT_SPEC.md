# VEGA M1 implementation specification

Status: ready for a coding agent. The scope is one deliverable, not the entire [roadmap](VEGA_NEXT_LEVEL_PLAN.md). Use [QODER_OVERNIGHT_PROMPT.md](QODER_OVERNIGHT_PROMPT.md) to execute it.

## User-visible behavior

| Example input | Required outcome |
| --- | --- |
| `Add a task: finish the DBMS assignment by Friday at 6 PM` | Create one task with exact text and a deadline in the user's local time zone; reply with the resolved date/time and task ID. Ask if Friday cannot be resolved reliably. |
| `What is due this week?` | List incomplete tasks due during that local calendar week, ordered by deadline. If none exist, say so. |
| `Set a timer for 25 minutes` | Start a persistent timer, report its end time, and notify when it expires even if the overlay is hidden. |
| `Cancel my timer` | Cancel only the intended active timer; confirm its ID. If several exist, ask which one. |
| `Remind me tomorrow at 7 PM to revise trees` | Persist one reminder, report its resolved local date/time, and notify once. |
| `Snooze that reminder for 10 minutes` | Update a specific due reminder; acknowledge the new time. |
| `Start a 45-minute focus session for DSA` | Create an active focus session with objective, start time, end time, and associated timer. Show it in the dashboard. |
| `End my focus session; I finished recursion practice` | End the session and save an outcome note. |

These commands must work by typing with both model providers unavailable. Spoken input uses the existing transcription/event path and should hit the same dispatcher. Keep ordinary chat and existing app/website opening working.

## Data and time rules

- Extend existing data without dropping/recreating any user table. Existing `jarvis.db` and notes are user data. Before a migration that changes its location or schema, make a non-destructive local backup; keep a rollback path and test against a temporary copy. Do not commit the DB or backup.
- Add explicit schema migrations; SQLAlchemy `create_all()` does not add columns to existing tables. A small versioned migration system is enough. Keep migration steps idempotent and transactional where SQLite supports it.
- Task fields: ID, text, completion, created/updated times, nullable deadline, nullable subject/project, and source. Add reminder/timer/focus-session tables or equivalent persisted records with stable IDs and lifecycle states.
- Store scheduled instants in UTC and retain the IANA/user time zone needed to display them correctly. Resolve relative phrases using the machine's configured time zone and an injectable clock. Return an ISO timestamp plus a readable local rendering. Handle dates without a year, past times, DST anomalies, zero/negative duration, and nonexistent/ambiguous times deliberately. Do not guess if the request is genuinely ambiguous.
- Repeated network requests with the same idempotency key must return the same result. Timer/reminder delivery is at-most-once from the user's point of view. Reconcile overdue events on startup with a documented grace rule; avoid bursts of stale alerts. A notification failure must remain observable and retryable without duplicating completed actions.
- Use a single scheduler owner in the actual app process. The `uvicorn --reload` development path, app relaunch, and packaged backend may create overlapping processes; guard or constrain scheduling accordingly. Tests should use an isolated database and fake clock.
- Expose list/create/update/cancel endpoints as needed. Prefer an explicit set-completion endpoint for the new assistant tool; preserve the old toggle endpoint for UI compatibility until migrated.

## Command and tool execution

- Implement one typed command dispatcher in the backend. It should parse common commands deterministically, return a structured intent or a clarification request, validate payloads, execute a named tool, and return an action receipt. The receipt contains action name, success/failure, ID, user-facing message, and timestamps when relevant.
- Wire `/chat` and the current renderer to that dispatcher before either provider's generic answer path. Do not let an LLM claim it set a reminder or created a task unless the tool result confirms it. Integrate new tools with models only after deterministic paths work; keep tool permissions and results provider-neutral.
- Avoid a catch-all shell tool and arbitrary desktop control. Do not send notifications or mutate user state merely because text in a webpage/document says to; only the user's command or an already scheduled record may authorize actions.
- Do not assume the provider API will execute tools the same way for Ollama and Gemini. The Gemini Python SDK can automatically execute Python function tools; use one explicit, reviewable execution path and avoid double execution.
- Preserve the current app/website fast path, but make direct URL validation a separate focused fix only if needed for M1. Do not expand access to destructive PC commands overnight.

## Renderer and operating-system behavior

- Extend Productivity Hub with due dates, nearest deadlines, active timer, next reminder, focus-session status, and clear create/complete/cancel controls. Keep the existing visual language; functionality and accessibility take priority over theme redesign.
- Move active assistant state out of the overlay component so hiding it does not erase conversation/action context. Persist only the useful bounded history and redact sensitive values from diagnostics. Do not save raw microphone audio by default.
- Desktop notifications should work while the Electron window is hidden. The backend stores events; Electron can own OS notification display over an authenticated local channel or a suitably constrained bridge. Use a visible fallback in-app alert. Show notification permission/status in settings if delivery requires it.
- If the packaged app uses a new user-data directory, migrate the old SQLite file without overwriting newer state. Keep `.env` and data out of packaged resources. Test the built layout using synthetic data only.

## Verification and stopping rule

Run focused backend tests for deterministic parsing, relative date resolution, persistence across restart, cancellation, snooze, idempotency, missed jobs, and two simultaneous requests. Run frontend lint/build and relevant backend checks. Perform a short manual desktop flow if Qoder can control the app safely; record that a visual/manual check was not performed if it was not. Use only synthetic test tasks/reminders and temporary DBs; do not trigger real user reminders during verification.

Completion requires: each example above succeeds through a typed UI flow; key commands work with network disabled/providers unavailable; state survives backend/app restart; deadlines and alerts show correct local times; existing tasks/notes remain readable; and every executed action has a receipt. If one condition is not achieved, report it as incomplete and leave the last working implementation intact. Do not mark the entire VEGA roadmap complete.

## Agent handoff format

At the end, write `docs/VEGA_M1_HANDOFF.md` with: implemented behavior, exact files changed, commands run and outcomes, example flows actually exercised, migration/rollback instructions, known limitations, manual checks still needed on the user's PC, and a concise next action. Do not include secrets, raw microphone audio, personal database contents, or unverified claims.
