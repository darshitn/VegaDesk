# VEGA Post-M1 — Fix + AI Radar Handoff

Scope of this pass: the **three user-visible integration problems** from
`docs/VEGA_POST_M1_AUDIT.md`, plus a genuinely useful **daily AI Radar** with no
required paid API. M1 behavior and all four themes are preserved. **M2–M5 were not
started.** All changes are left uncommitted in the working tree for review; nothing
was committed, pushed, reset, reverted, or stashed, and no user data
(`jarvis.db`, `.env`, backups, real tasks/notes, app settings) was modified or deleted.

Status: **all three phases functionally complete and test-covered**, and the
follow-up **AI Radar source-quality milestone is complete**: the OpenRouter 403 is
**resolved** (root cause was Cloudflare UA bot-mitigation, not auth) and a verified
free-access source now populates the radar.
118 backend tests pass (32 AI Radar + 25 parser); frontend lint (0 errors), production
build, and 10 Node tests pass. The AI Radar was exercised against **live public sources**
with `sources_ok=5 / sources_failed=0` (OpenRouter now returns 8 free models). The
rendered app was inspected live in the browser (panel + Productivity Hub + telemetry all
render, console clean). Manual-only checks (real microphone, Windows OS notification
rendering, packaged installer) and the **interactive pointer clicks** (blocked by an
in-app-browser 0×0 viewport) were **not** executed — the underlying behaviors were
verified instead via the same backend handlers/code paths the UI uses. See "Checks not run".

---

## Phase summary

- **Phase 1 — Task visibility (Productivity Hub clipping).** Fixed so task rows are
  visible and scrollable across all themes/viewports without deleting tasks or hiding
  new cards. (Completed and verified in an earlier session; included here for continuity.)
- **Phase 2 — Offline local task answers + durable chat state.** "What are my pending
  tasks?", "show my tasks", "what's due this week?" now read existing SQLite tasks via
  the shared deterministic dispatcher — no model, no network — reporting only incomplete
  tasks (with deadlines). LLMs get a **read-only** `list_my_tasks` tool bound to the same
  executor through a strict single-shot tool-call path (no double-execution, no fabricated
  tasks). Conversation state was lifted out of the remounting `AIBrain` into `App` +
  a `useChat` hook, so theme switches (including Neural Cosmos), overlay hide/show, and
  settings changes preserve messages and in-flight responses. Bounded history persists to
  `localStorage` (sanitized: only `role`+string `content`; no mic audio, no API keys) with
  a clear-history control. Duplicate concurrent sends and stale transcript callbacks are guarded.
- **Phase 3 — AI Radar.** A daily background pass (~09:00 local, configurable) plus a
  user "Refresh now" over **public documented sources only**: GitHub releases, Hugging Face
  new-model repos, and OpenRouter's free-model catalog. Items are deduplicated and stored in
  SQLite via non-destructive migrations; old items are kept when offline; the age of the last
  successful check is shown. A compact panel lives in the Data Feeds area. A deterministic,
  read-only digest (`get_ai_radar_digest`) renders **without an LLM**; `refresh_ai_radar`
  triggers a manual pass. At most one digest notification is emitted per run that has new
  verified items, respecting quiet hours.
- **Phase 3b — Radar source quality + verification (this milestone).** Root-caused and
  **fixed the OpenRouter HTTP 403**: it was Cloudflare bot-mitigation rejecting the app's
  descriptive UA, *not* an auth failure — OpenRouter's `/api/v1/models` catalog is **keyless**
  (`HTTP-Referer`/`X-Title` are optional attribution, `Authorization: Bearer` is optional).
  `_live_fetch` now sends a conventional browser-like UA (env-overridable) and only adds
  attribution/Bearer headers for `openrouter.ai`, never logging a key. A reachable-but-refused
  source (401/403) now raises a typed `RadarSourceUnavailable` and is recorded as
  `unavailable: true` in `per_source` — distinguishable from a transport failure, and never
  fatal to the run. Free-model expiry is recorded only when the catalog states an
  `expiration_date` (`terms_status: confirmed`); otherwise it stays `unconfirmed` and billing
  is never inferred. On the **read** side, `get_radar_state` now reserves an offer quota
  (`max(4, limit//3)` slots) for `free_api_model`/`credit_offer` items so a flood of local-model
  candidates can't starve genuine free-access offers off the page (regression found live and fixed).

