# VEGA AgentOS

> A local-first Windows personal agent built from the existing Electron, React, FastAPI, and SQLite application.

**Start here for implementation:** [Agent instructions](AGENTS.md) · [Product brief](docs/VEGA_AGENTOS_BRIEF.md) · [Phase 1 plan](docs/PHASE1_PLAN.md) · [Next Antigravity prompt](docs/ANTIGRAVITY_P1C1_APP_LAUNCH.md) · [Implementation log](docs/IMPLEMENTATION_LOG.md). Open this folder as the Antigravity workspace. Old Qoder plans and handoffs are retained in [history](docs/history/2026-qoder/) for reference.

**Development status:** The repository already contains substantial dashboard, productivity, voice, scheduler, and model-routing code. Phase 1 will assess and harden that code rather than replace it. Historical test claims are in the archived handoffs; Antigravity must verify the current source and tests. Real microphone, notification, and packaged installer behavior remains a separate desktop check.

V.E.G.A. is a frameless desktop overlay with a conversational AI brain, productivity tools, and real-time system telemetry. Press `Ctrl+Space` anywhere and the dashboard animates in.

**Key Highlights:**
- 🧠 **Dual AI Engine:** Google Gemini (cloud) or a local Ollama model — your choice, per-session.
- 🎙️ **Local Voice Interaction:** always-on "Hey Jarvis" wake word with on-device transcription via `faster-whisper`. No cloud APIs, no API keys for voice.
- ⚡ **System Actions:** "open youtube", "open leetcode" (typed or spoken) resolve instantly via a fast pattern path; the LLM tool-calling path handles everything else.
- ✅ **Offline task answers:** "what are my pending tasks?", "show my tasks", "what's due this week?" read your existing SQLite tasks through the same deterministic dispatcher — no model, no network. LLMs get a read-only `list_my_tasks` tool bound to that same executor, so they report only real open tasks instead of inventing them.
- 📡 **AI Radar:** a daily background pass (~09:00 local, plus "Refresh now") over public sources — GitHub releases, Hugging Face, OpenRouter's free-model catalog — stored deduplicated in SQLite. New HF repos are flagged as candidates (not confirmed launches), free hosted models are never presented as free token/credit grants, expired or unconfirmed offers say so, and each source failure is isolated so offline runs keep showing the last known items with an honest "last checked" age. The digest renders deterministically without an LLM.
- 📊 **Real-time Telemetry:** CPU, RAM, disk, network, GPU, and battery over a WebSocket.
- 📰 **Live Feeds:** weather (Open-Meteo), Hacker News headlines, crypto prices.
- 🗂️ **Productivity Hub:** local task list + auto-saving scratchpad notes (SQLite).
- 🌌 **Neural Cosmos theme:** a 3D hub controlled by camera hand-tracking (MediaPipe).
- 🎨 **Themes:** Sci-Fi HUD, Glass, Terminal, Neural Cosmos. Theme, display mode, and window geometry persist across restarts.

> **Naming note:** the assistant is branded **V.E.G.A.**; the wake phrase is still **"Hey Jarvis"** — it's tied to the pretrained openWakeWord model. Renaming the spoken phrase requires a custom wake-word model (see "Wake word" below).

---

## 🛠️ Setup (Development)

Requirements: Node.js 18+, Python 3.10+ (developed on 3.14).

```bash
npm run install:all   # node deps + backend pip deps
npm run dev           # backend (port 8000, auto-reload) + frontend (Vite + Electron) together
```

Stop both with a single `Ctrl+C` in that terminal.

### Running the built app instead

```bash
npm run build:win     # PyInstaller backend + electron-builder package
```

## ⚙️ AI Configuration

Create `backend/.env`:

```env
GEMINI_API_KEY=your_api_key_here
LLM_PROVIDER=gemini          # or "ollama"
# Ollama (fully local):
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=llama3
JARVIS_MIC_DEVICE=Airdopes   # optional: pin the input device by name substring
```

You can also switch engines per-session in the dashboard settings (gear icon). Weak local models sometimes emit tool-call JSON as text — the backend detects and executes it, and the UI never speaks raw JSON.

## 🎙️ Voice

1. **Wake word:** say **"Hey Jarvis"** (two words, normal volume) — openWakeWord `hey_jarvis`, fully local.
2. **Command capture:** Silero VAD detects when you stop speaking (~1 s of silence).
3. **Transcription:** local `faster-whisper` (base, int8, English, VAD-trimmed) — no network, no keys.
4. **Action:** the transcript is auto-sent to the chat; "open …" commands resolve through the fast path and the dashboard ducks out of the way so the launched app lands on top.

**Bluetooth headset users:** keeping the mic stream open forces classic-BT headsets into Hands-Free mode, which degrades their *playback* quality to telephone level. That's a Bluetooth-stack behavior, not a bug. The **Settings → "Wake Word"** toggle closes the mic entirely and restores full audio quality (voice off); re-enable it when you want voice back.

**Mic selection:** the backend uses the OS default input. To pin a specific device (e.g. a Bluetooth headset), set `JARVIS_MIC_DEVICE=<name substring>` in `backend/.env`. On startup the backend health-checks the mic for 6 s and prints a loud `[VOICE] WARNING` if the device is delivering silence.

**Wake-word tuning:** wake scores just under the threshold are logged as `Near-miss wake score 0.XX`. The threshold lives in `backend/voice_service.py` (`WAKE_THRESHOLD`, default 0.35).

### Switching Whisper models

Edit `WhisperModel("base", ...)` in `backend/voice_service.py` (wake-word path) and in `backend/main.py` (manual mic fallback endpoint). `"tiny.en"` is ~2× faster with lower accuracy; `"small"` is more accurate but slower.

## 🖼️ Themes

- **Sci-Fi HUD** — neon cyan, angular borders (default)
- **Glass** — frosted acrylic/mica backdrop
- **Terminal** — green-on-black phosphor
- **Neural Cosmos** — 3D "burning core" hub, camera hand-tracking (MediaPipe, downloads WASM + model from CDN on first use)

## ⌨️ Shortcuts & Modes

- `Ctrl+Space` — summon/dismiss the dashboard (global)
- `Esc` — close settings / hide the dashboard
- Tray menu — Show/Hide, **Hotkey Overlay** vs **Pinned Desktop** mode, Quit
- Settings — theme, LLM engine, summon sound, launch-on-startup, wake word on/off, gesture camera, user name, city, crypto coins

## 📦 Packaging notes

- `npm run build:win` compiles the backend with PyInstaller and packages the app with electron-builder (installer in `frontend/release`).
- The PyInstaller spec file (`backend/jarvis-backend.spec`) is **not** referenced by the build scripts — do not build with it as-is: it embeds `backend/.env` (your API key) and `jarvis.db` (personal data) into the exe.
- On quit, the packaged backend child tree is killed via `taskkill /T` (PyInstaller onefile spawns a child that survives a plain kill).
- Single-instance lock: launching a second copy focuses the existing dashboard instead of spawning a duplicate voice client.

## 🔒 Security posture

- Backend binds to `127.0.0.1` only; CORS is restricted to the app's origins.
- **Both WebSockets validate the `Origin` header** and reject foreign origins during the handshake — a random web page cannot subscribe to your telemetry or voice transcripts.
- System Actions never interpolates user text into shell commands: input is fuzzy-matched against a catalog of installed apps (`.lnk`/`.app`/`.desktop`) and curated site aliases, with shell metacharacters rejected outright.
