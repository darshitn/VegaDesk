# V.E.G.A. (AgentOS) — Task Tracking

## Current priority — voice reliability and Hey Vega (29 October 2026)

Voice work is in progress; [current findings and remaining tasks](docs/VOICE_QUALITY_AND_HEY_VEGA.md). Owner recordings were measured and the Colab notebook was prepared, but training and native wake acceptance have not run. The last full backend suite had 445 passed / 13 failed, with remaining failures reproduced against the original voice module.

Ollama work below is a deferred implementation checkpoint. Redirect handling, readiness timeout alignment and hostname validation remain unresolved; the existing loopback check is not complete isolation assurance. See [current resume guide](docs/ANTIGRAVITY_START.md).

## Historical implementation checklist: Phase 2.5 — Opt-In Local Ollama Beta Mode

- [x] **Loopback Endpoint Enforcement & Bounded Ollama Provider**
  - [x] Add loopback host check (`is_loopback_host`) and URL validator (`validate_loopback_url`) in `backend/providers/ollama.py`.
  - [x] Enforce loopback validation in `OllamaProvider.__init__`, rejecting remote/LAN endpoints to prevent SSRF and data exfiltration.
  - [x] Implement `check_ollama_readiness` probing local `/api/tags` with bounded timeout (no automatic downloads or token generation).
  - [x] Bound request timeout in `OllamaProvider._call` to 30.0s max with truthful timeout messaging.
  - [x] Export `validate_loopback_url` and `check_ollama_readiness` in `backend/providers/__init__.py`.

- [x] **Beta Profile & Backend Integration**
  - [x] Support `VEGA_BETA_OLLAMA=1` in `backend/main.py` when running with `VEGA_PROFILE=beta`.
  - [x] Keep ordinary beta strictly deterministic-only (`LLM_PROVIDER=none`, `deterministic_only: true`).
  - [x] Maintain scrubbing of all cloud API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, etc.) in beta Ollama mode.
  - [x] Expose structured Ollama readiness and `ollama_test_mode` in `GET /health`.
  - [x] Lock `effective_provider` in beta to `"ollama"` (if opt-in) or `"none"`, completely preventing cloud fallback.
  - [x] Guarantee deterministic commands ("add task:...", "timer 10m", etc.) resolve first before consulting Ollama.
  - [x] Ensure model proposals pass through `tool_registry` -> `policy.py` -> `executor` -> `ActionReceipt`.

- [x] **Launcher & Script Support**
  - [x] Add `options.ollama` handling to `getBetaEnvironment` in `scripts/launcherHelpers.js`.
  - [x] Sanitize inherited `OLLAMA_BASE_URL` to loopback; strip inherited `VEGA_BETA_OLLAMA` in default beta.
  - [x] Add `--ollama` flag handling and banner reporting to `scripts/launch_beta.js`.
  - [x] Add `dev:beta:ollama` (`node scripts/launch_beta.js --ollama`) and `dev:beta:ollama:voice` (`node scripts/launch_beta.js --ollama --voice`) to `package.json`.

- [x] **Automated Regression Verification**
  - [x] Create `backend/tests/test_beta_ollama.py` (7 tests: ordinary beta refusal, opt-in activation, loopback validation, readiness probe, deterministic priority, model proposal pipeline, and zero cloud fallback).
  - [x] Add launcher unit tests for `--ollama` in `frontend/tests/launcherValidation.test.js`.
  - [x] Run targeted backend suites (`test_beta_ollama.py`, `test_command_parser.py`, `test_beta_voice.py`, `test_beta_profile.py`): 62 passed (corrected arithmetic; historical targeted run).
  - [x] Run frontend unit tests (`npm test --prefix frontend`): 81 passed.
  - [x] Run linter (`npm run lint --prefix frontend`): 0 errors.
  - [x] Run format check (`git diff --check`): 0 errors.

---

## Completed Milestone: Phase 2 — Voice Interruption, Parsing Fix & Hub Reactivity (Git Commit `d13b6c3`)

- [x] **Voice Interruption & Ducking Pipeline**
  - [x] Implement wake prediction during ducking in `backend/voice_service.py`.
  - [x] Implement pending interruption state (`_interruption_pending`) with unique `interruption_id` and 1.5s timeout.
  - [x] Add client acknowledgement handshake via WebSocket frame `interrupt_ack` and fallback REST `POST /api/voice/interrupt_ack`.
  - [x] Purge pre-acknowledgement audio chunks from `audio_q` upon ACK to prevent captured speaker echo.
  - [x] Add generational utterance counter (`currentUtteranceIdRef` / `createSpeechSession`) in `frontend/src/lib/voiceInterruption.js` and `frontend/src/hooks/useChat.js`.
  - [x] Sanitize spoken text (`frontend/src/lib/speechText.js`) replacing "hey jarvis" with "VEGA" to avoid acoustic self-triggering.
  - [x] Add physical fallbacks: "Stop (Esc)" button in `frontend/src/components/AIBrain.jsx` and global `Escape` key shortcut in `frontend/src/App.jsx`.
  - [x] Owner-reported verification: Owner successfully verified desktop voice interruption (saying "Hey Jarvis" during TTS playback immediately silenced output and re-armed command capture).

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

---

## Upcoming Milestone: Phase 3 — Supervised Desktop Acceptance

- [ ] **Preparation & Pre-Flight Checklist**
  - [ ] Run synthetic seed (`npm run seed:beta`).
  - [ ] Launch beta desktop session (`npm run dev:beta`, `npm run dev:beta:voice`, and `npm run dev:beta:ollama`).
  - [ ] Verify profile banner, window title, and tray tooltip report port 8005.

- [ ] **Execute Acceptance Matrix (`docs/BETA_ACCEPTANCE_CHECKLIST.md`)**
  - [ ] Evaluate SEC-01 through SEC-04 (profile isolation, credential safety, port clash refusal).
  - [ ] Evaluate OLL-01 through OLL-03 (local Ollama readiness, conversation, tool proposal execution).
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
