"""
Voice Service: Always-on wake word detection + VAD-gated recording + local Whisper transcription.

Pipeline:
  1. sounddevice streams 16kHz mono PCM from default mic in ~80ms chunks
  2. openWakeWord runs "hey_jarvis" ONNX model on each chunk
  3. On wake detection → switch to recording mode
  4. Silero VAD (bundled with openWakeWord) detects speech end (~1.5s silence)
  5. faster-whisper transcribes the captured audio
  6. Result pushed to all connected WebSocket clients
"""

import asyncio
import threading
import time
import io
import os
import sys
import wave
import numpy as np

def _log(msg):
    """Print with flush so it appears in subprocess output."""
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(str(msg).encode('ascii', 'replace').decode('ascii'), flush=True)
        except Exception:
            pass

# ──────────────────────────────────────────────
# State shared between the audio thread and async
# ──────────────────────────────────────────────
_clients: list = []         # connected WebSocket objects
_lock = threading.Lock()
_event_queue: asyncio.Queue = None  # set in start()
_voice_active: bool = False
_voice_error: str = ""
_enabled = threading.Event()
_enabled.set()  # wake-word listening is on by default; UI can turn it off to
                # release the mic (classic-BT headsets then leave HFP and their
                # audio playback quality returns to normal)

# Ducking: while VEGA's own text-to-speech is playing, the browser tells us to
# ignore the mic so the speaker output can never wake the listener or be
# transcribed back as a user command. A safety expiry re-arms the mic even if
# the client dies mid-utterance and never sends the "off" signal.
_duck_active = threading.Event()
_duck_deadline = 0.0
DUCK_MAX_SECS = 120.0


def set_duck(active: bool, ttl: float = DUCK_MAX_SECS):
    """Turn mic ducking on/off (called when VEGA starts/stops speaking)."""
    global _duck_deadline
    if active:
        _duck_deadline = time.time() + max(1.0, min(float(ttl), DUCK_MAX_SECS))
        _duck_active.set()
        _log("[VOICE] Mic ducked (VEGA is speaking).")
    else:
        _duck_active.clear()
        _duck_deadline = 0.0
        _log("[VOICE] Mic un-ducked (speech finished).")


def is_ducked() -> bool:
    if not _duck_active.is_set():
        return False
    if time.time() >= _duck_deadline:
        _duck_active.clear()
        _log("[VOICE] Duck safety-expired; mic re-armed.")
        return False
    return True


def _set_mic_listening(listening: bool, note: str = ""):
    """Single funnel for mic on/off state: updates flags AND emits a structured
    {"type":"mic"} event so the UI indicator reflects reality, not just the
    WebSocket being open."""
    global _voice_active, _voice_error
    _voice_active = bool(listening)
    _voice_error = "" if listening else note
    _enqueue({"type": "mic", "listening": bool(listening), "note": note})


def wake_fired(prediction_scores: dict, threshold: float) -> bool:
    """Pure gate: did any wake model's latest score cross the threshold?"""
    for score in (prediction_scores or {}).values():
        if len(score) > 0 and float(score[-1]) >= threshold:
            return True
    return False


def recording_skip_reason(num_samples: int, peak_abs: float):
    """Pure gate for a captured recording: a reason string to skip
    transcription, or None when the audio is usable."""
    if num_samples < SAMPLE_RATE * 0.3:
        return "Recording too short"
    if peak_abs < QUIET_RECORD_PEAK:
        return "No speech captured"
    return None


# Whisper hallucinations on near-silence — short outputs containing one of
# these are treated as no speech, not commands.
TRANSCRIBE_HALLUCINATIONS = (
    "thank you for watching", "thanks for watching", "thank you for listening",
    "thanks for listening", "subscribe to", "see you next video",
    "see you in the next video", "stay tuned", "music [music]", "[music]", "♪",
)

