# V.E.G.A. — AgentOS

> A local-first Windows personal agent: conversational AI brain, real-time telemetry, voice control, and a hardened tool-execution engine — all running on your machine.

**Authoritative Docs:** [Product reality report](docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md) · [Active Phase 1 plan](docs/PHASE1_PLAN.md) · [Implementation log](docs/IMPLEMENTATION_LOG.md) · [Architecture brief](docs/VEGA_AGENTOS_BRIEF.md) · [Beta Acceptance Checklist](docs/BETA_ACCEPTANCE_CHECKLIST.md)

**Supervised Beta Acceptance:** [Beta checklist & verification](docs/BETA_ACCEPTANCE_CHECKLIST.md) · [Start/resume instructions](docs/ANTIGRAVITY_START.md) · [Polished beta roadmap](docs/POLISHED_BETA_ROADMAP.md)

---

## 🧭 Project Roadmap & Direction

- **One Authoritative Reality Report:** [docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md](docs/VEGA_PROJECT_REALITY_REPORT_2026-10-01.md) defines what VEGA actually is today: an implemented local personal productivity assistant for studies and projects, with voice as an input method, undergoing foundation hardening.
- **One Active Implementation Plan:** [docs/PHASE1_PLAN.md](docs/PHASE1_PLAN.md) outlines the active **AgentOS Phase 1** foundation milestones (P1-A through P1-E).
- **Roadmap Clarity (Historical P1/P2/P3 vs AgentOS Phase 1):**
  - **Historical P1/P2/P3 (Qoder-era prototypes in `docs/history/2026-qoder/`):** Created the initial feature prototypes — model provider lanes, durable workspaces/session drafts, and coursework planning. These features are implemented and active in the repository.
  - **AgentOS Phase 1 (Active core foundation):** Hardens execution security and reliability across those existing features without rewrites — centralized Risk 0..3 policy enforcement (`policy.py`), verified initiation (`verifier.py`), truthful SQLite receipts (`ActionReceipt`), and scheduler restart recovery (`scheduler.py`).

---

## ✨ What it does 

Press **`Ctrl+Space`** anywhere — the dashboard animates in over your desktop.

| Feature | Detail |
|---|---|
| 🧠 **Dual AI** | Google Gemini (cloud) or local Ollama — switch per-session |
| 🎙️ **Voice** | Always-on "Hey Jarvis" wake word, on-device Whisper transcription — no cloud, no keys |
| ⚡ **Fast actions** | "open youtube", "set a timer for 10 minutes" resolve via deterministic dispatcher before the LLM is consulted |
| 🔒 **Policy engine** | Every tool call passes a unified policy gate; unknown tools and malformed proposals fail closed with zero side effects |
| ⏰ **Scheduler** | Timers and reminders survive restarts; orphan healing uses exact due-time matching; delivery is idempotent |
| ✅ **Offline tasks** | "what's due this week?" reads your SQLite tasks — no model, no network |
| 📡 **AI Radar** | Daily background scan of GitHub releases, Hugging Face, and OpenRouter — deduplicated, source-isolated, no LLM required to render |
| 📊 **Telemetry** | CPU, RAM, disk, network, GPU, battery over WebSocket |
| 📰 **Live Feeds** | Weather (Open-Meteo), Hacker News, crypto prices |
| 🗂️ **Productivity Hub** | Local task list + auto-saving scratchpad (SQLite) |
| 🌌 **Neural Cosmos** | 3D hub with camera hand-tracking (MediaPipe) |
| 🎨 **Themes** | Sci-Fi HUD · Glass · Terminal · Neural Cosmos — persisted across restarts |

---

## 🛠️ Setup

Requirements: **Node.js 18+**, **Python 3.10+** (developed on 3.14).

```bash
npm run install:all   # installs node + pip dependencies
npm run dev           # starts backend (port 8000) + Vite + Electron together
```

Stop everything with a single `Ctrl+C`.

### Isolated Beta Acceptance Profile
To verify the app in a strictly isolated, synthetic test profile without touching personal data:
```bash
npm run seed:beta     # Idempotently seeds synthetic tasks, projects, coursework
npm run dev:beta      # Launches isolated backend (port 8005) and frontend in beta profile
```
See the full acceptance matrix in [docs/BETA_ACCEPTANCE_CHECKLIST.md](docs/BETA_ACCEPTANCE_CHECKLIST.md).

### Build a packaged installer

```bash
npm run build:win     # PyInstaller backend + electron-builder → frontend/release/
```

---

## ⚙️ Configuration

Create `backend/.env`:

```env
GEMINI_API_KEY=your_key_here
LLM_PROVIDER=gemini          # or "ollama"

# Ollama (fully local):
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=llama3

# Optional: pin mic device by name substring
# JARVIS_MIC_DEVICE=Airdopes
```

You can also switch AI engines per-session from the dashboard settings (⚙ icon).

---

## 🎙️ Voice

1. **Wake word** — say **"Hey Jarvis"** (normal volume). Uses openWakeWord `hey_jarvis`, fully local.
2. **Capture** — Silero VAD detects when you stop speaking (~1 s silence).
3. **Transcription** — local `faster-whisper` (defaults to `small.en`, int8 on CPU, VAD-trimmed). No network, no keys.
4. **Action** — transcript is sent to chat; "open …" commands resolve through the fast path and the dashboard steps aside so the app lands on top.

**Bluetooth users:** keeping the mic stream open forces classic-BT headsets into Hands-Free mode, degrading playback quality. Use **Settings → Wake Word** to toggle the mic off and restore full audio.

