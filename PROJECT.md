# V.E.G.A. (AgentOS) — Project Specification

## 1. Problem
Modern desktop workflows are fragmented across separate tools for tasks, calendar, notes, system monitoring, and AI assistants. Most AI assistants are either cloud-dependent (introducing latency, privacy risks, and recurring subscription costs), lack deep desktop integration, or cannot reliably execute deterministic actions without hallucinating or making accidental dangerous system modifications.

## 2. Target Users
- Power users, developers, and Computer Science students who need a unified, local-first keyboard- and voice-driven assistant on Windows.
- Users who value data privacy, zero recurring cloud API costs for daily productivity routines, and transparent, deterministic action execution.

## 3. Project Goals
- **Local-First & ₹0 Daily Cost:** Core workflows (tasks, notes, timers, reminders, system stats, workspace context) run 100% locally and deterministically via SQLite without requiring cloud LLMs.
- **Instant Keyboard & Voice Access:** Global hotkey (`Ctrl+Space` for production, `Ctrl+Alt+Space` for beta profile) and offline wake word ("Hey Jarvis") with sub-second responsiveness.
- **Fail-Closed Security:** Rigid Risk 0–3 policy gate preventing arbitrary shell execution, with typed action schemas, cryptographic verification tokens, and durable SQLite receipts.
- **Robustness Across Restarts:** Timers, reminders, active workspace context, and chat sessions survive application restarts without loss of state.
- **Zero-Coupling Voice Interruption:** Utterance-level generational guards and backend pending-interruption handshakes ensuring speaking "Hey Jarvis" immediately silences TTS and begins capturing the user's fresh command without acoustic self-triggering.

## 4. Core Features
- **Conversational Brain (Dual-Engine AI):** Local deterministic dispatch + optional local LLM (Ollama) or cloud LLM (Google Gemini) with per-session toggle.
- **Voice Pipeline:** Local offline wake word (openWakeWord `hey_jarvis`), Silero VAD silence detection, and local faster-whisper speech-to-text; voice interruption with TTS ducking and queue purging.
- **Productivity Hub:** Tasks with All/Open/Done filters, timers (seconds & minutes), scheduled reminders, focus sessions, coursework tracker, and durable session scratchpads.
- **Reactive UI Synchronization:** Real-time event subscription refreshing mounted Productivity Hub views immediately upon committed chat/voice actions without manual polling.
- **System Telemetry:** Real-time CPU, RAM, disk, network, GPU, and battery stats over WebSocket.
- **Live Feeds:** Local weather (Open-Meteo), Hacker News tech digest, and crypto prices.
- **Desktop System Tray & Hotkey Overlay:** Smooth hide-on-close to system tray, global shortcut toggling, and native Windows notifications via Electron IPC.
- **Synthetic Beta Isolation:** Dedicated isolated test profile (`VEGA_PROFILE=beta`, port 8005, DB in `%APPDATA%\Jarvis_Dashboard_Beta\db\jarvis-beta.db`) strictly preventing interference with personal profile data.

## 5. MVP vs Advanced Version
- **MVP (Current Beta Target):**
  - Reliable daily loop: capture tasks, complete tasks, start timers/reminders, switch workspaces, take notes.
  - Fully offline deterministic parser for all productivity commands.
  - Synthetic beta profile with verified session handshake and zero cloud key leakage.
  - Voice interruption support with keyboard (`Escape`) and click fallbacks.
  - 4 themes (Sci-Fi HUD, Glass, Terminal, Neural Cosmos) with contrast compliance and keyboard navigation.
- **Advanced Version (Future Milestones):**
  - Registered project launch routine (open approved IDE, folder, docs, and resume next action).
  - Semantic vector search over saved tasks, coursework PDFs, and session notes using local embeddings.
  - MediaPipe camera hand-gesture control in the 3D Neural Cosmos view.
  - Multi-window detached HUD widgets for continuous telemetry monitoring.

## 6. Constraints
- **OS Platform:** Windows 10/11 (utilizing Windows-specific tray, notifications, hotkeys, and process management).
- **Offline Integrity:** All health checks, migrations, startup scripts, and tests must run with zero internet connectivity and zero cloud API credentials.
- **Data Protection:** Personal database (`backend/jarvis.db`) and `.env` must never be used as test fixtures or touched by automated beta tests.
- **Acoustic Reality:** Without hardware Acoustic Echo Cancellation (AEC), software ducking and handoff acknowledgements mitigate speaker feedback, complemented by physical escape controls.

## 7. Technical Challenges
- **Voice Interruption & Acoustic Feedback:** Laptop speakers playing TTS right next to the mic can easily trigger false wake words or contaminate command audio. Mitigated by prompt wake sanitization, ducking, pending-interruption handshake, and pre-ack audio purging.
- **Cross-Process Session Verification:** Ensuring the Electron frontend binds strictly to its owned backend child process, refusing connection to mismatched ports, foreign profiles, or stale run IDs.
- **Scheduler Recovery:** Re-arming active timers and reminders upon app restart, handling missed/expired alerts idempotently, and preventing duplicate notifications.

## 8. Chosen Technology Stack
- **Frontend / Desktop Shell:** Electron 28+, React 18, Vite, Tailwind CSS, Lucide Icons, Three.js / React Three Fiber.
- **Backend Service:** Python 3.10+ (tested on 3.14), FastAPI, Uvicorn, SQLite3 (SQLAlchemy ORM), Pydantic v2.
- **Speech & Audio:** openWakeWord (ONNX runtime), PyAudio / sounddevice, Silero VAD, faster-whisper (CTranslate2).
- **Test Suites:** Node.js native test runner (`node --test`), Pytest, Oxlint.
