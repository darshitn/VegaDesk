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
from typing import Any, Optional
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
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_audio_thread_handle: threading.Thread = None
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


# Interruption handoff: when wake is detected while ducked, we enter a
# pending-interruption state. We continue to discard command audio and keep ducking
# active until the frontend physically cancels TTS and sends an acknowledgement
# correlated to the interruption ID. A bounded timeout fails closed if no ack arrives.
_interruption_pending: bool = False
_current_interruption_id: Optional[str] = None
_interruption_deadline: float = 0.0
_interruption_ack_event: threading.Event = threading.Event()
INTERRUPT_ACK_TIMEOUT: float = 1.5  # seconds: max wait for frontend to cancel TTS and ack


def is_interruption_pending() -> bool:
    global _interruption_pending, _current_interruption_id, _interruption_deadline
    if not _interruption_pending:
        return False
    if time.time() >= _interruption_deadline:
        _log(f"[VOICE] Interruption {_current_interruption_id!r} timed out waiting for client ack; fail closed.")
        _interruption_pending = False
        _current_interruption_id = None
        _interruption_deadline = 0.0
        _interruption_ack_event.clear()
        _enqueue({"type": "status", "text": "Interruption timed out"})
        return False
    return True


def start_interruption() -> str:
    """Enter pending-interruption state and emit interrupt wake event."""
    global _interruption_pending, _current_interruption_id, _interruption_deadline
    import uuid
    int_id = str(uuid.uuid4())[:8]
    _current_interruption_id = int_id
    _interruption_pending = True
    _interruption_deadline = time.time() + INTERRUPT_ACK_TIMEOUT
    _interruption_ack_event.clear()
    _log(f"[VOICE] Interruption detected while ducked. Entering pending state (id={int_id}).")
    _enqueue({"type": "wake", "interrupt": True, "interruption_id": int_id})
    return int_id


def acknowledge_interruption(interruption_id: Any) -> bool:
    """Frontend calls this after cancelling TTS, acknowledging the interruption.
    Returns True if valid and pending, False if stale, missing, or timed out."""
    global _interruption_pending, _current_interruption_id, _interruption_deadline
    req_id = str(interruption_id or "").strip()
    if not _interruption_pending:
        _log(f"[VOICE] Received ack for interruption {req_id!r}, but no interruption pending; ignoring.")
        return False
    if not _current_interruption_id or req_id != _current_interruption_id:
        _log(f"[VOICE] Stale or mismatching interruption ack {req_id!r} (expected {_current_interruption_id!r}); ignoring.")
        return False
    if time.time() >= _interruption_deadline:
        _log(f"[VOICE] Interruption ack {req_id!r} arrived after deadline; ignoring.")
        _interruption_pending = False
        _current_interruption_id = None
        _interruption_deadline = 0.0
        _interruption_ack_event.clear()
        return False

    _log(f"[VOICE] Interruption {req_id} acknowledged by client. Cancelling ducking and arming recording.")
    _interruption_pending = False
    _current_interruption_id = None
    _interruption_deadline = 0.0
    set_duck(False)
    _interruption_ack_event.set()
    return True


def cancel_interruption(reason: str = ""):
    """Fail closed on disconnect, mic disable, or explicit cancel."""
    global _interruption_pending, _current_interruption_id, _interruption_deadline
    if _interruption_pending:
        _log(f"[VOICE] Cancelled pending interruption ({reason or 'no reason'}).")
    _interruption_pending = False
    _current_interruption_id = None
    _interruption_deadline = 0.0
    _interruption_ack_event.clear()


def get_interruption_state() -> dict:
    """Introspection for tests and health."""
    return {
        "pending": bool(_interruption_pending and time.time() < _interruption_deadline),
        "interruption_id": _current_interruption_id,
        "deadline": _interruption_deadline,
        "ack_set": _interruption_ack_event.is_set(),
    }


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


def is_enabled():
    return _enabled.is_set()

def set_voice_enabled(enabled: bool):
    """Toggle wake-word listening. When disabled, the audio thread closes the
    mic stream and idles; when re-enabled it reopens and recalibrates."""
    global _voice_error
    if enabled:
        _enabled.set()
        _voice_error = ""
        _log("[VOICE] Wake-word listening enabled by user.")
        _enqueue({"type": "mic", "listening": False, "note": "Restarting microphone..."})
    else:
        _enabled.clear()
        cancel_interruption("voice disabled by user")
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
            if len(_clients) == 0:
                cancel_interruption("all clients disconnected")