# The one prompt sentence we feed Whisper — when it hears nothing it sometimes
# "transcribes" this prompt back at us. That echo is NEVER a user command, so
# it is filtered at any length (live incident 2026-09-22: a prompt echo reached
# the dispatcher and triggered a cloud LLM round-trip).
INITIAL_PROMPT = "The user gives a short spoken command or question."
_PROMPT_ECHO_MARKER = "the user gives a short spoken command"


def filter_hallucination(transcript: str) -> str:
    """Pure filter: returns '' for known silence-hallucination fillers and for
    echoes of our own initial prompt."""
    text = (transcript or "").strip()
    low = text.lower()
    if _PROMPT_ECHO_MARKER in low:
        _log(f"[VOICE] Prompt-echo hallucination filtered: {text[:60]!r}")
        return ""
    if low and len(low) < 60 and any(h in low for h in TRANSCRIBE_HALLUCINATIONS):
        _log(f"[VOICE] Hallucination pattern filtered: {text[:60]!r}")
        return ""
    return text


def is_voice_active():
    return _voice_active if not _voice_error else _voice_error

def is_enabled():
    return _enabled.is_set()

def set_voice_enabled(enabled: bool):
    """Toggle wake-word listening. When disabled, the audio thread closes the
    mic stream and idles; when re-enabled it reopens and recalibrates."""
    if enabled:
        _enabled.set()
        _log("[VOICE] Wake-word listening enabled by user.")
        _enqueue({"type": "mic", "listening": False, "note": "Restarting microphone..."})
    else:
        _enabled.clear()
        _log("[VOICE] Wake-word listening disabled by user (mic closed).")
        _set_mic_listening(False, "Wake word off")

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280        # 80ms at 16kHz — openWakeWord's expected frame size
WAKE_THRESHOLD = 0.25       # Lowered from 0.35 — near-miss logs showed real "Hey Jarvis"
                            # attempts scoring 0.25–0.30 on laptop/quiet mics
SILENCE_TIMEOUT = 1.0       # seconds of silence after speech before stopping
MAX_RECORD_SECS = 15        # hard cap on recording duration
MIC_SILENCE_RMS = 0.001     # below this the input is effectively delivering silence
                            # (muted/dead mics emit <0.001 of dither; a live mic in a
                            # quiet room sits above this even before anyone speaks)
VAD_FRAME = 512             # Silero VAD requires exactly 512-sample frames at 16 kHz
QUIET_RECORD_PEAK = 0.01    # recordings below this absolute peak had no audible speech

# One-time diagnostics state (populated by the audio thread)
_logged_predict_errors: set = set()
_last_near_miss_log = 0.0
# Energy-VAD fallback threshold, adapted to the measured ambient level at stream
# open (the fixed 0.01 was too high for low-gain mics and stalled end-of-speech).
_speech_energy_threshold = 0.01


# Runtime device override — set via set_mic_device() or JARVIS_MIC_DEVICE env.
# The audio thread picks this up on the *next* reconnect (stream close/reopen).
_device_override: str = ""


def set_mic_device(name: str):
    """Set the preferred microphone at runtime (no restart required — takes
    effect on the next stream reconnect, which is triggered automatically)."""
    global _device_override
    _device_override = (name or "").strip()
    _log(f"[VOICE] Runtime mic device set to: {_device_override!r}")


def list_input_devices() -> list:
    """Return a list of available audio input devices as dicts with id/name."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        return [
            {"id": i, "name": d["name"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
    except Exception as e:
        _log(f"[VOICE] Could not enumerate input devices: {e}")
        return []


def _resolve_input_device():
    """Pick the capture device. Priority: runtime _device_override >
    JARVIS_MIC_DEVICE env var > OS default input."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_idx = sd.default.device[0]
        # Runtime override takes highest priority
        name_filter = _device_override.lower() if _device_override else ""
        # Fall back to env var if no runtime override set
        if not name_filter:
            name_filter = (os.getenv("JARVIS_MIC_DEVICE", "") or "").strip().lower()
        if name_filter:
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0 and name_filter in d["name"].lower():
                    return i, d["name"]
            _log(f"[VOICE] Preferred mic '{name_filter}' matched no input device; using system default.")
        if default_idx is not None and default_idx >= 0:
            return default_idx, devices[default_idx]["name"]
        return None, "system default"
    except Exception as e:
        _log(f"[VOICE] Could not enumerate input devices: {e}")
        return None, "system default"

