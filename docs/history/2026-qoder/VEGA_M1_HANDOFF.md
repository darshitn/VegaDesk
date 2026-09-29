# VEGA M1 — Implementation Handoff

Milestone M1 only: a reliable, zero-recurring-cost daily assistant. Tasks, timers,
reminders, and focus sessions now work **offline** (no Ollama/Gemini) through one
command dispatcher, with persistent UTC storage, action receipts, idempotency,
date clarification, restart-safe alert reconciliation, and alerts that surface
while the overlay is hidden. M2–M5 are **not** started.

Status: **M1 functionally complete.** 74 backend tests pass; frontend lint (0 errors)
and production build pass. Manual-only checks (real mic, Windows OS notification
delivery, packaged installer) were **not** executed — see "Checks not run".

---

## Files changed

### New — backend
| File | Purpose |
| --- | --- |
| `backend/timeutil.py` (393) | Injectable clock (`SystemClock`/`FakeClock`), naive-UTC helpers, local rendering, week bounds, duration parsing, and `extract_when()` natural-language date/time resolution with clarification. |
| `backend/command_parser.py` (189) | Deterministic regex intent parser → `{intent, params}` / clarification / `None`. Wake-word prefixes stripped. |
| `backend/tools.py` (404) | Validated tool executor (`execute_intent`) + handlers for every intent. Idempotency replay, receipts, clarification/`ToolError` mapping, alert bookkeeping. |
| `backend/dispatcher.py` (34) | `dispatch_command()` = parse → execute. Single entry shared by `/chat` and `/api/assistant/command`. |
| `backend/scheduler.py` (169) | Single-owner `AlertScheduler`: atomic at-most-once claim, grace-window reconciliation, entity state transitions, receipts. |
| `backend/migrations.py` (88) | Versioned, idempotent, non-destructive migrations; backs up the DB file before the first ALTER. |
| `backend/tests/` (7 files, 1033) | conftest + `test_timeutil`, `test_command_parser`, `test_dispatcher_tools`, `test_scheduler`, `test_migrations`, `test_api`. |

### Modified — backend
| File | Change |
| --- | --- |
| `backend/db.py` (+202) | `Task` extended (deadline/subject/source/created/updated). New models: `Timer`, `Reminder`, `FocusSession`, `ScheduledAlert`, `ActionReceipt`, all with `to_dict()` (ISO-UTC). `JARVIS_DB_PATH` override for tests. Naive-UTC helpers. |
| `backend/main.py` (+320/−15) | Replaced `create_all` with `run_migrations`. Scheduler started in lifespan (single owner). Voice gated by `VEGA_DISABLE_VOICE`. `/chat` runs the dispatcher **before** fast-path/LLM. New endpoints: `/api/assistant/command`, `/api/tasks/{id}/complete`, `/api/timers`, `/api/reminders` (+`/snooze`), `/api/focus` (+`/start`,`/end`), `/api/alerts`, `/api/receipts`, `/api/hub/state`, and `WS /ws/alerts` (origin-validated). |
| `backend/requirements.txt` | Added `pytest`, `httpx` (dev/test). |

### Modified — frontend
| File | Change |
| --- | --- |
| `frontend/src/App.jsx` | Added `/ws/alerts` socket (owned in App, survives hide/show, auto-reconnect). Raises an OS `Notification` (the channel visible while hidden) plus an in-app toast fallback. Requests notification permission once. |
| `frontend/src/components/ProductivityHub.jsx` | Rewritten to add an "Active now" row (timers with live countdown + cancel, next reminder + snooze, focus session + start/end), task deadlines, idempotent complete (via `/complete`), and quick-add forms — all driven by `/api/hub/state` + the new endpoints. Tasks and notes behavior preserved. |

### Modified — repo
| File | Change |
| --- | --- |
| `.gitignore` | Added `*.db.bak-*` so migration backups are never committed. |

> **Pre-existing, untouched:** `README.md` and everything under `docs/` were already
> modified/untracked before this session. The README line "the features in them are
> not yet implemented" is now **stale for M1**; left as-is to avoid touching unrelated
> uncommitted work.

---

## Behavioral examples exercised (all 8 from the spec)

Verified end-to-end through the dispatcher with a `FakeClock` (Mon 2026-09-21 10:00 IST)
in `test_command_parser.py` + `test_dispatcher_tools.py`, and over HTTP in `test_api.py`:

1. **"Add a task: finish the DBMS assignment by Friday at 6 PM"** → task with
   `deadline_utc = 2026-09-25T12:30:00Z` (Fri 18:00 IST), `task_deadline` alert queued, receipt written.
2. **"What is due this week?"** → read-only `list_tasks` (week window), no receipt, ordered by deadline.
3. **"Set a timer for 25 minutes"** → `Timer` (1500s, `end_utc`, tz name) + `timer_due` alert.
4. **"Cancel my timer"** → cancels the single active timer; with >1 active it returns a
   clarification listing them and cancels nothing.
5. **"Remind me tomorrow at 7 PM to revise trees"** → `Reminder` `due_utc = 2026-09-22T13:30:00Z` + `reminder_due` alert.
6. **"Snooze that reminder for 10 minutes"** → pending reminder postpones from its due time
   (fired/overdue ones snooze from now); old alert cancelled, new one queued, `snooze_count` incremented.