def _enqueue(event: dict):
    """Thread-safe push into the async event queue."""
    if _event_queue is not None:
        try:
            if _event_loop is None:
                # Supports standalone synchronous consumers before start().
                # The running service always installs its owner loop first.
                _event_queue.put_nowait(event)
            else:
                _event_loop.call_soon_threadsafe(_event_queue.put_nowait, event)
        except RuntimeError:
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

def consume_vad_frames(buffer: np.ndarray, audio: np.ndarray, detector=None):
    """Feed every complete frame to stateful VAD, retaining <512 samples.

    Stopping on the first positive frame accumulates old speech and delays
    endpointing even after the speaker has finished.
    """
    detector = _is_speech if detector is None else detector
    pending = np.concatenate([buffer, audio])
    complete = len(pending) // VAD_FRAME * VAD_FRAME
    has_speech = False
    for offset in range(0, complete, VAD_FRAME):
        speech = detector(pending[offset:offset + VAD_FRAME])
        has_speech = bool(speech) or has_speech
    return has_speech, pending[complete:].copy()


def get_wake_phrase() -> str:
    """A configured label is not evidence that a model learned the phrase."""
    return os.getenv("VEGA_WAKE_PHRASE", "Hey Jarvis").strip() or "Hey Jarvis"


def get_wake_threshold() -> float:
    import math
    value = float(os.getenv("VEGA_WAKE_THRESHOLD", str(WAKE_THRESHOLD)))
    if not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError("VEGA_WAKE_THRESHOLD must be finite and in (0, 1]")
    return value


