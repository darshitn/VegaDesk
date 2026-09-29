# VEGA M2a Handoff — Reliable English Voice Input

Scope: make VEGA's always-on voice path (mic → wake word → local transcription
→ shared dispatcher) dependable for English commands, prevent VEGA's own
text-to-speech from being heard as a command, and make the listening /
transcribing / error states honest. The working **"Hey Jarvis"** wake phrase is
kept. Common commands stay **fully offline and ₹0**. No academic features and no
new PC-control permissions were added.

Status: **complete and test-covered.** Backend **137 passed** (+19 new voice/parser/API
tests), frontend **18 Node tests** pass (8 new), lint **0 errors** (17 pre-existing
warnings), build **succeeds**. The transcription pipeline was exercised on **real
English speech** (Windows TTS → `/api/transcribe`): 4/4 phrases transcribed exactly
and routed to the deterministic offline dispatcher. Live-microphone wake-word
accuracy/false-wake testing is **pending** (needs human speech into the mic).

---

## What the trace showed

```
Mic (sounddevice 16 kHz mono, 80 ms chunks)
  → openWakeWord "hey_jarvis" (WAKE_THRESHOLD 0.25)
  → Silero VAD (energy fallback) records until ~1 s silence (cap 15 s)
  → faster-whisper small.en  →  transcript
  → /ws/voice {"type":"transcript"}
  → App.dispatchTranscript → useChat.sendMessage → POST /chat
  → dispatcher.dispatch_command (deterministic, offline) → tools.execute_intent
```

Key facts confirmed by reading the code:
- The parser (`command_parser.py`) already strips a spoken wake-word prefix
  ("Hey Jarvis, …"), so transcripts with the wake word still resolve.
- `/chat` runs the deterministic dispatcher **before** any provider call, so
  common commands never touch Gemini/Ollama (no network, no cost).
- Conversation + voice state live in `App` (never unmounts), so a transcript
  arriving while the overlay is hidden is still processed.

---

## Changes

### `backend/voice_service.py`
- **Structured mic-state events.** All on/off transitions now flow through
  `_set_mic_listening()` which emits `{"type":"mic","listening":bool,"note":…}`
  instead of free-text `status` messages. `register_client` sends the *real*
  current mic state to a newly connected client, so the UI indicator reflects
  whether the listener is actually running (deps present, model loaded, stream
  open) rather than merely that the WebSocket connected.
- **Ducking (self-echo prevention).** New `set_duck(active, ttl)` / `is_ducked()`.
  While ducked the loop never wakes on VEGA's own voice, **drops any in-progress
  recording**, and resets the model buffer (so the wake right after speech needs
  a fresh user utterance). A server-side safety `DUCK_MAX_SECS = 120` re-arms the
  mic even if the browser never clears the flag (crash mid-speech).
