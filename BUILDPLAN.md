# JARVIS Dashboard — Antigravity Build Plan

A free, personal, JARVIS-style desktop dashboard: instant hotkey summon, live system stats, an AI brain, live web data, and your own automation folded in. Built phase by phase by Antigravity — not by hand.

## Stack

- **Frontend shell:** Electron + React (Vite) + TailwindCSS — cross-platform (Windows/macOS/Linux), transparent/frameless windows, global hotkeys, tray icon.
- **Backend brain:** Python + FastAPI — system stats, AI calls, data fetching. Runs locally on `localhost:8000`.
- **AI:** Google Gemini's free API tier by default, with a one-line switch to a fully local Ollama model if you want zero API keys and zero internet dependency.
- **Cost:** $0 — everything below is free or has a free tier.

## Using Antigravity well

- Antigravity has three autonomy levels. Use **Balanced/Checkpoint mode** for this whole build — the agent plans, pauses so you can glance at the plan, executes, then shows verification artifacts (screenshots, terminal output) before continuing. Full autonomy is fine for Phase 0's scaffolding; save step-by-step approval for anything you're nervous about.
- Paste **one phase at a time**. Let it finish and verify before starting the next — far more reliable than pasting the whole roadmap in one go.
- Antigravity can drive a real browser to test what it builds — every prompt below ends with a "Verify" line that asks it to actually launch the app and check its own work. Don't let it skip that.
- Free for individual use, cross-platform, downloadable at antigravity.google if you don't already have it.

## Progress checklist