# ──────────────────────────────────────────────
# WebSocket client management
# ──────────────────────────────────────────────
def register_client(ws):
    with _lock:
        _clients.append(ws)
    # Immediately inform the new client of the real mic state, so the UI
    # indicator is correct from the first frame (WS-connected != listening).
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(ws.send_json(
                {"type": "mic", "listening": bool(_voice_active),
                 "note": _voice_error or ""}))
    except Exception:
        pass

def unregister_client(ws):
    with _lock:
        if ws in _clients:
            _clients.remove(ws)

def _enqueue(event: dict):
    """Thread-safe push into the async event queue."""
    if _event_queue is not None:
        try:
            _event_queue.put_nowait(event)
        except Exception:
            pass

# ──────────────────────────────────────────────
# Silero VAD helper (lightweight ONNX model)
# ──────────────────────────────────────────────
_vad_model = None

def _load_silero_vad():
    """Load Silero VAD via torch hub (or ONNX fallback). Lazy, may fail offline."""
    global _vad_model
    try:
        import torch
        model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=True,
            trust_repo=True
        )
        _vad_model = model
    except Exception as e:
        _log(f"[VOICE] Could not load Silero VAD: {e}")
        _log("[VOICE] Falling back to simple energy-based VAD")
        _vad_model = None

def _is_speech(audio_chunk_np: np.ndarray) -> bool:
    """Check if a 512-sample frame contains speech using Silero VAD or the
    energy fallback (threshold adapted to the ambient level at stream open)."""
    global _speech_energy_threshold
    if _vad_model is not None:
        try:
            import torch
            audio_tensor = torch.FloatTensor(audio_chunk_np)
            speech_prob = _vad_model(audio_tensor, SAMPLE_RATE)
            if hasattr(speech_prob, 'item'):
                speech_prob = speech_prob.item()
            return speech_prob > 0.5
        except Exception:
            pass

    # Energy-based fallback
    rms = float(np.sqrt(np.mean(audio_chunk_np.astype(np.float64) ** 2)))
    return rms > _speech_energy_threshold

