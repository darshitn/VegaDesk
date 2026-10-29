# V.E.G.A. (AgentOS) — Development Roadmap

> Current checkpoint, 29 October 2026: active voice reliability/Hey Vega work is in progress; Ollama hardening is deferred. The broader desktop companion is a proposal in [the revised product plan](docs/VEGA_PERSONAL_OS_PLAN.md). Historical completed labels below describe implementation, not current full-suite or native release acceptance. See [resume guide](docs/ANTIGRAVITY_START.md) for known failures and training status.

## Phase 0: Discovery & Architecture Reality Audit (Completed)
- [x] Audit legacy codebase and distinguish actual behavior from aspirational docs.
- [x] Establish authoritative reality report (`docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md`).
- [x] Define Phase 1 security hardening plan (`docs/PHASE1_PLAN.md`).

## Phase 1: Core AgentOS Hardening (Completed)
- [x] **P1-A: Scaffolding & Security Architecture:** Implement unified risk policy gate (`policy.py`), typed action schemas, and strict tool execution boundaries.
- [x] **P1-B: Verifier & Receipt System:** Add verified session tokens (`verifier.py`) and durable SQLite receipts (`ActionReceipt`).
- [x] **P1-C: Scheduler Restart Recovery:** SQLite-backed scheduled alerts with orphan healing, exact timestamp matching, and duplicate suppression (`scheduler.py`).
- [x] **P1-D: SQLite Migrations & Integrity:** Online SQLite backup, DDL schema migration validation, and corruption recovery (`migrations.py`).
- [x] **P1-E: Readiness & Interaction Correctness:**
  - P1-E1: Separation of core DB readiness from optional service liveness; fault-tolerant provider handling.
  - P1-E2: Honest HTTP error feedback, input preservation on failed mutations, and native Electron notification channels.

## Phase 2: Beta Experience Polish & Desktop Isolation (Completed)
- [x] **BETA-UI:** Responsive layout, unified design tokens, All/Open/Done task filters, keyboard accessibility, custom scroll containers.
- [x] **Isolated Beta Profile:**
  - Dedicated synthetic test profile (`VEGA_PROFILE=beta`, port 8005, DB in `%APPDATA%\Jarvis_Dashboard_Beta`).
  - Strict exclusion of `backend/.env` and scrubbing of inherited cloud API keys from memory.
  - Non-destructive synthetic database seeder (`npm run seed:beta`).
  - Session verification gating (`verifyBackendSession`) requiring matching `run_id` and healthy database before permitting mutations.
- [x] **Voice Interruption & Cancellation Pipeline:**
  - Local wake detection during TTS ducking in `backend/voice_service.py`.
  - Pending-interruption state machine (`_interruption_pending`) with correlated `interruption_id`.
  - Frontend unconditional Web Speech synthesis cancellation upon interrupt signal.
  - WebSocket and REST fallback (`POST /api/voice/interrupt_ack`) acknowledgement handshake.
  - Audio queue pre-ack frame purging to discard speaker echo.
  - Generational utterance counter in `useChat.js` / `createSpeechSession` preventing race conditions.
  - Physical fallbacks: on-screen "Stop (Esc)" button in `AIBrain.jsx` and global `Escape` key shortcut in `App.jsx`.
  - Owner-reported verification: Owner verified real desktop spoken interruption working cleanly.
- [x] **Deterministic Task Parsing Fix:**
  - Corrected regex matching for `"Add a new task to ..."` and `"Add a task to ..."` in `backend/command_parser.py`.
  - Enforced body cleaning (`_clean_body`) to eliminate leading `"to "` prepositions.
- [x] **Reactive Productivity Hub Updates:**
  - Event-driven pub/sub (`publishProductivityUpdate` / `subscribeProductivityUpdates`) refreshing mounted task, project, and timer views immediately upon committed chat/voice actions.
  - Support for seconds and minutes timer durations.

## Phase 2.5: Opt-in Local Ollama Beta Mode (Active Milestone)
- [x] **Loopback Endpoint Enforcement & Bounded Ollama Provider:**
  - Strict validation of `OLLAMA_BASE_URL` against loopback interfaces (`localhost`, `127.0.0.1`, `::1`); remote/LAN IPs rejected fail-closed.
  - Bounded `/api/tags` readiness probe with truthful model detection (no automatic downloads or token generation).
  - Bounded 30s request timeouts with graceful offline explanations.
- [x] **Beta Mode Architecture & Launcher Support:**
  - Dedicated CLI flags and scripts: `npm run dev:beta:ollama` and `npm run dev:beta:ollama:voice`.
  - Ordinary beta (`npm run dev:beta`, `npm run dev:beta:voice`) remains deterministic-only.
  - Deterministic commands ("add task:...", "timer 10m", etc.) always execute first with zero model calls.
  - Model tool proposals execute through the standard `tool_registry` -> `policy.py` -> `executor` -> `ActionReceipt` pipeline.
  - Zero cloud fallback on Ollama failure or timeout; cloud keys remain stripped in process memory.

## Phase 3: Desktop Acceptance & Hardware Verification (Next Milestone)
- [ ] Supervised Windows acceptance session using the matrix in `docs/BETA_ACCEPTANCE_CHECKLIST.md`.
- [ ] Manual verification of physical microphone wake-word latency and ambient acoustic cancellation limits.
- [ ] Real Windows DPI scaling validation (100%, 125%, 150%) on physical displays.
- [ ] Native Windows Action Center notification delivery during hidden/minimized window states.
- [ ] Process tree teardown verification on tray quit (confirm port 8005 release).
- [ ] Real Ollama conversational reasoning and tool call verification with an installed local model.

## Phase 4: Production Release & Post-Beta Enhancements (Upcoming)
- [ ] Packaged installer build validation with PyInstaller and `electron-builder` (`npm run build:win`).
- [ ] Registered project launch routine (open approved IDE, folder, and resume next action).
- [ ] Local semantic search over saved notes and coursework.
- [ ] MediaPipe hand-tracking gesture refinement in Neural Cosmos.
