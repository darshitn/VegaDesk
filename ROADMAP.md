# V.E.G.A. (AgentOS) — Development Roadmap

## Phase 0: Discovery & Architecture Reality Audit (Completed)
- [x] Audit legacy codebase and distinguish actual behavior from aspirational docs.
- [x] Establish authoritative reality report (`docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md`).
- [x] Define Phase 1 security hardening plan (`docs/PHASE1_PLAN.md`).

## Phase 1: Core AgentOS Hardening (Completed)
- [x] **P1-A: Scaffolding & Security Architecture:** Implement unified risk policy gate (`policy.py`), typed action schemas, and strict tool execution boundaries.
- [x] **P1-B: Verifier & Receipt System:** Add cryptographic action verification (`verifier.py`) and durable SQLite receipts (`ActionReceipt`).
- [x] **P1-C: Scheduler Restart Recovery:** SQLite-backed scheduled alerts with orphan healing, exact timestamp matching, and duplicate suppression (`scheduler.py`).
- [x] **P1-D: SQLite Migrations & Integrity:** Online SQLite backup, DDL schema migration validation, and corruption recovery (`migrations.py`).
- [x] **P1-E: Readiness & Interaction Correctness:**
  - P1-E1: Separation of core DB readiness from optional service liveness; fault-tolerant provider handling.
  - P1-E2: Honest HTTP error feedback, input preservation on failed mutations, and native Electron notification channels.

## Phase 2: Beta Experience Polish & Desktop Isolation (Current — Completed)
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
- [x] **Deterministic Task Parsing Fix:**
  - Corrected regex matching for `"Add a new task to ..."` and `"Add a task to ..."` in `backend/command_parser.py`.
  - Enforced body cleaning (`_clean_body`) to eliminate leading `"to "` prepositions.
- [x] **Reactive Productivity Hub Updates:**
  - Event-driven pub/sub (`publishProductivityUpdate` / `subscribeProductivityUpdates`) refreshing mounted task, project, and timer views immediately upon committed chat/voice actions.
  - Support for seconds and minutes timer durations.

## Phase 3: Desktop Acceptance & Hardware Verification (Next Milestone)
- [ ] Supervised Windows acceptance session using the 32-case matrix in `docs/BETA_ACCEPTANCE_CHECKLIST.md`.
- [ ] Manual verification of physical microphone wake-word latency and ambient acoustic cancellation limits.
- [ ] Real Windows DPI scaling validation (100%, 125%, 150%) on physical displays.
- [ ] Native Windows Action Center notification delivery during hidden/minimized window states.
- [ ] Process tree teardown verification on tray quit (confirm port 8005 release).

## Phase 4: Production Release & Post-Beta Enhancements (Upcoming)
- [ ] Packaged installer build validation with PyInstaller and `electron-builder` (`npm run build:win`).
- [ ] Registered project launch routine (open approved IDE, folder, and resume next action).
- [ ] Local semantic search over saved notes and coursework.
- [ ] MediaPipe hand-tracking gesture refinement in Neural Cosmos.