- **Prompt-echo hallucination filter (real fix).** Whisper sometimes "transcribes"
  its own `initial_prompt` back ("The user gives a short spoken command or
  question.") on a quiet capture. That is never a user command. `filter_hallucination`
  now drops it **at any length**, plus the existing short-silence fillers. (The
  stale echo messages currently sitting in the app's chat panel are old
  `localStorage` history — clear them with the "Clear chat history" button.)
- **Single shared transcription path.** Extracted `transcribe_source()` (guarded
  beam/temperature/VAD + hallucination filter). Both the wake-word pipeline and
  `/api/transcribe` now use it — the manual-mic endpoint previously used a
  different, less-guarded `beam_size=5` call with **no** hallucination filter.
- Pure gates extracted for testing: `wake_fired()`, `recording_skip_reason()`,
  `filter_hallucination()`.

### `backend/main.py`
- **`POST /api/voice/duck`** — browser calls it while VEGA speaks (fire-and-forget).
- **`/api/transcribe`** now delegates to `voice_service.transcribe_source` via
  `asyncio.to_thread` (off the event loop) and accepts more container types.
- **`/chat` `source` field** — `"voice"` | `"chat"` (anything else sanitized to
  `"chat"`), recorded on receipts so voice-initiated actions are auditable.

### `frontend/src/lib/voiceState.js` (new) + reducer wiring
- Pure `reduceVoiceEvent` state machine for `idle → wake → listening → processing`.
  Fixes the reproduced bug where a capture that ended in a terminal
  `status`/`error` (no usable speech) **stuck the banner on "Transcribing…"** —
  those events now return to `idle` and surface a human note. The wake-word
  indicator is driven by `micListening` (from `mic` events), not socket openness.
- `App.jsx` wires it via `useReducer`; unknown message types can't corrupt state;
  `ws_close` resets honestly during reconnect.
- `AIBrain.jsx` shows the terminal note in the banner (failed captures never go
  silent again) and derives labels from the shared module.

### `frontend/src/hooks/useChat.js`
- **Duck on TTS.** A `useEffect` posts `/api/voice/duck` true/false exactly on
  `isSpeaking` edges, so VEGA can't hear its own replies. `keepalive: true` so a
  final un-ducks still lands if the overlay hides mid-speech.
- `sendMessage(text, { source })` threads the voice origin to `/chat`.

---

## Tests (focused, new)

| File | Covers |
| --- | --- |
| `backend/tests/test_voice_service.py` (14) | Wake gate at/above/below threshold + multi-model buffer; recording-quality gates (too-short / silent / usable); hallucination filter incl. **prompt-echo at any length**; duck on/off round-trip + **safety expiry**; `mic` event emission on enable/disable; `_set_mic_listening` state+event. |
| `backend/tests/test_command_parser.py` (+2) | Wake-word-prefixed transcripts route to the right **offline** intent; a bare wake-word echo parses to **nothing** (no action, no model call). |
| `backend/tests/test_api.py` (+2) | `/chat` with `source:"voice"` executes offline and attributes the receipt to `voice`; bad `source` sanitizes to `chat`. |
| `frontend/tests/voiceState.test.js` (8) | Happy path; the **stuck-"Transcribing…" regression**; error terminal; mic-events drive the indicator; note clearing; unknown types inert; `ws_close` reset; labels honest. |

---

## Measured results (real English speech)

Synthesized four English phrases with Windows TTS (`.wav`) and ran them through
the live guarded `/api/transcribe` pipeline, then routed three of them (the
read-only ones) through `/chat`:

| Spoken phrase | Transcript | Result |
| --- | --- | --- |
| "What are my pending tasks?" | exact | → `list_tasks`, `read_only:true`, answered from local SQLite |
| "Hey Jarvis, set a timer for five minutes" | "Hey Jarvis, set a timer for 5 minutes." | wake-word prefix + number-word both resolve to `start_timer` |
| "Are there any free AI models right now?" | exact | → `get_ai_radar_digest` (free), `read_only:true` |
| "What is new in AI?" | exact | → `get_ai_radar_digest` (general), `read_only:true` |

- **Transcription word-error-rate: 0/4 phrases** on clean TTS speech through the
  guarded pipeline (beam 1, temp 0, VAD, prompt-echo filter).
- **Dispatcher: 4/4** routed to deterministic offline intents; the timer case
  confirmed wake-word-prefix stripping + numeral normalization + "five minutes"
  parsing all hold together.
- **Duck endpoint** on the live backend: `{"ducked":true}` → `{"ducked":false}`.
- **Frontend render** (Vite dev, `take_snapshot`): wake-word banner, Productivity
  Hub, radar panel, telemetry all render; `voiceState` reducer mounted; **console
  clean** (only benign WS "closed before established" notices on HMR + React
  DevTools info).

## Limitations / pending

- **Live-microphone check is pending.** No human could speak into the physical
  mic in this environment, so **wake-word detection accuracy** and **false-wake
  rate** on real ambient speech are not yet measured. The synthetic TTS test
  exercises transcription + dispatcher, not openWakeWord's acoustic firing.
- **"Hey VEGA" NOT measured** and **not adopted** — see below.
- OS-notification rendering while the overlay is hidden, and packaged-installer
  behaviour, remain manual-only (unchanged from prior handoffs).
- The stale prompt-echo messages visible in the app are old persisted history;
  they don't recur (filter added) and can be cleared via the existing button.

## "Hey Jarvis" vs "Hey VEGA"

Kept **"Hey Jarvis"** (the pretrained openWakeWord model VEGA ships). Per the
milestone, "Hey VEGA" must be **measured separately and must not replace the
working phrase without recorded accuracy + false-wake results**. That measurement
requires the live-mic session above, so it is **not done**; VEGA still wakes on
"Hey Jarvis". (A custom "Hey VEGA" model would also need training data — no
pretrained model exists. Rename guidance is already in the README.)

---

## Next smallest step

Run one **live-microphone pass**: speak N "Hey Jarvis" utterances at varying
volume/distance and ~10 unrelated-speech distractors, and record the wake/false-wake
counts plus a few transcription samples. That single dataset is what unblocks both
the real-mic sign-off and any future "Hey VEGA" decision. No code change is needed
to perform it — just observe `[VOICE]` logs.

## Safety recap
- No general shell, file-deletion, or new PC-control capability added. The only
  system actions remain the pre-existing allowlisted `open_app`/`open_website`.
- Voice is always on-device (openWakeWord + faster-whisper); no audio or secrets
  leave the machine, and the mic can be turned off from Settings (releases the
  mic for Bluetooth headsets).