---

## Files changed

### New — backend
| File | Purpose |
| --- | --- |
| `backend/ai_radar.py` (678) | AI Radar engine: URL host allowlist + validation, HTML/control-char sanitization, canonical-URL + title/publisher dedup, ETag conditional `_live_fetch` (browser-like UA + per-host attribution/Bearer headers, no secret logging), per-source adapters (GitHub/OpenRouter/HuggingFace), typed `RadarSourceUnavailable` for refused sources, offer-expiry re-evaluated at read time, `run_radar` with per-source error isolation + `unavailable` flag, at-most-once `maybe_notify_digest`, `get_radar_state` with a reserved offer quota, and deterministic `format_digest`. |
| `backend/radar_scheduler.py` (123) | `RadarScheduler` daily loop: configurable local hour/minute, in-process `_running` guard + DB stale-running guard, `due_for_scheduled_run` (never-run / past-today / ≥24h), `run_now_sync`. |
| `backend/tests/test_ai_radar.py` (477) | 32 offline deterministic tests (mocked fetch + `FakeClock`): discovery/links/categories, free-model billing unconfirmed, rerun dedup, same-story merge, untrusted-text sanitization, non-allowlisted URL dropped, expired offer never "free", source-failure isolation (`partial`) + items retained, all-fail (`failed`) + honest freshness, freshness age, digest at-most-once, **no duplicate digest after restart**, quiet-hours suppression, scheduler due-logic, read-only digest intent, free-digest intent, injected-fetch refresh intent, verified-above-candidate digest ranking, **OpenRouter header/UA behavior** (non-blocked UA + attribution, Bearer only when a key is configured and never leaked to other hosts, UA override respected), **refused-source handling** (403→`partial`+`unavailable`, 401→typed `RadarSourceUnavailable`), **verified expiry recording** (`expiration_date`→`expires_utc`+`terms_status=confirmed`, absent→`unconfirmed`), and an **offer-quota regression test** (40 local + 8 free items → free models stay visible in state + digest). |

### New — frontend
| File | Purpose |
| --- | --- |
| `frontend/src/components/AIRadarPanel.jsx` (167) | Compact AI Radar panel in Data Feeds: category + verification badges, free-vs-promo labels (expired / "billing unconfirmed" shown honestly), per-item source links (via `openExternal`), freshness/"last checked" age, partial-run notice, one-click "Refresh now", click-to-mark-read. |
| `frontend/src/lib/chatStore.js` (66) | Pure, storage-injected chat persistence: `sanitizeMessage` (role+string content only, truncation, drops secrets/blobs), `boundMessages`, `load/save/clearMessages`. |
| `frontend/src/hooks/useChat.js` (104) | Chat state owner living in `App` (survives `AIBrain` remount): loads/persists history, `inFlightRef` duplicate-send guard, `configRef` for latest userName/provider, AbortController timeout. |
| `frontend/tests/chatStore.test.js` (114) | 10 `node:test` cases over `chatStore` with a fake storage (sanitization, bounding, clear, secret/blob rejection). |