def resolve_wake_word_model() -> Optional[str]:
    """An explicit custom model never silently falls back to Jarvis."""
    custom_path = os.getenv("VEGA_WAKE_MODEL_PATH") or os.getenv("JARVIS_WAKE_MODEL_PATH")
    if custom_path:
        return custom_path if os.path.isfile(custom_path) and os.path.getsize(custom_path) > 0 else None
    if get_wake_phrase().lower() != "hey jarvis":
        return None
    try:
        import openwakeword
        oww_dir = os.path.dirname(openwakeword.__file__)
        candidates = [
            os.path.join(oww_dir, "resources", "models", "hey_jarvis_v0.1.onnx"),
            os.path.join(oww_dir, "resources", "models", "hey_jarvis.onnx"),
            os.path.join(oww_dir, "resources", "models", "hey_jarvis_v0.1.tflite"),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 0:
                return c
    except Exception:
        pass
    return None


def verify_cached_whisper_assets(model_name: str) -> tuple[bool, Optional[str]]:
    """Verify that required faster-whisper assets exist locally and are non-empty without downloading."""
    global _whisper_model
    if _whisper_model is not None:
        return True, None

    # 1. Custom model path check if configured
    custom_path = os.getenv("WHISPER_MODEL_PATH")
    if custom_path:
        if not os.path.isdir(custom_path):
            return False, f"Custom Whisper model path not found: {custom_path}"
        bin_f = os.path.join(custom_path, "model.bin")
        cfg_f = os.path.join(custom_path, "config.json")
        has_tok = any(
            os.path.isfile(os.path.join(custom_path, f)) and os.path.getsize(os.path.join(custom_path, f)) > 0
            for f in ["vocabulary.txt", "vocabulary.json", "tokenizer.json"]
        )
        if os.path.isfile(bin_f) and os.path.getsize(bin_f) > 0 and os.path.isfile(cfg_f) and os.path.getsize(cfg_f) > 0 and has_tok:
            return True, None
        return False, "Custom Whisper model directory incomplete: missing or empty model.bin, config.json, or vocabulary"

    # 2. Check HuggingFace hub cache roots
    cache_roots = []
    hf_home = os.getenv("HF_HOME") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if hf_home:
        cache_roots.extend([hf_home, os.path.join(hf_home, "hub")])
    cache_roots.extend([
        os.path.expanduser("~/.cache/huggingface/hub"),
        os.path.expanduser("~/.cache/huggingface"),
    ])

    repo_dir_names = [
        f"models--Systran--faster-whisper-{model_name}",
        f"models--{model_name.replace('/', '--')}" if "/" in model_name else f"models--{model_name}",
    ]

    found_repo_dir = None
    for root in cache_roots:
        if not os.path.isdir(root):
            continue
        for repo_name in repo_dir_names:
            candidate = os.path.join(root, repo_name)
            if os.path.isdir(candidate):
                found_repo_dir = candidate
                break
        if found_repo_dir:
            break

    if not found_repo_dir:
        return False, f"Whisper model '{model_name}' not cached locally"

    snapshots_dir = os.path.join(found_repo_dir, "snapshots")
    if not os.path.isdir(snapshots_dir):
        return False, f"Whisper model '{model_name}' cache incomplete: missing snapshots directory"

    try:
        snapshots = [
            os.path.join(snapshots_dir, s)
            for s in os.listdir(snapshots_dir)
            if os.path.isdir(os.path.join(snapshots_dir, s))
        ]
    except Exception as e:
        return False, f"Whisper model '{model_name}' snapshot access error: {e}"

    if not snapshots:
        return False, f"Whisper model '{model_name}' cache incomplete: no snapshot directories found"

    for snap in snapshots:
        bin_f = os.path.join(snap, "model.bin")
        cfg_f = os.path.join(snap, "config.json")
        has_tok = any(
            os.path.isfile(os.path.join(snap, f)) and os.path.getsize(os.path.join(snap, f)) > 0
            for f in ["vocabulary.txt", "vocabulary.json", "tokenizer.json"]
        )
        if os.path.isfile(bin_f) and os.path.getsize(bin_f) > 0 and os.path.isfile(cfg_f) and os.path.getsize(cfg_f) > 0 and has_tok:
            return True, None

    return False, f"Whisper model '{model_name}' cache incomplete: missing or empty model.bin, config.json, or vocabulary"


def check_voice_readiness() -> dict:
    """Truthful pre-flight check of installed dependencies and cached assets without network downloads."""
    report = {
        "deps_ready": False,
        "wake_word_ready": False,
        "whisper_ready": False,
        "mic_ready": False,
        "wake_model": None,
        "wake_phrase": get_wake_phrase(),
        "whisper_model": os.getenv("WHISPER_MODEL", "small.en"),
        "mic_device": None,
        "errors": [],
    }

    try:
        import sounddevice as sd
        import openwakeword
        from faster_whisper import WhisperModel
        report["deps_ready"] = True
    except ImportError as e:
        report["errors"].append(f"Missing dependency: {getattr(e, 'name', str(e))}")
        return report
    except Exception as e:
        report["errors"].append(f"Dependency error: {e}")
        return report

    wake_path = resolve_wake_word_model()
    if wake_path:
        report["wake_word_ready"] = True
        report["wake_model"] = os.path.basename(wake_path)
    else:
        report["errors"].append(f"Wake-word model for '{get_wake_phrase()}' missing or empty on disk")

    model_name = os.getenv("WHISPER_MODEL", "small.en")
    is_whisper_ready, whisper_err = verify_cached_whisper_assets(model_name)
    if is_whisper_ready:
        report["whisper_ready"] = True
    else:
        report["errors"].append(whisper_err or f"Whisper model '{model_name}' not cached locally")

    try:
        devices = sd.query_devices()
        input_devs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if input_devs:
            report["mic_ready"] = True
            report["mic_device"] = input_devs[0].get("name", "Unknown")
        else:
            report["errors"].append("No audio input device detected")
    except Exception as e:
        report["errors"].append(f"Audio device query error: {e}")

    return report


# ──────────────────────────────────────────────
# Audio capture thread
# ──────────────────────────────────────────────
def _audio_thread():
    """Runs in a dedicated thread. Captures mic, detects wake word, records, transcribes."""
    global _voice_active, _voice_error, _last_near_miss_log
    import queue
    try:
        wake_threshold = get_wake_threshold()
    except ValueError as exc:
        _set_mic_listening(False, str(exc))
        return

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

    wake_model_path = resolve_wake_word_model()
    if not wake_model_path:
        _log(f"[VOICE] Wake model for '{get_wake_phrase()}' missing or empty. Automatic downloads are disabled.")
        _set_mic_listening(False, "Voice off (wake model missing)")
        return

    model_name = os.getenv("WHISPER_MODEL", "small.en")
    is_whisper_ready, whisper_err = verify_cached_whisper_assets(model_name)
    if not is_whisper_ready:
        _log(f"[VOICE] {whisper_err}. Automatic downloads are disabled.")
        _set_mic_listening(False, "Voice off (whisper model missing or incomplete)")
        return

    try:
        _log(f"[VOICE] Loading local openWakeWord model from '{wake_model_path}'...")
        oww = OWWModel(wakeword_models=[wake_model_path], inference_framework="onnx" if wake_model_path.endswith(".onnx") else "tflite")
        _log(f"[VOICE] Wake word model loaded. Configured phrase: '{get_wake_phrase()}'.")
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
                        audio = audio_q.get(timeout=0.2)
                    except queue.Empty:
                        # Check pending interruption timeout even during queue empty
                        if is_interruption_pending():
                            pass
                        continue

                    # ── State 1: Acknowledged interruption transition ──
                    if _interruption_ack_event.is_set():
                        _interruption_ack_event.clear()
                        # Discard all queued pre-acknowledgement audio chunks!
                        # Any audio that arrived between the wake word and the frontend's
                        # TTS cancellation ack may contain speaker audio. Drop it completely.
                        while not audio_q.empty():
                            try:
                                audio_q.get_nowait()
                            except Exception:
                                break
                        recorded_frames = []
                        vad_buffer = np.zeros(0, dtype=np.float32)
                        if _vad_model is not None and hasattr(_vad_model, "reset_states"):
                            try:
                                _vad_model.reset_states()
                            except Exception:
                                pass
                        recording = True
                        silence_start = 0.0
                        record_start = time.time()
                        _enqueue({"type": "listening"})
                        _log("[VOICE] Clean command recording armed after verified interrupt ack.")
                        continue

                    # ── State 2: Pending interruption (waiting for client ack) ──
                    if is_interruption_pending():
                        # We detected 'Hey Jarvis' during TTS and notified frontend.
                        # While waiting for frontend to cancel TTS and acknowledge,
                        # continue discarding all incoming audio so speaker audio
                        # is never accumulated into command buffers.
                        continue

                    # ── State 3: Normal ducking (VEGA is speaking, no interrupt yet) ──
                    if is_ducked():
                        # If a recording was in progress when ducking began,
                        # drop it immediately so VEGA speech is never captured.
                        if recording:
                            recording = False
                            recorded_frames = []
                            vad_buffer = np.zeros(0, dtype=np.float32)
                            _log("[VOICE] Dropped in-progress recording (VEGA is speaking).")

                        # While VEGA is speaking: keep ordinary speech capture suppressed.
                        # However, allow bounded wake-word detection for interruption:
                        # if the owner says "Hey Jarvis", enter pending-interruption state.
                        audio_int16 = (audio * 32767).astype(np.int16)
                        try:
                            oww.predict(audio_int16)
                        except Exception as e:
                            err_key = repr(e)[:150]
                            if err_key not in _logged_predict_errors:
                                _logged_predict_errors.add(err_key)
                                _log(f"[VOICE] Wake model predict during ducking failed: {err_key}")
                            continue

                        if wake_fired(oww.prediction_buffer, wake_threshold):
                            _log("[VOICE] Spoken interruption detected: 'Hey Jarvis' during speech playback.")
                            oww.reset()
                            # Enter pending-interruption state:
                            # Do NOT un-duck yet! Do NOT start recording yet!
                            start_interruption()
                            # Purge audio queue and buffers
                            while not audio_q.empty():
                                try:
                                    audio_q.get_nowait()
                                except Exception:
                                    break
                            recorded_frames = []
                            vad_buffer = np.zeros(0, dtype=np.float32)
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

                        if wake_fired(oww.prediction_buffer, wake_threshold):
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

                            if top_score >= wake_threshold * 0.5 \
                                    and time.time() - _last_near_miss_log > 5:
                                _last_near_miss_log = time.time()
                                _log(f"[VOICE] Near-miss wake score {top_score:.2f} "
                                     f"(threshold {wake_threshold}) — if these never climb, "
                                     f"check the mic level/device.")
                    else:
                        recorded_frames.append(audio)
                        elapsed = time.time() - record_start

                        # Silero VAD requires exactly 512-sample frames at 16 kHz
                        # and raises on the 1280-sample chunks — feed it through a
                        # rolling buffer so end-of-speech detection actually works.
                        has_speech, vad_buffer = consume_vad_frames(vad_buffer, audio)

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
_whisper_load_lock = threading.Lock()
_transcribe_lock = threading.Lock()

def _get_whisper():
    global _whisper_model
    with _whisper_load_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            model_name = os.getenv("WHISPER_MODEL", "small.en")
            is_ready, err = verify_cached_whisper_assets(model_name)
            if not is_ready:
                raise RuntimeError(err or f"Whisper model '{model_name}' incomplete or missing from cache")
            source = os.getenv("WHISPER_MODEL_PATH") or model_name
            device = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
            if device not in ("cpu", "cuda"):
                raise ValueError("WHISPER_DEVICE must be cpu or cuda")
            compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8_float16" if device == "cuda" else "int8").strip()
            _log(f"[VOICE] Loading local faster-whisper '{model_name}' on {device}/{compute_type}...")
            _whisper_model = WhisperModel(source, device=device, compute_type=compute_type, local_files_only=True)
            _log("[VOICE] Whisper model loaded from local cache.")
    return _whisper_model

def _preload_whisper():
    """Load Whisper in the background so the first wake-word command doesn't
    pay the cold-load cost (~2s+) while the user waits."""
    global _voice_error
    try:
        t0 = time.time()
        _get_whisper()
        _log(f"[VOICE] Whisper preloaded in {time.time()-t0:.1f}s.")
    except Exception as e:
        _voice_error = f"Whisper load failed: {e}"
        _log(f"[VOICE] Whisper preload failed: {e}")

def transcribe_source(src):
    """Serialize manual and wake transcription, including lazy segment decoding."""
    with _transcribe_lock:
        return _transcribe_source_locked(src)


def _transcribe_source_locked(src):
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
    global _event_queue, _event_loop, _audio_thread_handle
    _event_loop = loop
    _event_queue = asyncio.Queue()

    _audio_thread_handle = threading.Thread(target=_audio_thread, daemon=True, name="jarvis-voice")
    _audio_thread_handle.start()

    loop.create_task(_dispatch_events())
    _log("[VOICE] Voice service started.")


def is_voice_active() -> bool:
    return bool(_voice_active)


def is_enabled() -> bool:
    return _enabled.is_set()


def _sanitize_voice_error(err: Any) -> Optional[str]:
    if err is None:
        return None
    err_str = str(err)
    if not err_str:
        return None
    import re
    err_lower = err_str.lower()
    if "disabled by user" in err_lower:
        return None
    if "wake word model" in err_lower or "wake model" in err_lower:
        return "Wake word model missing on disk"
    if "whisper" in err_lower and ("not cached" in err_lower or "not found" in err_lower or "load failed" in err_lower or "incomplete" in err_lower):
        return "Whisper model not cached or incomplete on disk"
    if "no microphone" in err_lower or "no input" in err_lower or "device" in err_lower:
        return "Microphone unavailable"
    # If the error string contains paths, strip to safe reason
    if re.search(r"[A-Za-z]:[\\/]|/(?:Users|home|root|tmp)/", err_str):
        return "Audio hardware unavailable"
    # Strip any potential tokens/secrets
    err_str = re.sub(r"(AIza[0-9A-Za-z-_]{35})", "<redacted>", err_str)
    err_str = re.sub(r"(sk-[0-9A-Za-z]{20,})", "<redacted>", err_str)
    err_str = re.sub(r"\b[0-9a-fA-F]{24,}\b", "<redacted>", err_str)
    return err_str[:120]


def get_status() -> dict:
    disabled = os.getenv("VEGA_DISABLE_VOICE", "").strip() in ("1", "true", "yes")
    user_disabled = not _enabled.is_set()
    model_name = os.getenv("WHISPER_MODEL", "small.en")
    wake_model_path = resolve_wake_word_model()
    wake_model_name = os.path.basename(wake_model_path) if wake_model_path else None

    if disabled:
        return {
            "status": "disabled",
            "active": False,
            "enabled": False,
            "wake_word_model": wake_model_name,
            "whisper_model": model_name,
            "error": None,
        }
    if user_disabled:
        return {
            "status": "disabled",
            "active": False,
            "enabled": False,
            "wake_word_model": wake_model_name,
            "whisper_model": model_name,
            "error": "Voice disabled by user",
        }
    if _voice_error:
        safe_err = _sanitize_voice_error(_voice_error)
        return {
            "status": "unavailable",
            "active": False,
            "enabled": _enabled.is_set(),
            "wake_word_model": wake_model_name,
            "whisper_model": model_name,
            "error": safe_err,
        }
    if _voice_active:
        return {
            "status": "ready",
            "active": True,
            "enabled": True,
            "wake_word_model": wake_model_name,
            "whisper_model": model_name,
            "error": None,
        }
    # Voice is enabled, not active, no error yet:
    # Starting only if the stream thread is actively running
    is_starting = bool(_event_queue is not None and _audio_thread_handle is not None and _audio_thread_handle.is_alive())
    status = "starting" if is_starting else "unavailable"
    return {
        "status": status,
        "active": False,
        "enabled": True,
        "wake_word_model": wake_model_name,
        "whisper_model": model_name,
        "error": None,
    }