**Mic selection:** set `JARVIS_MIC_DEVICE=<name substring>` in `backend/.env` to pin a device. The backend health-checks the mic for 6 s on startup and warns loudly if it's silent.

**Whisper model:** defaults to `small.en` for high accuracy. Configure via `WHISPER_MODEL` environment variable (e.g. `WHISPER_MODEL=base.en` or `WHISPER_MODEL=tiny.en` for lower CPU latency).

> **Naming note:** the assistant is branded **V.E.G.A.**; the wake phrase is **"Hey Jarvis"** — tied to the pretrained openWakeWord model. Renaming the spoken phrase requires a custom wake-word model.

---

## ⌨️ Shortcuts & Modes

| Shortcut | Action |
|---|---|
| `Ctrl+Space` | Summon / dismiss dashboard (global hotkey) |
| `Esc` | Close settings / hide dashboard |
| Tray menu | Show/Hide · Hotkey Overlay vs Pinned Desktop · Quit |

Settings: theme, LLM engine, summon sound, launch-on-startup, wake word, gesture camera, username, city, crypto coins.

---

## 🎨 Themes

| Theme | Style |
|---|---|
| **Sci-Fi HUD** | Neon cyan, angular borders (default) |
| **Glass** | Frosted acrylic / mica backdrop |
| **Terminal** | Green-on-black phosphor |
| **Neural Cosmos** | 3D burning-core hub with hand-tracking (MediaPipe WASM, downloads on first use) |

---

## 🔒 Security

- Backend binds to `127.0.0.1` only; CORS is restricted to app origins.
- Both WebSockets validate the `Origin` header — a random web page cannot subscribe to your telemetry or voice transcripts.
- The policy engine rejects unknown tools, malformed proposals, and Risk 2+ actions (those needing confirmation) fail closed — **zero side effects** on denial.
- System Actions never interpolates user text into shell commands: input is fuzzy-matched against an installed-app catalog and site aliases; shell metacharacters are rejected outright.

---

## 🧪 Tests

```bash
# Backend (from jarvis-dashboard/backend/)
python -m pytest                                            # full suite (388 tests)
python -m pytest tests/test_p1_d2_migration_verification.py # migrations & backup reliability
python -m pytest tests/test_p1_d1_scheduler_recovery.py     # scheduler recovery
python -m pytest tests/test_workspaces.py                  # workspaces & session drafts
python -m pytest tests/test_academic.py                    # coursework & academic suggestions

# Frontend (from jarvis-dashboard/)
npm test --prefix frontend                                  # Node test runner suite (19 tests)
npm run lint --prefix frontend                              # Oxlint check
```

Tests use temporary SQLite databases, synthetic clocks (`FakeClock`), and fake listeners. No real app launches, desktop notifications, or personal data are touched.

---

## 📦 Packaging notes

- `npm run build:win` compiles the backend with PyInstaller and packages with electron-builder.
- The PyInstaller spec (`backend/jarvis-backend.spec`) is maintained for the desktop build pipeline; `datas` is configured to prevent bundling personal databases or credentials.
- On quit, the packaged backend child tree is killed via `taskkill /T` (PyInstaller onefile spawns a child that survives a plain kill).
- Single-instance lock: a second launch focuses the existing dashboard instead of spawning a duplicate.

---

## 📁 Project structure

```
jarvis-dashboard/
├── backend/
│   ├── main.py            # FastAPI app, WebSocket endpoints
│   ├── tools.py           # execute_intent — central policy + dispatch choke point
│   ├── policy.py          # risk classification for all tools
│   ├── scheduler.py       # timer/reminder scheduling, restart recovery, idempotent delivery
│   ├── dispatcher.py      # deterministic command dispatcher
│   ├── migrations.py      # additive SQLite schema migrations
│   ├── db.py              # SQLAlchemy models
│   ├── providers/         # Gemini + Ollama provider adapters
│   ├── tests/             # pytest suite (388 tests)
│   │   ├── test_p1_b_vertical_slice.py
│   │   ├── test_p1_c1_app_launch.py
│   │   ├── test_p1_c2_policy_enforcement.py
│   │   ├── test_p1_d1_scheduler_recovery.py
│   │   ├── test_p1_d2_migration_verification.py
│   │   ├── test_duration_and_completion.py
│   │   ├── test_workspaces.py
│   │   ├── test_academic.py
│   │   └── ...
│   └── ...
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/    # AIBrain, ProductivityHub, LiveFeeds, AIRadarPanel, …
│   │   ├── hooks/         # useChat
│   │   └── lib/           # chatStore, voiceState
│   └── tests/             # Node test runner suite (19 tests)
├── docs/
│   ├── VEGA_PROJECT_REALITY_REPORT_2026-10-01.md  # Authoritative product reset report
│   ├── PHASE1_PLAN.md                             # Active AgentOS Phase 1 plan
│   ├── IMPLEMENTATION_LOG.md                      # Verifiable implementation history & evidence
│   ├── VEGA_AGENTOS_BRIEF.md                      # Architecture brief & boundaries
│   ├── ANTIGRAVITY_START.md                       # Initial execution guide
│   ├── ANTIGRAVITY_P1D2_MIGRATION_VERIFICATION.md # Next active milestone prompt
│   └── history/                                   # Historical prompts & legacy handoffs
│       ├── 2026-qoder/
│       └── 2026-antigravity/
└── AGENTS.md              # AI agent instructions for this repo
```
