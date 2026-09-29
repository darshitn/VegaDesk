"""M2a voice-service unit tests: the gates and state events around the wake
pipeline, exercised WITHOUT any microphone or model. The heavy imports live
inside the audio thread, so the pure module surface (threshold gate, recording
quality gate, hallucination filter, ducking, mic-state events) is testable
headless. Transcription itself is covered by the live synthetic-speech check
in the M2a handoff, not here."""

import asyncio
import time

import numpy as np

import voice_service as vs


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


# ── wake gate ────────────────────────────────────────────────────────

def test_wake_fires_at_or_above_threshold():
    assert vs.wake_fired({"hey_jarvis": [0.1, 0.30]}, 0.25) is True
    assert vs.wake_fired({"hey_jarvis": [0.25]}, 0.25) is True  # boundary fires


def test_wake_does_not_fire_below_threshold_or_empty():
    assert vs.wake_fired({"hey_jarvis": [0.249]}, 0.25) is False
    assert vs.wake_fired({}, 0.25) is False
    assert vs.wake_fired({"hey_jarvis": []}, 0.25) is False


def test_wake_checks_every_model_in_buffer():
    assert vs.wake_fired({"a": [0.01], "b": [0.9]}, 0.5) is True


# ── recording quality gate ───────────────────────────────────────────

def test_short_recording_skipped():
    assert vs.recording_skip_reason(int(vs.SAMPLE_RATE * 0.29), 0.5) == "Recording too short"


def test_silent_recording_skipped():
    assert vs.recording_skip_reason(vs.SAMPLE_RATE, vs.QUIET_RECORD_PEAK * 0.5) == "No speech captured"


def test_usable_recording_passes():
    assert vs.recording_skip_reason(vs.SAMPLE_RATE, 0.1) is None


# ── hallucination filter ─────────────────────────────────────────────

def test_hallucination_fillers_become_empty():
    for t in ("Thank you for watching.", "[Music]", "thanks for watching!"):
        assert vs.filter_hallucination(t) == "", t


def test_prompt_echo_is_filtered_at_any_length():
    # Live incident: Whisper echoed its own initial prompt on a quiet capture
    # and the echo reached the dispatcher. Must never happen again.
    echo = vs.INITIAL_PROMPT
    assert vs.filter_hallucination(echo) == ""
    long_echo = (echo + " ") * 10  # repeated-loop variant, >60 chars
    assert vs.filter_hallucination(long_echo) == ""


def test_real_commands_are_kept():
    for t in ("open youtube", "set a timer for five minutes", "what are my pending tasks?"):
        assert vs.filter_hallucination(t) == t.strip(), t


def test_long_output_is_never_filtered():
    long_text = ("some long dictation that happens to end with "
                 "thank you for watching and keeps going well past sixty chars")
    assert len(long_text) >= 60
    assert vs.filter_hallucination(long_text) == long_text


# ── ducking (VEGA can't hear itself) ─────────────────────────────────

def test_duck_on_off_roundtrip():
    assert vs.is_ducked() is False
    vs.set_duck(True)
    assert vs.is_ducked() is True
    vs.set_duck(False)
    assert vs.is_ducked() is False


def test_duck_safety_expiry_rearms_mic():
    # If the client dies mid-speech and never clears the duck, the mic must
    # re-arm by itself after the ttl.
    vs.set_duck(True)
    try:
        future = time.time() + vs.DUCK_MAX_SECS + 10
        vs.time = type("T", (), {"time": staticmethod(lambda: future),
                                 "sleep": staticmethod(time.sleep)})
        assert vs.is_ducked() is False
    finally:
        vs.time = time
    assert vs.is_ducked() is False


# ── structured mic-state events ──────────────────────────────────────

def test_disable_and_enable_emit_mic_events():
    old_queue = vs._event_queue
    vs._event_queue = asyncio.Queue()
    try:
        vs.set_voice_enabled(False)
        events = _drain(vs._event_queue)
        mic = [e for e in events if e.get("type") == "mic"]
        assert mic and mic[-1]["listening"] is False
        assert mic[-1]["note"] == "Wake word off"
        assert vs.is_enabled() is False
        assert vs._voice_error == "Wake word off"

        vs.set_voice_enabled(True)
        events = _drain(vs._event_queue)
        mic = [e for e in events if e.get("type") == "mic"]
        assert mic and mic[-1]["listening"] is False  # restart pending
        assert mic[-1]["note"] == "Restarting microphone..."
        assert vs.is_enabled() is True
    finally:
        vs._event_queue = old_queue
        vs._enabled.set()
        vs._voice_error = ""


def test_set_mic_listening_updates_state_and_emits():
    old_queue = vs._event_queue
    vs._event_queue = asyncio.Queue()
    try:
        vs._set_mic_listening(True)
        e = vs._event_queue.get_nowait()
        assert e == {"type": "mic", "listening": True, "note": ""}
        assert vs._voice_active is True and vs._voice_error == ""

        vs._set_mic_listening(False, "Voice off (no microphone)")
        e = vs._event_queue.get_nowait()
        assert e["type"] == "mic" and e["listening"] is False
        assert e["note"] == "Voice off (no microphone)"
        assert vs._voice_error == "Voice off (no microphone)"
    finally:
        vs._event_queue = old_queue
        vs._voice_active = False
        vs._voice_error = ""


# ── transcription quality gates feed on real numpy shapes ────────────

def test_quiet_audio_gate_end_to_end_shape():
    frames = [np.zeros(vs.CHUNK_SAMPLES, dtype=np.float32) for _ in range(50)]
    audio = np.concatenate(frames)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    assert vs.recording_skip_reason(len(audio), peak) == "No speech captured"