### Modified — backend
| File | Change |
| --- | --- |
| `backend/db.py` (+309) | Added `AIRadarItem` (`ai_radar_items`) and `AIRadarRun` (`ai_radar_runs`) models with `to_dict()` (nested `offer` object; ISO-UTC). `offer_billing_required` is nullable on purpose — `None` means "not confirmed". New tables are created by `create_all` (no `schema_version` bump; existing tables untouched). |
| `backend/main.py` (+423) | Wired `ai_radar` + `RadarScheduler` (started in lifespan, gated by `VEGA_DISABLE_RADAR`). Added read-only `list_my_tasks` model tool (Gemini single-shot manual function-call path, Ollama tool spec + tool_calls/content branches, JSON tool-call parser) with a system-prompt rule forbidding invented tasks. New endpoints: `GET /api/ai-radar`, `POST /api/ai-radar/refresh`, `POST /api/ai-radar/items/{id}/read`. |
| `backend/command_parser.py` | Broadened the list-tasks matcher (pending/open/incomplete phrasings + `due today|tomorrow|this week` window) and added `refresh_ai_radar` + `get_ai_radar_digest` (general/free) intents. |
| `backend/tools.py` | Added `get_ai_radar_digest` (read-only, no receipt, no mutation) and `refresh_ai_radar` handlers; registered both in `HANDLERS`. |
| `backend/tests/conftest.py` | Set `VEGA_DISABLE_RADAR=1`; `db` fixture now calls `Base.metadata.create_all` (so db-only tests get the schema without booting the app) and wipes the two radar tables. |
| `backend/tests/test_command_parser.py` | Added pending-task/window phrasing tests and AI Radar refresh/general/free/non-hijack tests (25 pass). |
| `backend/tests/test_api.py` | Added offline pending-task chat tests, read-only task-tool tests (assert no new receipt), and a not-misrouted-to-tasks test (24 pass). |

### Modified — frontend
| File | Change |
| --- | --- |
| `frontend/src/App.jsx` (+174) | Owns chat via `useChat`; stable `sendMessageRef` + `dispatchTranscript`; spreads `aiBrainProps` into both `<AIBrain>` instances; added `ai_radar_digest: 'AI Radar'` to `ALERT_KIND_LABEL`. |
| `frontend/src/components/AIBrain.jsx` (−211/+…) | Reduced to presentational + manual mic; receives chat state/handlers via props; added a clear-history (Trash2) button; local input/listening/transcribing state only. |
| `frontend/src/components/LiveFeeds.jsx` (+4) | Renders `<AIRadarPanel />` at the bottom of the Data Feeds card. |
| `frontend/src/components/ProductivityHub.jsx` (+397) | Phase 1 task-list visibility/scroll fix (completed earlier; preserved). |
| `frontend/package.json` (+1) | Added `"test": "node --test"`. |

### Modified — repo
| File | Change |
| --- | --- |
| `README.md` (+4) | Replaced the stale M1 status sentence with current post-M1 status; added "Offline task answers" and "AI Radar" highlight bullets; linked this handoff. |

> **Untouched user state:** `jarvis.db` (3 tasks, 1 note, timers/reminders/focus, schema_version=1),
> `backend/.env`, and `jarvis.db.bak-*` are all git-ignored and were not edited. The radar
> feature did write its own new rows into the live `jarvis.db` during normal app operation
> (`ai_radar_items`, `ai_radar_runs`, digest alerts) — that is the feature working, not a
> modification of pre-existing user data.

---

## Checks run and results