7. **"Start a 45-minute focus session for DSA"** → `FocusSession` (2700s) + inner `Timer` + one `focus_end` alert; a second concurrent session is refused.
8. **"End my focus session; I finished recursion practice"** → session `completed`, `outcome_note`
   captured, inner timer + `focus_end` alert cancelled, elapsed minutes reported.

Non-commands (e.g. "open chrome", "what is the meaning of life") return `handled: False`
and fall through to the existing fast-path/LLM unchanged.

---

## Checks run and results

| Check | Command | Result |
| --- | --- | --- |
| Backend unit + API tests | `python -m pytest tests -q` (in `backend/`) | **74 passed**, 0 failed (2 deprecation warnings, unrelated). |
| Migration on synthetic old DB | `test_migrations.py` | Adds all 5 columns, preserves rows, writes `*.bak-*` backup, stamps version 1, rerun is a no-op, fresh-DB path safe. |
| Frontend lint | `npm run lint` (in `frontend/`) | **0 errors**, 16 warnings (all pre-existing React-Compiler advisories; the only one in new code is a benign `set-state-in-effect` note on the data-fetch effect, same pattern as the original component). |
| Frontend build | `npm run build` | **Succeeded** (electron main/preload + client bundle). |

### Checks NOT run (manual / environment-gated — not claimed as passing)
- **Real microphone + wake word** end-to-end ("Hey Jarvis" → transcript → dispatcher).
  Voice is disabled in tests (`VEGA_DISABLE_VOICE=1`); the wake→transcript→`/chat` path
  is code-wired and the dispatcher it feeds is fully tested, but live audio was not exercised.
- **Windows OS notification delivery** while the overlay is hidden. The renderer calls
  `new Notification(...)` on `/ws/alerts` messages; actual toast/Action-Center rendering
  needs a manual run of the Electron app.
- **Packaged installer / PyInstaller** behavior (DB-next-to-exe path, migration on a real
  user `jarvis.db`).
- **Live LLM providers** (Gemini/Ollama) — intentionally out of scope; M1 commands are offline.

To run the manual app check: start the backend (`python run.py` in `backend/`) and the
frontend (`npm run dev` in `frontend/`), then use the Productivity Hub or chat. Allow
notifications when prompted, hide the overlay (Ctrl+Space), and confirm a timer/reminder
alert raises an OS notification.

---

## Migration & rollback

- Migrations run automatically on backend start via `migrations.run_migrations(engine, Base, DB_PATH)`.
- They are **idempotent** and **non-destructive**: `schema_version` tracks state; existing
  `tasks` rows are preserved and only get new (nullable / defaulted) columns; `create_all`
  adds the brand-new tables.
- Before the first ALTER on an existing DB, the file is copied to `jarvis.db.bak-<YYYYMMDDHHMMSS>`.
- **Rollback:** stop the backend, copy the `.bak-<timestamp>` file back over `jarvis.db`,
  restart on the previous code. Backups are git-ignored.
- New installs (no `tasks` table) skip backup/ALTER and just create the full schema.
- Tests never touch the real DB: `conftest.py` points `JARVIS_DB_PATH` at a temp file before `db` import.

---

## Design notes / invariants

- **Time:** all `*_utc` columns are naive UTC; rendered to local (system tz = IST) for display.
  Conventions: date-only deadline → 23:59 local; bare "at 7pm" already past → tomorrow;
  year-less date → next occurrence; past/ambiguous dates → clarification, never a guess.
- **Idempotency:** `action_receipts.idempotency_key` is unique; a retry returns the stored
  receipt with `replayed: true` instead of re-executing. Concurrent same-key inserts fall
  back to a re-query.
- **Alerts:** single scheduler owner; delivery is at-most-once via an atomic
  `UPDATE ... WHERE status='pending'`. Alerts are only claimed when a `/ws/alerts` client is
  connected (otherwise they stay pending). Due-within-grace (default `VEGA_ALERT_GRACE_MIN=30`)
  are delivered once (prefixed "(missed while away)" if >60s late); older ones are marked
  `missed` silently with a `success=false` receipt — no stale bursts after restart.
- **Security:** no general shell execution, file deletion, or PC control was added. The only
  system actions remain the pre-existing allowlisted `open_app`/`open_website`.

---

## Unresolved issues / known gaps

- LLM **tool-calling** for these intents is intentionally not wired yet (spec: deterministic
  flows first). Model integration is the next step, reusing `tools.execute_intent`.
- `ProductivityHub` polls `/api/hub/state` every 5s (countdowns tick locally each 1s); a push
  refresh on `/ws/alerts` events would be a small efficiency win.
- Reminder quick-add uses a `datetime-local` picker; natural-language entry is via chat/voice only.
- The stale README "not yet implemented" line (see above).

## Next smallest milestone

Wire the **LLM tool-calling layer** to the existing executor: expose `create_task`,
`start_timer`, `create_reminder`, `start_focus_session` (etc.) as Gemini/Ollama tools that
call `tools.execute_intent` with an idempotency key, so free-form phrasing routes to the same
validated, receipted path the deterministic parser already uses. Keep the deterministic parser
as the fast/offline path in front of it.