# ──────────────────────────────────────────────
# Audio capture thread
# ──────────────────────────────────────────────
def _audio_thread():
    """Runs in a dedicated thread. Captures mic, detects wake word, records, transcribes."""
    global _voice_active, _voice_error, _last_near_miss_log
    import queue

    # Lazy imports — don't crash backend if audio libs missing
    try:
        import sounddevice as sd
        import openwakeword
        from openwakeword.model import Model as OWWModel
    except ImportError as e:
        _log(f"[VOICE] Voice dependencies missing: {e}. Voice features disabled.")
        _set_mic_listening(False, "Voice off (deps missing)")
        return
    except Exception as e:
        _log(f"[VOICE] Failed to import audio libs: {e}")
        _set_mic_listening(False, "Voice off (import error)")
        return

    try:
        _log("[VOICE] Downloading/loading openWakeWord 'hey_jarvis' model...")
        openwakeword.utils.download_models(["hey_jarvis"])
        oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        _log("[VOICE] Wake word model loaded. Listening for 'Hey Jarvis'...")
    except Exception as e:
        _log(f"[VOICE] Failed to load wake word model: {e}")
        _set_mic_listening(False, "Voice off (model load failed)")
        return

    # Try loading Silero VAD (non-fatal if fails)
    _load_silero_vad()

    recording = False
    recorded_frames: list[np.ndarray] = []
    vad_buffer = np.zeros(0, dtype=np.float32)  # rolling buffer feeding Silero 512-sample frames
    silence_start = 0.0
    record_start = 0.0

    audio_q: queue.Queue = queue.Queue(maxsize=100)

    def callback(indata, frames, time_info, status):
        if status:
            pass
        try:
            # Drop frames if queue full to avoid unbounded growth
            if audio_q.qsize() < 90:
                audio_q.put_nowait(indata[:, 0].copy().astype(np.float32))
        except Exception:
            pass

    # Retry loop for microphone — allows recovery if mic is plugged in later
    retry_delay = 5
    while True:
        if not _enabled.is_set():
            # User turned wake-word listening off: keep the mic closed so
            # Bluetooth headsets leave HFP and playback quality recovers.
            if _voice_active:
                _voice_active = False
                _voice_error = "Voice disabled by user"
                _log("[VOICE] Microphone closed (wake-word listening disabled).")
            time.sleep(0.5)
            continue
        dev_idx, dev_name = _resolve_input_device()
        try:
            with sd.InputStream(
                device=dev_idx,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                blocksize=CHUNK_SAMPLES,
                callback=callback,
            ):
                _log(f"[VOICE] Microphone stream open on '{dev_name}'. Wake word listener active.")
                _set_mic_listening(True)
                retry_delay = 5

                # Mic health check: a muted/dead input delivers pure silence, and
                # the wake word can never fire — warn loudly instead of silently
                # listening to nothing forever. Short window: Bluetooth mics stay
                # silent for the first few seconds while the Hands-Free profile
                # engages, and would otherwise false-alarm.
                hc_peak = 0.0
                hc_secs = 2.0
                hc_until = time.time() + hc_secs
                while time.time() < hc_until:
                    try:
                        hc_audio = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    hc_peak = max(hc_peak, float(np.sqrt(np.mean(hc_audio.astype(np.float64) ** 2))))
                if hc_peak < MIC_SILENCE_RMS:
                    _log(
                        f"[VOICE] WARNING: microphone '{dev_name}' is delivering silence "
                        f"(peak RMS {hc_peak:.5f} over {hc_secs:.0f}s) — wake word cannot fire. "
                        f"Check that the mic isn't muted and is the Windows default input, "
                        f"or set JARVIS_MIC_DEVICE in backend/.env to another input device name."
                    )
                    _enqueue({"type": "status",
                              "text": "Microphone appears muted or silent — wake word can't fire"})
                # Adapt the energy-VAD fallback to this mic's ambient level
                global _speech_energy_threshold
                _speech_energy_threshold = min(max(0.003, hc_peak * 3.0), 0.05)
                _log(f"[VOICE] Speech energy threshold set to {_speech_energy_threshold:.4f} "
                     f"(ambient peak {hc_peak:.4f}).")

                # Preload Whisper so the first command doesn't pay the cold-load cost
                threading.Thread(target=_preload_whisper, daemon=True, name="jarvis-whisper-preload").start()

                while True:
                    if not _enabled.is_set():
                        break  # closes the stream via the with-block
                    try:
                        audio = audio_q.get(timeout=1.0)
                    except queue.Empty:
                        continue

                    if is_ducked():
                        # VEGA is speaking: never wake on its own voice, drop any
                        # recording already in progress (it can only contain VEGA),
                        # and reset the model buffer so a wake right after speech
                        # needs a fresh user utterance.
                        if recording:
                            recording = False
                            recorded_frames = []
                            vad_buffer = np.zeros(0, dtype=np.float32)
                            _log("[VOICE] Dropped in-progress recording (VEGA was speaking).")
                        try:
                            oww.reset()
                        except Exception:
                            pass
                        continue

                    if not recording:
                        audio_int16 = (audio * 32767).astype(np.int16)
                        try:
                            oww.predict(audio_int16)
                        except Exception as e:
                            err_key = repr(e)[:150]
                            if err_key not in _logged_predict_errors:
                                _logged_predict_errors.add(err_key)
                                _log(f"[VOICE] Wake model predict failed: {err_key}")
                            continue

                        if wake_fired(oww.prediction_buffer, WAKE_THRESHOLD):
                            oww.reset()
                            recording = True
                            recorded_frames = []
                            vad_buffer = np.zeros(0, dtype=np.float32)
                            if _vad_model is not None and hasattr(_vad_model, "reset_states"):
                                try:
                                    _vad_model.reset_states()
                                except Exception:
                                    pass
                            silence_start = 0.0
                            record_start = time.time()
                            _enqueue({"type": "wake"})
                            _enqueue({"type": "listening"})
                        else:
                            top_score = 0.0
                            for score in oww.prediction_buffer.values():
                                if len(score) > 0:
                                    top_score = max(top_score, float(score[-1]))

                            if top_score >= WAKE_THRESHOLD * 0.5 \
                                    and time.time() - _last_near_miss_log > 5:
                                _last_near_miss_log = time.time()
                                _log(f"[VOICE] Near-miss wake score {top_score:.2f} "
                                     f"(threshold {WAKE_THRESHOLD}) — if these never climb, "
                                     f"check the mic level/device.")
                    else:
                        recorded_frames.append(audio)
                        elapsed = time.time() - record_start

                        # Silero VAD requires exactly 512-sample frames at 16 kHz
                        # and raises on the 1280-sample chunks — feed it through a
                        # rolling buffer so end-of-speech detection actually works.
                        vad_buffer = np.concatenate([vad_buffer, audio])
                        has_speech = False
                        while len(vad_buffer) >= VAD_FRAME:
                            frame = vad_buffer[:VAD_FRAME]
                            vad_buffer = vad_buffer[VAD_FRAME:]
                            if _is_speech(frame):
                                has_speech = True
                                break

                        if has_speech:
                            silence_start = 0.0
                        else:
                            if silence_start == 0.0:
                                silence_start = time.time()
                            elif (time.time() - silence_start) >= SILENCE_TIMEOUT:
                                recording = False
                                _enqueue({"type": "processing"})
                                frames_copy = recorded_frames.copy()
                                recorded_frames = []
                                threading.Thread(
                                    target=_transcribe_and_dispatch,
                                    args=(frames_copy,),
                                    daemon=True
                                ).start()
                                continue

                        if elapsed >= MAX_RECORD_SECS:
                            recording = False
                            _enqueue({"type": "processing"})
                            frames_copy = recorded_frames.copy()
                            recorded_frames = []
                            threading.Thread(
                                target=_transcribe_and_dispatch,
                                args=(frames_copy,),
                                daemon=True
                            ).start()

        except Exception as e:
            # Handle PortAudio errors gracefully without killing backend
            err_str = str(e).lower()
            if "no input" in err_str or "device" in err_str or "portaudio" in err_str:
                _log(f"[VOICE] Microphone not available: {e}. Retrying in {retry_delay}s...")
                _set_mic_listening(False, "Voice off (no microphone)")
            else:
                _log(f"[VOICE] Audio stream error: {e}. Retrying in {retry_delay}s...")
                _set_mic_listening(False, f"Voice error: {type(e).__name__}")

            # Clear queue
            while not audio_q.empty():
                try: audio_q.get_nowait()
                except: break
            recording = False
            recorded_frames = []

            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 60)  # exponential backoff up to 60s