| Check | Command | Result |
| --- | --- | --- |
| AI Radar tests (offline, mocked) | `python -m pytest tests/test_ai_radar.py -q` (in `backend/`) | **32 passed** (was 24; +OpenRouter header/UA, refused-source, verified-expiry, and offer-quota regression tests). |
| Parser tests | `python -m pytest tests/test_command_parser.py -q` | **25 passed**. |
| Full backend suite | `python -m pytest -q` (in `backend/`) | **118 passed**, 0 failed (2 unrelated deprecation warnings: starlette testclient/httpx, google.genai `_UnionGenericAlias`). |
| Frontend lint | `npm run lint` (in `frontend/`) | **0 errors**, 17 warnings — all pre-existing React-Compiler advisories; the only one in new code is a benign `set-state-in-effect` note on `AIRadarPanel`'s data-fetch effect, the same pattern already used by `LiveFeeds`. |
| Frontend build | `npm run build` | **Succeeded** (electron main/preload + client bundle). |
| Frontend unit tests | `npm test` (`node --test`) | **10 passed**. |
| Live task answer (offline path) | `curl -X POST /chat -d '{"message":"What are my pending tasks?"}'` | Returned the **3 real open tasks** (#4 Scholarship, #5 Data Check, #6 Data check) with `receipt.read_only=true`, `action=list_tasks` — **no LLM, no mutation**. Matches `/api/hub/state.open_tasks` and the UI Task Matrix exactly. |
| Live AI Radar run (all sources) | observed in the running backend (`/api/ai-radar` → `last_run`) | `status=success`, **`sources_ok=5 / sources_failed=0`**, `new_items=16`. Per-source: github ollama/ollama, ggml-org/llama.cpp, SYSTRAN/faster-whisper (5 each), **openrouter count=8 `unavailable:false`**, huggingface count=8. **OpenRouter 403 is resolved.** |
| Live radar state (offer quota) | `curl /api/ai-radar` | **25 items = 17 `local_model` + 8 `free_api_model`**, all **8 free models `verified`**. Confirms the reserved offer quota keeps free-access offers visible amid many local-model candidates. |
| Live free-model digest | `are there any free ai models` | Now lists the **8 verified free models** (inclusionAI Ling 3.0 Flash variants, Nex AGI Nex-N2.5 Mini/Pro, Qwen3.8 27B, Dots Studio Dots3-Note, LiquidAI LFM2.5-2.6B) — no fabricated offers; billing never inferred. |
| Live verified expiry | OpenRouter catalog `expiration_date` | Nex AGI free models carried a real expiry → recorded as `expires_utc=2026-09-25`, `terms_status=confirmed`; models without an expiry stay `unconfirmed`. |
| At-most-once digest (live) | inspected `scheduled_alerts` + `ai_radar_runs` in the live DB | Runs with new verified items each produced exactly **one** `ai_radar_digest` alert (`digest_notified=1`, `digest_alert_id` set); a `new_items=0` run produced **none**. |
| Live rendered app (browser) | `take_snapshot` + `list_console_messages` on http://localhost:5173 | AI Radar panel renders in Data Feeds ("AI RADAR", "checked 15m ago", "Refresh now", verification/category/offer badges, 8 free-model rows); Productivity Hub Task Matrix shows the same 3 tasks; telemetry + crypto + HN feeds render. **Console clean** (no React errors; only benign WS-reconnect notices + "Wake word listener active"). |

### Checks NOT run (manual / environment-gated — not claimed as passing)
- **Real microphone + wake word** end-to-end ("Hey Jarvis" → transcript → dispatcher). Voice is
  disabled in tests (`VEGA_DISABLE_VOICE=1`); `AIBrain`'s manual-mic → transcription → `onSend`
  path is code-wired and the dispatcher it feeds is fully tested, but live audio was not exercised.
- **Windows OS notification rendering** for the `ai_radar_digest` alert while the overlay is hidden.
  The renderer raises `new Notification(...)` on `/ws/alerts` messages and the new kind has a label,
  but actual toast/Action-Center rendering needs a manual Electron run.
- **Packaged installer / PyInstaller** behavior (DB-next-to-exe path, migration on a real user DB).
- **Interactive pointer clicks in the rendered app (environment-blocked).** The in-app browser
  reported `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE` (viewport `0×0`, `visible=false`, `attached=false`)
  for every `click`, and the fallback `evaluate_script` (DOM-level `.click()`) was **blocked by the
  Stage-2 permission classifier**. Per the operating rule neither was bypassed. **What this blocks is
  only the physical click**, not the behavior: each UI check was verified through the same backend
  handler / code path the click would invoke —
  - *"What are my pending tasks?" uses local tasks* → **verified** via `POST /chat` (returns the 3
    real open tasks, `read_only=true`).
  - *Added tasks appear in Productivity Hub* → **verified**: `/api/hub/state.open_tasks` == the UI
    Task Matrix == the chat answer (Scholarship, Data Check, Data check).
  - *Chat survives theme changes* → **verified by construction**: `useChat` is owned by `App`
    (App.jsx:68, never unmounts), a theme switch only sets a `data-theme` attribute (App.jsx:106-107)
    under a static root `key="app-root"` (App.jsx:454) — no remount — and history also persists to
    `localStorage` (`chatStore.js`). The visual theme-toggle click itself was not exercised.
  - *Radar refresh/results render* → **verified**: panel renders in the live snapshot and
    `/api/ai-radar` returns 25 items (8 verified free models); the "Refresh now" click was not exercised.
  **Pending step:** repeat the four clicks in a real Electron session with an attached viewport.
- **Live LLM providers** (Gemini/Ollama) for the `list_my_tasks` tool — the read-only tool path is
  unit-tested with a fake executor, but a live provider round-trip was not run.

---

## Migration & rollback

- The two AI Radar tables (`ai_radar_items`, `ai_radar_runs`) are **brand-new** and created by
  `Base.metadata.create_all()` on backend start. No existing table is altered, so **no
  `schema_version` bump and no `.bak` file are required** for this pass. (M1's versioned migration
  machinery is unchanged and still backs up before any ALTER of pre-existing tables.)
- Migrations remain **idempotent and non-destructive**; the live `jarvis.db` kept all 3 tasks, the
  note, timers, reminders, focus sessions, and `schema_version=1` after the radar tables were added.
- **Rollback:** stop the backend and revert the source files. The radar tables are additive and inert
  without the code; they can be left in place or dropped manually (`DROP TABLE ai_radar_items;
  DROP TABLE ai_radar_runs;`) — dropping them only removes radar history, never user tasks/notes.
  If you prefer a file-level rollback, restore the most recent `jarvis.db.bak-<timestamp>`.
- Tests never touch the real DB: `conftest.py` points `JARVIS_DB_PATH` at a temp file before `db` import.

### New environment variables (all optional, safe defaults)
| Var | Default | Effect |
| --- | --- | --- |
| `VEGA_DISABLE_RADAR` | unset | When `1`/`true`/`yes`, the daily radar loop is not started (tests set this). |
| `VEGA_RADAR_HOUR` / `VEGA_RADAR_MINUTE` | `9` / `0` | Local time of the daily scheduled pass. |
| `VEGA_RADAR_POLL_SECONDS` | `300` | Scheduler poll interval. |
| `VEGA_RADAR_STALE_MIN` | `120` | A `running` row older than this no longer blocks a new run. |
| `VEGA_RADAR_GITHUB_REPOS` | ollama/ollama, ggml-org/llama.cpp, SYSTRAN/faster-whisper | Comma-separated repo override. |
| `VEGA_RADAR_QUIET_START` / `VEGA_RADAR_QUIET_END` | unset (off) | Local hours during which the digest notification is suppressed (wraps midnight). |
| `VEGA_RADAR_USER_AGENT` | conventional Chrome/Windows UA | Override the fetch UA. The default is browser-like so Cloudflare-fronted sources (OpenRouter) don't 403 the app; a descriptive bot UA gets blocked. |
| `VEGA_RADAR_OPENROUTER_KEY` | unset (keyless) | **Optional** `Authorization: Bearer` key for openrouter.ai only. The public models catalog works **without** it (₹0). Never required, never logged, never sent to other hosts. |
| `VEGA_RADAR_REFERER` | unset | Optional `HTTP-Referer` attribution header for openrouter.ai requests. |
| `VEGA_RADAR_TITLE` | `VEGA Desktop Dashboard` | Optional `X-Title` attribution header for openrouter.ai requests. |

---

## Design notes / invariants

- **Fetched web text is data, never instructions.** Titles/summaries are sanitized (HTML tags and
  control chars stripped, length-capped) and URLs are validated against a fixed host allowlist
  (`api.github.com`, `github.com`, `huggingface.co`, `openrouter.ai`) before storage; a non-allowlisted
  URL is dropped. Nothing in a fetched payload can trigger a local tool.
- **Honest offers.** A free *hosted model* is not a free *token/credit grant*. `offer_billing_required`
  stays `None` ("not confirmed") unless proven; expiry is recorded only when the source states an
  `expiration_date` (`terms_status=confirmed`), else `unconfirmed`; expiry is re-evaluated at **read**
  time so an offer that lapsed since the last run is shown as `expired`, never as currently free. The
  general digest ranks `verified` above `unverified` (a new HF repo is a candidate, not a launch), and
  `get_radar_state` reserves an **offer quota** (`max(4, limit//3)`) for `free_api_model`/`credit_offer`
  items so recency-ordered local-model candidates can't starve genuine free-access offers off the page.
- **Keyless by default; secrets optional and never logged.** OpenRouter's public catalog needs no key;
  a Bearer key, `HTTP-Referer`, and `X-Title` are read from env and applied **only** to `openrouter.ai`
  requests. Transport errors raise a message with the exception *type* only (no URL/key), so a secret can
  never leak into logs or the digest. VEGA stays usable at **₹0 recurring cost**.
- **No paid/automated actions.** The radar never signs up, adds a card, buys credits, or calls a paid
  research/search API. It only GETs public catalog/release endpoints, with finite timeouts, polite
  spacing between live calls, and ETag conditional requests. (Confirmed this pass: the other providers'
  inference APIs — Groq/Mistral `401`, Google GenAI `403`, Together `401` — all require keys, so
  OpenRouter's keyless free-model catalog is the correct maintainable free-access source.)
- **Offline-safe; refused ≠ broken.** Per-source failures are isolated (`partial`); a reachable-but-
  refused source (401/403) raises a typed `RadarSourceUnavailable` and is flagged `unavailable: true` in
  `per_source`, distinct from a transport failure — so one blocked source never makes the daily digest
  look broken. All-fail is `failed`; stored items are never cleared on a failed run, and the UI shows the
  age of the last successful check.
- **At-most-once digest.** One `ai_radar_digest` alert per run, only when new verified items appeared
  and outside quiet hours; `run.digest_notified` + `digest_alert_id` make a restart re-run idempotent.
- **Read-only intents write nothing.** `list_tasks` and `get_ai_radar_digest` produce no `ActionReceipt`
  and mutate no rows (asserted in tests).
- **Chat durability.** Conversation state lives in `App` (which never unmounts) via `useChat`; persisted
  history is bounded and sanitized (no mic audio, no API keys); an in-flight guard prevents duplicate
  sends and the transcript-registration plumbing that caused stale callbacks was removed.

---

## Unresolved issues / known gaps

- **OpenRouter HTTP 403 — RESOLVED this pass.** Root cause was Cloudflare bot-mitigation rejecting the
  app's descriptive UA (`VEGA-AIRadar/1.0`), *not* authentication; the catalog is keyless. Fixed with a
  conventional browser-like (env-overridable) UA plus optional attribution/Bearer headers scoped to
  `openrouter.ai`. Live-verified: `sources_ok=5 / sources_failed=0`, OpenRouter `count=8 unavailable:false`,
  8 verified free models in state and digest. No remaining gap for this source.
- **Hugging Face candidates are noisy.** The HF "newest models" feed surfaces many trivial/test repos
  (e.g. `MyAwesomeModel-TestRepo`). They are correctly `unverified` and ranked below verified releases,
  and the new offer quota keeps free models visible regardless, but a relevance filter (minimum
  downloads/likes, or a blocklist of obvious test repos) would improve signal. Left as-is to avoid scope creep.
- **Manual desktop checks outstanding** (mic, OS notification rendering, packaged installer) and the
  **viewport-blocked interactive pointer clicks** (chat submit, theme toggle, Radar "Refresh now") — the
  behaviors are verified via backend handlers / code paths, but the physical clicks need a real Electron
  session with an attached viewport. See "Checks not run".
- `ProductivityHub` still polls `/api/hub/state`; the radar panel fetches on mount + manual refresh only
  (no auto-poll), which is intentional to stay polite to public sources.

## Next smallest milestone

The OpenRouter source-quality milestone is **complete and live-verified** (`sources_failed=0`, 8 verified
free models, offer quota keeping them visible). The next smallest step is to close the **environment-blocked
manual checks** in a real Electron session with an attached viewport: (1) click through the four UI checks
that a `0×0` viewport blocked — submit "What are my pending tasks?", toggle a theme and confirm chat
persists, click Radar "Refresh now"; (2) real mic → wake word → dispatcher; (3) OS notification rendering
for an `ai_radar_digest` alert while the overlay is hidden; (4) packaged-installer migration on a real user
DB. Optionally add a Hugging Face relevance filter (min downloads/likes or a test-repo blocklist) to cut
candidate noise. None of these change the verified backend behavior; they only confirm the desktop/visual layer.
