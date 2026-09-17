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

def is_voice_active():
    return _voice_active if not _voice_error else _voice_error

def is_enabled():
    return _enabled.is_set()

def set_voice_enabled(enabled: bool):
    """Toggle wake-word listening. When disabled, the audio thread closes the
    mic stream and idles; when re-enabled it reopens and recalibrates."""
    global _voice_active, _voice_error  # fix: without this, assignments below create locals
    if enabled:
        _enabled.set()
        _log("[VOICE] Wake-word listening enabled by user.")
    else:
        _voice_active = False
        _voice_error = "Voice disabled by user"
        _enabled.clear()
        _log("[VOICE] Wake-word listening disabled by user (mic closed).")
        _enqueue({"type": "status", "text": _voice_error})

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

# One-time diagnostics state (populated by the audio thread)
_logged_predict_errors: set = set()
_last_near_miss_log = 0.0


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
    # Immediately inform new client of current status
    if _voice_error:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(ws.send_json({"type": "status", "text": _voice_error}))
        except Exception:
            pass
    elif _voice_active:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(ws.send_json({"type": "status", "text": "Wake word listener active"}))
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
        _voice_error = "Voice disabled (dependencies missing)"
        _enqueue({"type": "status", "text": _voice_error})
        return
    except Exception as e:
        _log(f"[VOICE] Failed to import audio libs: {e}")
        _voice_error = "Voice disabled (import error)"
        _enqueue({"type": "status", "text": _voice_error})
        return

    try:
        _log("[VOICE] Downloading/loading openWakeWord 'hey_jarvis' model...")
        openwakeword.utils.download_models(["hey_jarvis"])
        oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        _log("[VOICE] Wake word model loaded. Listening for 'Hey Jarvis'...")
    except Exception as e:
        _log(f"[VOICE] Failed to load wake word model: {e}")
        _voice_error = f"Voice disabled (model load failed: {e})"
        _enqueue({"type": "status", "text": _voice_error})
        return

    # Try loading Silero VAD (non-fatal if fails)
    _load_silero_vad()

    _voice_active = True
    _voice_error = ""
    _enqueue({"type": "status", "text": "Wake word listener active"})

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
                _voice_active = True
                _voice_error = ""
                retry_delay = 5

                # Mic health check: a muted/dead input delivers pure silence, and
                # the wake word can never fire — warn loudly instead of silently
                # listening to nothing forever. 6s window: Bluetooth mics stay
                # silent for the first few seconds while the Hands-Free profile
                # engages, and would otherwise false-alarm.
                hc_peak = 0.0
                hc_until = time.time() + 2.0  # reduced from 6s → 2s to shrink the OWW warm-up blind window
                while time.time() < hc_until:
                    try:
                        hc_audio = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    hc_peak = max(hc_peak, float(np.sqrt(np.mean(hc_audio.astype(np.float64) ** 2))))
                if hc_peak < MIC_SILENCE_RMS:
                    _log(
                        f"[VOICE] WARNING: microphone '{dev_name}' is delivering silence "
                        f"(peak RMS {hc_peak:.5f} over 6s) — wake word cannot fire. "
                        f"Check that the mic isn't muted and is the Windows default input, "
                        f"or set JARVIS_MIC_DEVICE in backend/.env to another input device name."
                    )
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

                        top_score = 0.0
                        for model_name, score in oww.prediction_buffer.items():
                            if len(score) > 0 and score[-1] > WAKE_THRESHOLD:
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
                                break
                            if len(score) > 0:
                                top_score = max(top_score, float(score[-1]))

                        if not recording and top_score >= WAKE_THRESHOLD * 0.5 \
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
                _voice_active = False
                _voice_error = "Voice disabled (No Mic)"
                _enqueue({"type": "status", "text": _voice_error})
            else:
                _log(f"[VOICE] Audio stream error: {e}. Retrying in {retry_delay}s...")
                _enqueue({"type": "status", "text": f"Voice error: {e}"})

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

def _transcribe_and_dispatch(frames: list[np.ndarray]):
    """Transcribe recorded audio and push result to WebSocket clients."""
    try:
        audio = np.concatenate(frames)

        if len(audio) < SAMPLE_RATE * 0.3:
            _log("[VOICE] Recording too short, skipping")
            _enqueue({"type": "status", "text": "Recording too short"})
            return

        # Skip recordings that never contained audible speech (false wake) —
        # transcribing 15s of near-silence burns ~15s of CPU for nothing.
        peak = float(np.max(np.abs(audio)))
        if peak < QUIET_RECORD_PEAK:
            _log(f"[VOICE] Recording had no audible speech (peak {peak:.4f}) — skipping transcription")
            _enqueue({"type": "status", "text": "No speech captured"})
            return

        # Clip to [-1, 1] before int16 conversion to avoid overflow
        audio = np.clip(audio, -1.0, 1.0)

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        buf.seek(0)

        model = _get_whisper()

        # Hallucination guards: no long vocab-biasing prompt (it bent off-topic
        # speech toward app names), temperature pinned, timestamps skipped.
        t0 = time.time()
        segments, info = model.transcribe(
            buf,
            beam_size=1,
            language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=0.0,
            initial_prompt="The user gives a short spoken command or question.",
        )
        transcript = " ".join(seg.text for seg in segments).strip()
        trimmed = getattr(info, "duration_after_vad", None) or getattr(info, "duration", 0)
        _log(f"[VOICE] Transcribed {len(audio)/SAMPLE_RATE:.1f}s of audio "
             f"({trimmed:.1f}s after VAD trim) in {time.time()-t0:.1f}s")

        # Known Whisper hallucination fillers — treat as silence, not speech
        low = transcript.lower().strip()
        hallucinations = (
            "thank you for watching", "thanks for watching", "thank you for listening",
            "thanks for listening", "subscribe to", "see you in the next video",
            "stay tuned", "music [music]", "[music]", "♪",
        )
        if transcript and any(h in low for h in hallucinations) and len(low) < 60:
            _log(f"[VOICE] Hallucination pattern filtered: {transcript[:60]!r}")
            transcript = ""

        if transcript:
            _log(f"[VOICE] Transcript: {transcript}")
            _enqueue({"type": "transcript", "text": transcript})
        else:
            _log("[VOICE] Empty transcript")
            _enqueue({"type": "status", "text": "No speech detected"})

    except Exception as e:
        _log(f"[VOICE] Transcription error: {e}")
        _enqueue({"type": "error", "text": str(e)})

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