- [x] Phase 0 — Scaffold, hotkey, tray, theme system
- [x] Phase 1 — AI Brain (chat)
- [x] Phase 2 — Live system monitor
- [x] Phase 3 — Live web feeds
- [x] Phase 4 — Productivity hub
- [x] Phase 5 — Wake word + reliable voice capture
- [x] Phase 6 — Local task execution (System Actions)
- [ ] Phase 7 — Focus Mode — audit found this was NEVER actually built (stub only), despite believing it was done
- [x] Phase 8 — Polish, sound, packaging (packaging itself has real issues — see below)
- [x] Phase 9 — Neural Cosmos: gesture-controlled 3D theme (voice doesn't route into it — see below)

## Post-audit findings (GLM 5.3, Sept 2026)

GLM 5.3 audited the full codebase. Full report is worth keeping, but here's the priority order:

- [ ] **P0 — WebSocket origin/token check** (`main.py:280`, `main.py:295`) — confirmed live: any website open in any browser on this machine can silently connect to `/ws/system-stats` and `/ws/voice` and read your hardware telemetry and voice transcripts. Fix immediately, regardless of what else you touch.
- [ ] **P0 — Persist theme + display mode** (`main.js:13-14`, IPC at `357`) — confirmed broken, resets every restart. Everything else (name/city/provider/etc.) already persists correctly via localStorage; only these two don't.
- [ ] **P1 — Before you ever build/share a distributable:** `jarvis-backend.spec` currently bakes your real `GEMINI_API_KEY` and personal `jarvis.db` into the .exe — extractable in minutes. Strip both from the spec's `datas`, and consolidate the 3 conflicting build scripts into one that's actually used.
- [ ] **P1 — Fix `requirements.txt`** so a fresh clone can actually install (the torch `--index-url` is scoped wrong and breaks the whole install).
- [ ] **P2 — System Actions can't open full URLs**, only bare domain names — narrower than it should be.
- [ ] **P2 — Decide Focus Mode's fate**: actually build it (paste the real enforcer script this time) or remove the hub node/placeholder so Neural Cosmos stops advertising a feature that doesn't exist.
- [ ] **P2 — Wake word doesn't route into Neural Cosmos** — the voice WebSocket listener only lives inside the AI Brain component, so saying "Hey Jarvis" while in your newest theme does nothing visible.
- [ ] **P3 — Hygiene**: Tailwind v4 opacity classes are silently no-ops in 97 spots (visible as flat buttons instead of translucent ones), dead code/assets, duplicate wake-word vocab list, README doesn't mention half the built features, MediaPipe CDN version mismatch (blocks offline gesture control).

Next prompt for GLM 5.3 — bundles the two P0 fixes, both confirmed small:

```
Based on your audit, implement these two fixes now — both were flagged as small and contained:

1. WebSocket origin/token validation (P0, main.py:280 and main.py:295): add validation so /ws/voice and /ws/system-stats only accept connections from the app's own origin. Since Starlette doesn't validate Origin on WebSocket upgrades by default, either (a) manually check the Origin header against an allowlist (the app's own file:// / localhost origin) and reject the connection during the handshake if it doesn't match, or (b) require a short-lived token that the Electron frontend fetches from an authenticated HTTP endpoint first and passes as a query param on the WebSocket URL — pick whichever fits the existing code better, but the end result must be that a connection from an arbitrary external origin is rejected, not just filtered after connecting. Confirm the fix by attempting a WebSocket connection from a page on a different origin the same way you did during the audit, and confirming it is now rejected.

2. Persist theme and display mode (main.js:13-14 and the switch-theme IPC at line 357): replace the in-memory literals with actual persistence — use electron-store (or a small JSON file in Electron's userData path) so theme, display mode, and window bounds survive an app restart, matching how the other settings already persist via localStorage. Load the saved values on startup instead of defaulting to Sci-Fi HUD / Hotkey Overlay every launch.

Verify: restart the app after switching to a non-default theme and display mode, and confirm both are remembered. Then repeat your WebSocket origin test from the audit and confirm the foreign-origin connection is now refused for both endpoints.
```

---

## Phase 0 — Scaffold, Shell, Hotkey & Themes

```
Create a new desktop app project called "jarvis-dashboard".

Stack:
- /frontend: Electron + React (Vite) + TailwindCSS
- /backend: Python 3 + FastAPI, served at http://localhost:8000

Build ONLY the following in this phase — no feature modules yet:

1. Electron main window: frameless, transparent background, always-on-top, centered, starts hidden.
2. Global hotkey Ctrl+Space (Cmd+Space on macOS) via Electron's globalShortcut — toggles the window instantly, Spotlight/Raycast-style. Hide on Escape or on losing focus.
3. A second display mode: a small pinned "widget" window, corner-anchored. Add a settings toggle: "Hotkey Overlay" / "Pinned Widget" / "Both".
4. A system tray icon with a right-click menu: Show/Hide, Switch Mode, Quit.
5. A glassmorphic shell: rounded corners, backdrop-blur, soft glowing border, dark by default. Header reads "J.A.R.V.I.S." with a small animated status dot. Leave the content area empty with a comment marking where modules get added later.
6. A theme system as CSS variables with 3 presets, switchable from a settings panel:
   - "sci-fi-hud": near-black/navy background, glowing cyan/electric-blue accents, thin animated arcs
   - "glass": frosted neutral glass, light or dark
   - "terminal": black background, monospace font, phosphor-green text, subtle scanline
7. FastAPI backend with a single GET /health route returning {"status": "ok"}, CORS enabled for the Electron frontend.
8. package.json scripts to run frontend + backend together in dev (use "concurrently"), plus a README with setup/run steps.

Verify: run the dev script, confirm the hotkey shows/hides the window, confirm the tray menu works, confirm all 3 themes render correctly, confirm GET /health responds. Screenshot each theme as a verification artifact.
```

## Phase 1 — AI Brain (Chat)

```
Add an "AI Brain" module to jarvis-dashboard's content area.

1. Chat UI: scrollable history, input box, send button, streamed text if possible.
2. FastAPI POST /chat endpoint accepting {message, history}, wired to the Gemini API's free tier via its current official Python SDK (check ai.google.dev if the package name has changed since you were trained). Read the key from a .env file: GEMINI_API_KEY=.
3. Give it a short JARVIS-like system prompt: concise, dry-witted, helpful, uses the user's name if set in settings.
4. Add an LLM_PROVIDER env var: "gemini" (default) or "ollama" — if "ollama", call a local Ollama instance at http://localhost:11434 instead, so the assistant can run fully offline with no API key. Document both paths in the README.
5. Keep the last several messages in memory for conversational context.
6. Add a "thinking" state while waiting on a reply.

Verify: send a few test messages and confirm real replies come back through the UI. Screenshot a short conversation.
```

## Phase 2 — Live System Monitor

```
Add a "System Monitor" module to jarvis-dashboard.

1. FastAPI WebSocket at /ws/system-stats pushing CPU %, RAM, disk usage, network up/down, and battery % (if present) every 1-2 seconds via psutil. Try GPUtil or pynvml for NVIDIA GPU usage; if no compatible GPU, omit that stat gracefully rather than erroring.
2. Frontend: animated circular gauges or a compact live chart for CPU/RAM, plus simple readouts for disk/network/battery. Colors follow the active theme's CSS variables.
3. Keep updates smooth — no jank, no layout shift.

Verify: open a few heavy apps and confirm the numbers respond in real time; confirm it doesn't crash on a machine with no dedicated GPU. Screenshot the module.
```

## Phase 3 — Live Web Feeds

```
Add a "Live Feeds" module to jarvis-dashboard, using free, no-key APIs where possible:

1. Weather: Open-Meteo (no key required) — current conditions + short forecast for a city set in settings.
2. Headlines: a free source (e.g. the Hacker News API) — 5 headlines, each opening in the system's default browser on click.
3. Optional: a small crypto ticker via CoinGecko's free public API for 2-3 coins configurable in settings.
4. Cache each feed server-side for a few minutes so the UI stays fast and free APIs aren't hammered.
5. Match the active theme.

Verify: confirm each feed shows real current data, and that a slow/failed request shows a small inline error instead of breaking the dashboard. Screenshot the module.
```

## Phase 4 — Productivity Hub

```
Add a "Productivity Hub" module to jarvis-dashboard.

1. Task list: add / complete / delete, persisted in a local SQLite database via FastAPI so tasks survive restarts.
2. A quick-notes scratchpad that autosaves locally.
3. A small "today at a glance" strip showing open task count. Leave a placeholder for Google Calendar's next few events that only activates if the user later adds their own Google credentials — it should degrade gracefully with no crash if none are set.
4. No login or account required for tasks/notes to work.

Verify: add, complete, and delete a few tasks, restart the app, confirm they persisted. Screenshot the module.
```

## Phase 5 — Wake Word + Reliable Voice Capture

```
Rebuild the voice module in jarvis-dashboard. The previous approach (browser SpeechRecognition) isn't reliable enough — replace it with a proper on-device wake-word + transcription pipeline:

1. Wake word: use openWakeWord (pip install openwakeword) with its pretrained "hey_jarvis" model — no account, no signup, no API key, runs fully locally via ONNX. Run this as a lightweight, always-on background loop in the Python backend, capturing the default microphone (e.g. via sounddevice or pyaudio).
2. On wake word detection: push a WebSocket event to the frontend so the UI immediately shows a "listening" state (and summons the window via the existing hotkey-show logic if it's hidden).
3. Command capture: immediately after the wake word fires, start recording the microphone and use voice activity detection (Silero VAD, which openWakeWord already integrates, or the silero-vad package directly) to detect when the user has actually finished speaking — stop roughly 1-1.5s after speech ends, with a 15-second hard cap as a safety net. Don't use a fixed-length recording window; that's what caused missed words before.
4. Transcription: run the captured audio through a local Whisper model via faster-whisper (start with the "base" model for speed; note in the README how to switch to "small" for better accuracy if the machine can spare it). Local Whisper is dramatically more accurate at capturing a full utterance than continuous browser speech recognition.
5. Feed the final transcript into the existing chat pipeline from Phase 1 (and, once Phase 6 exists, into the command router first).
6. Keep the manual mic-button flow from before as a fallback for noisy environments or if the wake word doesn't fire — don't remove it.
7. Text-to-speech stays as before (browser SpeechSynthesis) — only the wake-word/capture side is being replaced here.
8. Note in the README: the wake phrase is the full two words "Hey Jarvis", not "Jarvis" alone — the model is trained specifically on that phrase and single-word variants have a higher false-reject rate. Leave a short pause after the wake phrase before speaking the command.

Verify: say "Hey Jarvis" from across the room at normal volume, confirm it wakes reliably without raising your voice, then say a full test sentence and confirm the transcript in the UI exactly matches what you said — the whole sentence, not just the first few words. Test at least 5 wake attempts and report the hit rate.
```

## Phase 6 — Local Task Execution (System Actions)

```
Add a "System Actions" capability to jarvis-dashboard so spoken or typed commands can actually do things on the machine — opening apps and websites — quickly, without always needing a full LLM round-trip.

1. Create a backend action registry (e.g. backend/system_actions.py) with two core functions:
   - open_app(name): cross-platform app launcher. Maintain a small, user-editable config mapping common app names to how to launch them per OS (e.g. "calculator" -> calc.exe on Windows / open -a Calculator on macOS / the relevant binary on Linux). Fail gracefully with a clear message if a name isn't mapped, rather than crashing.
   - open_website(site_or_url): use Python's built-in webbrowser module to open a URL in the default browser directly — don't simulate opening a browser and typing into its address bar, that's slower and more fragile. Maintain a small alias list ("youtube" -> https://youtube.com, "gmail" -> https://mail.google.com, etc.) so short spoken names resolve without a full URL.
2. Fast path: before involving the LLM at all, pattern-match simple phrasings like "open X" / "launch X" / "start X" against the alias registry and execute immediately on a match — this should feel close to instant, not a chat round-trip.
3. Fallback path: if the fast path doesn't match, pass the transcript to the existing Gemini/Ollama chat backend from Phase 1 using function/tool calling, exposing open_app and open_website as callable tools so more naturally-phrased requests ("pull up YouTube", "I need the calculator") still resolve. If neither path applies, treat it as a normal chat message.
4. After executing any action, show a short confirmation in the UI ("Opening Calculator...") and speak it via the existing text-to-speech if voice output is on, so the user knows the command landed.

Verify: test the fast path ("open calculator") and the fallback path (something looser, like "pull up YouTube for me") and confirm both actually open the right thing on this machine. Time the fast path from end-of-speech to the app opening and report roughly how long it takes.
```

### Phase 6b — Harden App Discovery & Fuzzy Matching

```
Harden the "System Actions" module from the previous phase — right now it only works for exact "open X" phrasing and only for apps explicitly listed in a hardcoded registry ("pull up the calculator" fails, and "terminal" isn't recognized at all). Fix this properly rather than adding more regex patterns for every possible phrasing:

1. Replace the static app registry with dynamic OS-level app discovery: on startup (and refreshed periodically), scan for actually installed applications —
   - Windows: enumerate Start Menu shortcuts (.lnk files) in both the user and system Start Menu folders
   - macOS: scan /Applications and ~/Applications for .app bundles
   - Linux: parse .desktop files in /usr/share/applications and ~/.local/share/applications
   Build this into a live catalog of {display name -> launch path/command} so the system knows about every app actually on this machine, not just ones someone remembered to hardcode. Also keep a small hardcoded fallback list of well-known system utilities per OS (Calculator, Terminal/Command Prompt, File Explorer/Finder) in case discovery misses them.
2. When resolving an app name (from either the fast path or the LLM tool call), normalize it first — lowercase, strip filler words like "the", "a", "please", "my" — then fuzzy-match against the discovered catalog (e.g. using the rapidfuzz library) instead of requiring an exact string match. Use a reasonable similarity threshold and pick the best match, not just the first match.
3. Debug the LLM fallback path specifically: looser phrasing like "pull up the calculator" is currently failing, meaning either the tool call isn't firing or its extracted argument isn't reaching the resolver correctly. Add logging at each stage (fast-path attempted -> matched or not -> fallback invoked -> tool called with what argument -> resolver matched to what app -> launched or failed) so failures are diagnosable instead of silent. Strengthen the tool's description/system prompt so the model reliably calls open_app or open_website for ANY request that means "access/open/launch/show me X", not just requests phrased with the literal word "open".
4. Apply the same normalization + fuzzy matching to website aliases in open_website.
5. If nothing resolves above the similarity threshold, respond conversationally saying it couldn't find that app/site, rather than failing silently.

Verify with a regression set covering both paths: "open calculator", "pull up the calculator", "start terminal", "I need a terminal", "show me gmail", "get me youtube" — confirm all resolve correctly, and share the debug log output for at least one fast-path and one fallback-path case so we can see it's actually routing where expected.
```

### Phase 6c — Fix Tool-Call Execution & False-Success Bug

```
Two bugs remain in System Actions, both suggesting the LLM fallback path isn't actually executing real function calls — it's likely generating confirmation text without the backend ever invoking open_app()/open_website(). Fix the root cause rather than patching each symptom:

1. Audit the LLM fallback integration end-to-end: confirm the Gemini/Ollama call is using real structured function/tool calling (a defined tools/functions schema passed to the API, with the code checking the response for an actual function_call/tool_call field) — not just a system prompt instructing the model to "say you're opening X." If the model is only producing conversational text like "Opening Calculator..." without the backend detecting and executing a real tool call, that's the bug: fix the integration so the model must emit a structured call before any confirmation text is shown, and treat plain-text-only responses to action requests as a failure to retry or report, not a success.
2. Confirmation text and TTS output must only fire AFTER open_app()/open_website() returns a real success — check the actual subprocess/OS call's result (the process actually spawned without immediate error) before saying "Opening X". On failure, say so explicitly ("Couldn't open Calculator — <reason>") instead of claiming success.
3. Fix the bug where an unresolved app name silently opens Calculator instead of failing: find and remove whatever causes an unmatched name to fall through to Calculator specifically — likely a fuzzy-match threshold check that isn't actually rejecting low-confidence matches (e.g. returning catalog[0] or a hardcoded default instead of "no match found"). An unresolved name must return a clear "couldn't find that" result, never a silent substitute.
4. Add full request-to-execution logging (transcript received -> fast-path result -> fallback invoked? -> tool call emitted with what args -> resolver match + confidence score -> execution attempted -> execution result) so any future failure is traceable from logs alone.

Verify with this exact regression set, checking BOTH the spoken/UI confirmation AND whether the app/site actually appears on screen:
- "open calculator" (fast path)
- "pull up the calculator" (fallback path)
- "terminal"
- a deliberately nonsense request like "open the flibbertigibbet" — confirm it says it couldn't find that, and confirm Calculator does NOT open
Share the log output for the "pull up the calculator" case and the nonsense case specifically, so we can see exactly where the disconnect is.
```

## Phase 7 — Focus Mode

```
Add a "Focus Mode" module to jarvis-dashboard that brings in my existing Valorant-blocking script (blocks it on weekdays, allows it on weekends) as a dashboard toggle instead of a standalone tool.

[Paste your existing enforcer script here so the agent wires in the real logic instead of guessing at it.]

1. A toggle + status readout showing whether Focus Mode is active right now and why ("Weekday lock active" / "Weekend — unlocked").
2. Wire the toggle to the real enforcer logic via a FastAPI endpoint.
3. Let the AI Brain answer questions about current Focus Mode status if asked.

Verify: confirm the toggle reflects and can override the real block state. Screenshot the module.
```

## Phase 8 — Polish & Packaging

```
Final pass on jarvis-dashboard.

1. Entrance/exit animation for the hotkey overlay (fade + scale, ~150-200ms) and small hover/press micro-interactions, via Framer Motion, respecting the active theme.
2. An optional short summon sound effect, off by default, togglable in settings.
3. "Launch on system startup" toggle using the auto-launch npm package.
4. A first-run setup wizard: name, city, theme, display mode, LLM provider.
5. Package into installable builds with electron-builder: .exe (Windows), .dmg (macOS), .AppImage (Linux).
6. Finish the README with setup steps, a screenshot of each theme, and a short description written for a GitHub portfolio.

Verify: produce an installable build for your own OS and run the packaged app end-to-end, not just the dev server.
```

## Phase 9 — Neural Cosmos: Gesture-Controlled 3D Theme

```
Add a new 4th theme called "Neural Cosmos" to jarvis-dashboard — a 3D, gesture-controlled visualization that replaces the flat module grid with an interactive hub when this theme is active.

1. Visual: build a Three.js scene — a rotating sphere of glowing point-nodes distributed evenly (fibonacci sphere layout), connected by thin animated pulsing arcs between nearby nodes, with additive-blended glow (UnrealBloomPass) and a deep-space near-black background with slow-drifting ambient particles. Make 5 of the nodes larger and distinctly labeled/colored as "hub nodes" — one each for AI Brain, System Monitor, Live Feeds, Productivity Hub, and Focus Mode. This should read as equal parts neural network and galaxy — call the aesthetic direction "neural cosmos."
2. Hand gesture control: use @mediapipe/tasks-vision's GestureRecognizer (with the GPU delegate for performance) reading the webcam feed to control the scene:
   - Open palm + hand movement -> rotates the sphere, mapped to hand X/Y position delta
   - Pinch (distance between thumb tip and index fingertip landmarks) -> zooms the camera in/out
   - Pointing gesture held on a hub node for about 1 second (dwell-to-select, no click gesture needed) -> navigates into that module's existing view
   - Closed fist -> resets the camera to the default overview position
3. Camera privacy and resource use, both important: camera access must be OFF by default and only requested when the user explicitly enables this theme. Add a clear on/off toggle and a distinct always-visible "camera active" indicator whenever the webcam is capturing, separate from the existing mic-listening indicator. Fully stop the camera track (not just hide the UI) whenever the user switches to a different theme, disables the toggle, or the window is hidden.
4. Fallback controls: if the camera is off, denied, or unavailable, the scene must still be fully usable via mouse/trackpad — drag to rotate, scroll to zoom, click a hub node to navigate. Gesture control is an enhancement, not a requirement.
5. Performance: only run the MediaPipe pipeline while this theme is active and the window is visible — never in the background. Monitor actual frame rate and automatically drop to a lower-detail mode (fewer nodes/particles, lower hand-tracking input resolution) if it dips below a smooth threshold (~30fps), rather than staying pinned to full detail on weaker hardware.

Verify: switch to the Neural Cosmos theme, grant camera access, and confirm each gesture works — rotate with an open palm, zoom with a pinch, dwell-select on at least two different hub nodes to confirm navigation actually opens the right module, and reset with a fist. Then turn the camera toggle off and confirm mouse-based rotate/zoom/click still fully work. Screenshot the hub view and one gesture mid-action.
```

---



- Swap the local task list for real calendar/email integrations
- Speaker verification via Picovoice Eagle, so Jarvis only responds to your voice specifically, not roommates or the TV
- A plugin system so new modules can be dropped in without touching the shell
- Antigravity's scheduled-task feature to have an agent check for dependency updates on a cron, later on