# ──────────────────────────────────────────────
# Transcription
# ──────────────────────────────────────────────
_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # small.en: ~3% WER on clean speech (vs ~6-8% for base) — the accuracy
        # tier worth the extra CPU. Override with WHISPER_MODEL=base.en (faster)
        # or tiny.en (fastest) if latency matters more than accuracy.
        model_name = os.getenv("WHISPER_MODEL", "small.en")
        _log(f"[VOICE] Loading faster-whisper '{model_name}' model...")
        _whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _log("[VOICE] Whisper model loaded.")
    return _whisper_model

def _preload_whisper():
    """Load Whisper in the background so the first wake-word command doesn't
    pay the cold-load cost (~2s+) while the user waits."""
    try:
        t0 = time.time()
        _get_whisper()
        _log(f"[VOICE] Whisper preloaded in {time.time()-t0:.1f}s.")
    except Exception as e:
        _log(f"[VOICE] Whisper preload failed (will retry on first transcription): {e}")

def transcribe_source(src):
    """Transcribe an audio source (file path or file-like) with VEGA's guarded
    command settings — shared by the wake-word pipeline and the manual-mic
    /api/transcribe endpoint so both behave identically. Fully offline: local
    faster-whisper only, no network, no API key.

    Hallucination guards: no long vocab-biasing prompt (it bent off-topic
    speech toward app names), temperature pinned, timestamps skipped.
    Returns the cleaned transcript ('' for silence/hallucination fillers).
    """
    model = _get_whisper()
    t0 = time.time()
    segments, info = model.transcribe(
        src,
        beam_size=1,
        language="en",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False,
        without_timestamps=True,
        temperature=0.0,
        initial_prompt=INITIAL_PROMPT,
    )
    raw = " ".join(seg.text for seg in segments).strip()
    trimmed = getattr(info, "duration_after_vad", None) or getattr(info, "duration", 0)
    _log(f"[VOICE] Transcribed {trimmed:.1f}s of audio (after VAD trim) "
         f"in {time.time()-t0:.1f}s")
    return filter_hallucination(raw)


