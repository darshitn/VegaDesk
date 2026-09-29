# V.E.G.A. — AgentOS

> A local-first Windows personal agent: conversational AI brain, real-time telemetry, voice control, and a hardened tool-execution engine — all running on your machine.

**Docs:** [Agent instructions](AGENTS.md) · [Product brief](docs/VEGA_AGENTOS_BRIEF.md) · [Phase 1 plan](docs/PHASE1_PLAN.md) · [Implementation log](docs/IMPLEMENTATION_LOG.md)

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
3. **Transcription** — local `faster-whisper` (base, int8, English, VAD-trimmed). No network, no keys.
4. **Action** — transcript is sent to chat; "open …" commands resolve through the fast path and the dashboard steps aside so the app lands on top.

**Bluetooth users:** keeping the mic stream open forces classic-BT headsets into Hands-Free mode, degrading playback quality. Use **Settings → Wake Word** to toggle the mic off and restore full audio.

**Mic selection:** set `JARVIS_MIC_DEVICE=<name substring>` in `backend/.env` to pin a device. The backend health-checks the mic for 6 s on startup and warns loudly if it's silent.

**Whisper model:** edit `WhisperModel("base", ...)` in `backend/voice_service.py`. `"tiny.en"` is ~2× faster; `"small"` is more accurate.

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
python -m pytest                          # full suite (~370 tests)
python -m pytest tests/test_scheduler.py  # scheduler + recovery

# Frontend (from jarvis-dashboard/)
npm test --prefix frontend                # Vitest suite
```

Tests use temporary SQLite databases, synthetic clocks (`FakeClock`), and fake listeners. No real app launches, desktop notifications, or personal data are touched.

---

## 📦 Packaging notes

- `npm run build:win` compiles the backend with PyInstaller and packages with electron-builder.
- The PyInstaller spec (`backend/jarvis-backend.spec`) is **not** used by the build scripts — it embeds `.env` and `jarvis.db` into the exe; do not ship it.
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
│   ├── tests/             # pytest suite (~370 tests)
│   └── ...
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/    # AIBrain, ProductivityHub, LiveFeeds, AIRadarPanel, …
│   │   ├── hooks/         # useChat
│   │   └── lib/           # chatStore, voiceState
│   └── tests/             # Vitest suite
├── docs/
│   ├── VEGA_AGENTOS_BRIEF.md
│   ├── PHASE1_PLAN.md
│   └── IMPLEMENTATION_LOG.md
└── AGENTS.md              # AI agent instructions for this repo
```
