# V.E.G.A. (AgentOS) — Task Tracking

## Current Active Milestone: Phase 2 — Voice Interruption, Parsing Fix & Hub Reactivity (Finalizing & Committing)

- [x] **Voice Interruption & Ducking Pipeline**
  - [x] Implement wake prediction during ducking in `backend/voice_service.py`.
  - [x] Implement pending interruption state (`_interruption_pending`) with unique `interruption_id` and 1.5s timeout.
  - [x] Add client acknowledgement handshake via WebSocket frame `interrupt_ack` and fallback REST `POST /api/voice/interrupt_ack`.
  - [x] Purge pre-acknowledgement audio chunks from `audio_q` upon ACK to prevent captured speaker echo.
  - [x] Add generational utterance counter (`currentUtteranceIdRef` / `createSpeechSession`) in `frontend/src/lib/voiceInterruption.js` and `frontend/src/hooks/useChat.js`.
  - [x] Sanitize spoken text (`frontend/src/lib/speechText.js`) replacing "hey jarvis" with "VEGA" to avoid acoustic self-triggering.
  - [x] Add physical fallbacks: "Stop (Esc)" button in `frontend/src/components/AIBrain.jsx` and global `Escape` key shortcut in `frontend/src/App.jsx`.

- [x] **Deterministic Task Parsing Fix**
  - [x] Fix regex in `backend/command_parser.py` to match `"add a new task to ..."` and `"add a task to ..."`.
  - [x] Strip leading `"to "` prepositions using `_clean_body` on non-deadline matches.
  - [x] Add regression test cases in `backend/tests/test_command_parser.py`.

- [x] **Reactive Productivity Hub Updates & Timers**
  - [x] Implement `frontend/src/lib/productivityUpdates.js` with `subscribeProductivityUpdates` and `publishProductivityUpdate`.
  - [x] Wire `publishProductivityUpdate(data)` into `useChat.js` on successful mutating action receipts.
  - [x] Wire `subscribeProductivityUpdates(refresh)` into `ProductivityHub.jsx`.
  - [x] Add support for seconds & minutes timer durations with input validation.
  - [x] Add responsive CSS layout for hub cards and timer forms in `frontend/src/index.css`.
  - [x] Add unit tests in `frontend/tests/productivityUpdates.test.js`.

- [x] **Automated Verification & Linting**
  - [x] Run frontend unit tests (`npm test --prefix frontend`): 81 passed.
  - [x] Run backend pytest suite (`python -m pytest backend/tests/test_command_parser.py backend/tests/test_beta_voice.py`): 45 passed.
  - [x] Run frontend linter (`npm run lint --prefix frontend`): 0 errors.
  - [x] Run git whitespace check (`git diff --check`): 0 errors.

- [x] **Documentation & Quality Control**
  - [x] Create core planning documents: `PROJECT.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `TASKS.md`, `AI_INSTRUCTIONS.md`.
  - [x] Update `docs/IMPLEMENTATION_LOG.md` with complete implementation details.
  - [ ] Stage and commit completed working milestone to Git.

---

## Upcoming Milestone: Phase 3 — Supervised Desktop Acceptance

- [ ] **Preparation & Pre-Flight Checklist**
  - [ ] Run synthetic seed (`npm run seed:beta`).
  - [ ] Launch beta desktop session (`npm run dev:beta` and `npm run dev:beta:voice`).
  - [ ] Verify profile banner, window title, and tray tooltip report port 8005.

- [ ] **Execute Acceptance Matrix (`docs/BETA_ACCEPTANCE_CHECKLIST.md`)**
  - [ ] Evaluate SEC-01 through SEC-04 (profile isolation, credential safety, port clash refusal).
  - [ ] Evaluate NAV-01 through NAV-02 (workspace switcher, tab navigation).
  - [ ] Evaluate TSK-01 through TSK-03 (task filters, optimistic mutation, long list overflow).
  - [ ] Evaluate PRJ-01 through PRJ-02 (project view, coursework, session notes).
  - [ ] Evaluate CHT-01 through CHT-02 (chat persistence, mutating receipt vs read-only distinction).
  - [ ] Evaluate NOT-01 through NOT-04 (toast visible/hidden, deduplication, truthful audit).
  - [ ] Evaluate PTT-01 (voice test mode vs default mic disable).
  - [ ] Evaluate WIN-01 through WIN-03 (window hotkey, maximize/restore, actual quit vs hide).
  - [ ] Evaluate RST-01 (restart persistence across process teardown).
  - [ ] Evaluate SCL-01 through SCL-03 (100%, 125%, 150% Windows DPI scaling).
  - [ ] Evaluate THM-01 (theme suite cycling).
  - [ ] Evaluate PRF-01 through PRF-03 (idle/hidden resource usage, execution latency).
  - [ ] Evaluate PRC-01 (process tree teardown and port 8005 release).