def _audio_to_wav_bytes(audio_np):
    """In-memory 16kHz mono int16 WAV from float32 frames."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes((audio_np * 32767).astype(np.int16).tobytes())
    buf.seek(0)
    return buf


def _transcribe_and_dispatch(frames: list[np.ndarray]):
    """Transcribe recorded audio and push result to WebSocket clients."""
    try:
        audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        reason = recording_skip_reason(len(audio), peak)
        if reason:
            # False wake / dead capture: tell the UI honestly instead of
            # burning ~15s of CPU transcribing silence.
            _log(f"[VOICE] {reason} (peak {peak:.4f}) — skipping transcription")
            _enqueue({"type": "status", "text": reason})
            return

        # Clip to [-1, 1] before int16 conversion to avoid overflow
        audio = np.clip(audio, -1.0, 1.0)
        transcript = transcribe_source(_audio_to_wav_bytes(audio))

        if transcript:
            _log(f"[VOICE] Transcript: {transcript}")
            _enqueue({"type": "transcript", "text": transcript})
        else:
            _log("[VOICE] Empty transcript")
            _enqueue({"type": "status", "text": "No speech detected"})

    except Exception as e:
        _log(f"[VOICE] Transcription error: {e}")
        _enqueue({"type": "error", "text": str(e)[:200]})

# ──────────────────────────────────────────────
# Async dispatcher (runs in the FastAPI event loop)
# ──────────────────────────────────────────────
async def _dispatch_events():
    """Read from the event queue and broadcast to all connected WS clients."""
    while True:
        event = await _event_queue.get()
        dead = []
        with _lock:
            clients_snapshot = list(_clients)
        for ws in clients_snapshot:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            unregister_client(ws)

# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def start(loop: asyncio.AbstractEventLoop):
    """Called once from FastAPI startup. Launches the audio thread + async dispatcher."""
    global _event_queue
    _event_queue = asyncio.Queue()

    t = threading.Thread(target=_audio_thread, daemon=True, name="jarvis-voice")
    t.start()

    loop.create_task(_dispatch_events())
    _log("[VOICE] Voice service started.")
