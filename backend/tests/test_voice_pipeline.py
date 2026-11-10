"""Production voice helpers; synthetic frames/fake models, no mic or downloads."""
import asyncio
import concurrent.futures
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import voice_service as voice


def test_vad_speech_does_not_accumulate_old_frames():
    remainder = np.zeros(0, dtype=np.float32)
    calls = []

    def detector(frame):
        calls.append(frame.copy())
        return bool(np.any(frame))

    # Eight seconds of speech used to leave 76,800 stale samples queued.
    for _ in range(100):
        speech, remainder = voice.consume_vad_frames(remainder, np.ones(1280), detector)
        assert speech
        assert len(remainder) < voice.VAD_FRAME
    assert len(calls) == 250
    speech, remainder = voice.consume_vad_frames(remainder, np.zeros(1280), detector)
    assert not speech  # next silence is evaluated immediately, not seconds later
    assert len(remainder) == 256


def test_vad_evaluates_frames_after_first_speech_and_preserves_remainder():
    seen = []
    audio = np.concatenate([np.ones(512), np.zeros(512), np.full(17, 0.25)])
    speech, remainder = voice.consume_vad_frames(np.zeros(0), audio, lambda f: seen.append(f.copy()) or np.any(f))
    assert speech and len(seen) == 2
    np.testing.assert_equal(remainder, np.full(17, 0.25))


def test_explicit_vega_model_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("VEGA_WAKE_PHRASE", "Hey Vega")
    monkeypatch.delenv("JARVIS_WAKE_MODEL_PATH", raising=False)
    monkeypatch.delenv("VEGA_WAKE_MODEL_PATH", raising=False)
    assert voice.resolve_wake_word_model() is None
    monkeypatch.setenv("VEGA_WAKE_MODEL_PATH", str(tmp_path / "missing.onnx"))
    assert voice.resolve_wake_word_model() is None
    artifact = tmp_path / "hey_vega.onnx"
    artifact.write_bytes(b"synthetic fixture; not a trained model")
    monkeypatch.setenv("VEGA_WAKE_MODEL_PATH", str(artifact))
    assert voice.resolve_wake_word_model() == str(artifact)
    assert voice.get_wake_phrase() == "Hey Vega"


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-0.1", "1.1", "invalid"])
def test_wake_threshold_rejects_invalid_operating_points(monkeypatch, value):
    monkeypatch.setenv("VEGA_WAKE_THRESHOLD", value)
    with pytest.raises(ValueError):
        voice.get_wake_threshold()


def test_selected_wake_threshold_is_used_by_gate(monkeypatch):
    monkeypatch.setenv("VEGA_WAKE_THRESHOLD", "0.6")
    assert not voice.wake_fired({"hey_vega": [0.59]}, voice.get_wake_threshold())
    assert voice.wake_fired({"hey_vega": [0.6]}, voice.get_wake_threshold())


def test_reenable_clears_previous_voice_error(monkeypatch):
    monkeypatch.setattr(voice, "_enabled", threading.Event())
    monkeypatch.setattr(voice, "_voice_error", "Wake word off")
    monkeypatch.setattr(voice, "_enqueue", lambda event: None)
    voice.set_voice_enabled(True)
    assert voice.is_enabled()
    assert voice._voice_error == ""


def test_whisper_initializes_once_and_loads_validated_custom_path(monkeypatch, tmp_path):
    calls = []
    model = object()

    def factory(source, **kwargs):
        calls.append((source, kwargs))
        time.sleep(0.02)
        return model

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=factory))
    monkeypatch.setattr(voice, "_whisper_model", None)
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda name: (True, None))
    monkeypatch.setenv("WHISPER_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.delenv("WHISPER_COMPUTE_TYPE", raising=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: voice._get_whisper(), range(3)))
    assert results == [model] * 3
    assert calls == [(str(tmp_path), {"device": "cuda", "compute_type": "int8_float16", "local_files_only": True})]


def test_enqueue_marshals_worker_event_to_asyncio_owner(monkeypatch):
    async def scenario():
        queue = asyncio.Queue()
        monkeypatch.setattr(voice, "_event_queue", queue)
        monkeypatch.setattr(voice, "_event_loop", asyncio.get_running_loop())
        worker = threading.Thread(target=voice._enqueue, args=({"type": "listening"},))
        worker.start()
        event = await asyncio.wait_for(queue.get(), timeout=1)
        worker.join(timeout=1)
        assert event == {"type": "listening"}
    asyncio.run(scenario())


def test_transcription_lock_covers_lazy_segment_iteration(monkeypatch):
    active = 0
    peak = 0

    class Model:
        def transcribe(self, source, **kwargs):
            def segments():
                nonlocal active, peak
                active += 1
                peak = max(active, peak)
                time.sleep(0.02)
                yield SimpleNamespace(text="test command")
                active -= 1
            return segments(), SimpleNamespace(duration=1)

    monkeypatch.setattr(voice, "_get_whisper", lambda: Model())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(voice.transcribe_source, ["a", "b"])) == ["test command"] * 2
    assert peak == 1


# ── Bounded admission & stale-result invalidation tests ──────────────

def test_transcription_worker_admission_bounded_and_busy_rejection(monkeypatch):
    """At most one worker runs; additional submissions rejected while busy; fresh runs after."""
    frames = [np.full(voice.CHUNK_SAMPLES, 0.5, dtype=np.float32) for _ in range(10)]
    stall_event = threading.Event()
    decode_entered = threading.Event()
    events_emitted = []

    def fake_transcribe(src):
        decode_entered.set()
        stall_event.wait(timeout=5)
        return "command 1"

    def fake_enqueue(ev, session_token=None, *args, **kwargs):
        events_emitted.append((dict(ev), session_token))

    monkeypatch.setattr(voice, "transcribe_source", fake_transcribe)
    monkeypatch.setattr(voice, "_enqueue_scoped", fake_enqueue)

    token = voice.get_session_generation()
    try:
        assert voice._submit_transcription(frames, token) is True
        assert decode_entered.wait(timeout=2)
        assert voice.is_transcription_busy() is True

        # Rapid additional submissions must be rejected without spawning new workers
        for _ in range(5):
            assert voice._submit_transcription(frames, token) is False

        busy_statuses = [e for e, _ in events_emitted if e.get("text") == "Transcription busy; recording dropped"]
        assert len(busy_statuses) == 5
    finally:
        stall_event.set()
        for _ in range(50):
            if not voice.is_transcription_busy():
                break
            time.sleep(0.02)
        voice._release_transcription_admission()

    assert voice.is_transcription_busy() is False

    # Fresh recording can now be admitted
    assert voice._submit_transcription(frames, token) is True
    for _ in range(50):
        if not voice.is_transcription_busy():
            break
        time.sleep(0.02)
    assert voice.is_transcription_busy() is False


def test_disable_during_decode_then_reenable_suppresses_old_and_admits_fresh(monkeypatch):
    """Disable during decode suppresses old transcript; reenable starts fresh session."""
    frames = [np.full(voice.CHUNK_SAMPLES, 0.5, dtype=np.float32) for _ in range(10)]
    decode_started = threading.Event()
    decode_proceed = threading.Event()
    emitted = []

    def fake_transcribe(src):
        decode_started.set()
        decode_proceed.wait(timeout=5)
        return "old transcript"

    def fake_enqueue(ev, session_token=None, *args, **kwargs):
        emitted.append((dict(ev), session_token))

    monkeypatch.setattr(voice, "transcribe_source", fake_transcribe)
    monkeypatch.setattr(voice, "_enqueue_scoped", fake_enqueue)

    token1 = voice.get_session_generation()
    try:
        assert voice._submit_transcription(frames, token1) is True
        assert decode_started.wait(timeout=2)

        # Disable and re-enable during decode
        voice.set_voice_enabled(False)
        voice.set_voice_enabled(True)

        # Let the old decode finish
        decode_proceed.set()
        for _ in range(50):
            if not voice.is_transcription_busy():
                break
            time.sleep(0.02)

        # Verify old transcript was never emitted
        old_transcripts = [e for e, _ in emitted if e.get("text") == "old transcript"]
        assert old_transcripts == []

        # Fresh recording in new session succeeds
        token2 = voice.get_session_generation()
        assert token2 != token1
        monkeypatch.setattr(voice, "transcribe_source", lambda src: "fresh transcript")
        assert voice._submit_transcription(frames, token2) is True
        for _ in range(50):
            if not voice.is_transcription_busy():
                break
            time.sleep(0.02)

        fresh_transcripts = [e for e, tok in emitted if e.get("text") == "fresh transcript" and tok == token2]
        assert len(fresh_transcripts) == 1
    finally:
        decode_proceed.set()
        voice.set_voice_enabled(True)
        voice._release_transcription_admission()


def test_device_reset_and_last_client_disconnect_invalidate_old_results(monkeypatch):
    """Device reset and last-client disconnect invalidate sessions and protect new clients."""
    # 1. Device reset invalidation
    t1 = voice.get_session_generation()
    voice.set_mic_device("test-mic-device")
    t2 = voice.get_session_generation()
    assert t2 > t1
    assert not voice.is_session_valid(t1)
    assert voice.is_session_valid(t2)

    # 2. Last-client disconnect invalidation & new client protection
    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    ws1 = FakeWS()
    ws2 = FakeWS()
    voice.register_client(ws1)
    gen_c1 = voice.get_session_generation()

    event_queue = asyncio.Queue()
    monkeypatch.setattr(voice, "_event_queue", event_queue)

    # Enqueue a transcript for gen_c1
    voice._enqueue_scoped({"type": "transcript", "text": "stale client1 text"}, gen_c1)

    # Last client disconnects
    voice.unregister_client(ws1)
    assert not voice.is_session_valid(gen_c1)

    # New client connects
    voice.register_client(ws2)

    async def drain_dispatch():
        task = asyncio.create_task(voice._dispatch_events())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(drain_dispatch())
        # ws2 must NOT receive the old transcript from ws1's session
        transcripts = [m for m in ws2.sent if m.get("type") == "transcript"]
        assert transcripts == []
    finally:
        voice.unregister_client(ws2)


def test_dispatch_rechecks_token_per_client_suspension_isolation(monkeypatch):
    """Client A send suspends, invalidation occurs, Client A completes, Client B receives nothing."""
    class BlockingWS:
        def __init__(self):
            self.sent = []
            self.send_started = threading.Event()
            self.send_proceed = threading.Event()

        async def send_json(self, msg):
            self.send_started.set()
            await asyncio.to_thread(self.send_proceed.wait, 5)
            self.sent.append(msg)

    class SecondWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, msg):
            self.sent.append(msg)

    ws_a = BlockingWS()
    ws_b = SecondWS()

    voice.register_client(ws_a)
    voice.register_client(ws_b)
    tok = voice.get_session_generation()

    event_queue = asyncio.Queue()
    monkeypatch.setattr(voice, "_event_queue", event_queue)
    event_queue.put_nowait({"type": "transcript", "text": "stale broadcast text", "_session_token": tok})

    async def run_scenario():
        task = asyncio.create_task(voice._dispatch_events())
        # Wait until Client A has started sending
        await asyncio.to_thread(ws_a.send_started.wait, 2)
        # Invalidate session while Client A is suspended
        voice._invalidate_session("invalidation during client A send")
        # Unblock Client A
        ws_a.send_proceed.set()
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(run_scenario())
        # Client A was in-flight and received it
        assert len(ws_a.sent) == 1
        # Client B must receive NOTHING because token was rechecked before B's send
        assert len(ws_b.sent) == 0
    finally:
        ws_a.send_proceed.set()
        voice.unregister_client(ws_a)
        voice.unregister_client(ws_b)


class StopAudioLoop(BaseException):
    """Deterministic exit sentinel for production audio thread loop."""
    pass


def _run_capture_script(monkeypatch, script, *, ducked=False, max_seconds=15):
    """Drive the real capture loop; only hardware, wake/VAD and time are fake."""
    from collections import deque
    import queue

    clock = SimpleNamespace(now=100.0)
    logs, submissions, vad_frames, events = [], [], [], []
    pending = deque()
    steps = iter(script)
    enabled = threading.Event()
    enabled.set()
    duck = threading.Event()
    if ducked:
        duck.set()
    for name, value in {
        "_enabled": enabled, "_duck_active": duck, "_duck_deadline": 1000.0,
        "_capture_reset_flag": threading.Event(), "_interruption_ack_event": threading.Event(),
        "_interruption_pending": False, "_current_interruption_id": None,
        "_interruption_deadline": 0.0, "_session_generation": 1,
        "_device_generation": 0, "_stream_reconnect_requested": False,
        "_active_audio_q": None, "_active_oww": None,
        "_voice_active": False, "_voice_error": "", "_speech_energy_threshold": 0.01,
        "_last_near_miss_log": 0.0, "MAX_RECORD_SECS": max_seconds,
    }.items():
        monkeypatch.setattr(voice, name, value)
    monkeypatch.setattr(voice, "time", SimpleNamespace(
        time=lambda: clock.now, monotonic=lambda: clock.now, sleep=lambda _: None))
    monkeypatch.setattr(voice, "_log", logs.append)
    monkeypatch.setattr(voice, "_enqueue", lambda event: events.append(dict(event)))
    monkeypatch.setattr(voice, "_enqueue_scoped", lambda event, token: events.append(dict(event)))
    monkeypatch.setattr(voice, "get_wake_threshold", lambda: 0.5)

    class ScriptedQueue:
        calibrated = False
        purged = 0

        def get(self, timeout=0.2):
            if not self.calibrated:
                self.calibrated = True
                clock.now += 2.1
                return np.full(voice.CHUNK_SAMPLES, 0.91, dtype=np.float32)
            try:
                amplitude, action = next(steps)
            except StopIteration:
                raise StopAudioLoop("script ended without submission")
            clock.now += 0.08
            if action:
                action(pending)
            return np.full(voice.CHUNK_SAMPLES, amplitude, dtype=np.float32)

        def empty(self):
            return not pending

        def get_nowait(self):
            self.purged += 1
            return pending.popleft()

    audio_queue = ScriptedQueue()
    monkeypatch.setattr(queue, "Queue", lambda maxsize: audio_queue)

    class FakeWake:
        def __init__(self, **kwargs):
            self.prediction_buffer = {}

        def reset(self):
            self.prediction_buffer = {}

        def predict(self, audio):
            # Only the uniquely marked trigger chunk fires wake detection.
            score = 0.99 if np.all(audio == int(0.23 * 32767)) else 0.0
            self.prediction_buffer = {"hey_jarvis": [score]}

    class FakeVAD:
        resets = 0

        def reset_states(self):
            self.resets += 1

    vad = FakeVAD()
    monkeypatch.setattr(voice, "_vad_model", vad)

    def detect(frame):
        assert vad.resets >= 2  # Stream initialization and recording arm.
        vad_frames.append(frame.copy())
        return bool(np.any(frame > 0.2))

    monkeypatch.setattr(voice, "_is_speech", detect)
    monkeypatch.setattr(voice, "_load_silero_vad", lambda: None)
    monkeypatch.setattr(voice, "_preload_whisper", lambda: None)
    monkeypatch.setattr(voice, "resolve_wake_word_model", lambda: "synthetic.onnx")
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda _: (True, None))
    monkeypatch.setattr(voice, "_resolve_input_device", lambda: (0, "synthetic input"))
    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openwakeword.model", SimpleNamespace(Model=FakeWake))

    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=FakeStream))
    # The only thread created before our intercepted submission is preload.
    monkeypatch.setattr(voice.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))

    def submit(frames, token):
        assert voice.is_session_valid(token)
        submissions.append(np.concatenate(frames))
        raise StopAudioLoop("captured")

    monkeypatch.setattr(voice, "_submit_transcription", submit)
    with pytest.raises(StopAudioLoop, match="^captured$"):
        voice._audio_thread()
    return submissions[0], vad_frames, logs, audio_queue.purged, events


def test_ordinary_wake_retains_trigger_once_through_real_capture_and_vad(monkeypatch):
    script = [(0.11, None), (0.23, None), (0.37, None)] + [(0.0, None)] * 20
    audio, vad_frames, logs, purged, events = _run_capture_script(monkeypatch, script)
    np.testing.assert_equal(audio[:1280], np.full(1280, 0.23, dtype=np.float32))
    np.testing.assert_equal(audio[1280:2560], np.full(1280, 0.37, dtype=np.float32))
    assert np.count_nonzero(audio == np.float32(0.23)) == 1280
    assert not np.any(audio == np.float32(0.11))
    assert not np.any(audio == np.float32(0.91))  # Calibration never enters capture.
    processed = np.concatenate(vad_frames)
    np.testing.assert_equal(processed, audio[:len(processed)])
    assert len(audio) - len(processed) < voice.VAD_FRAME
    assert purged == 0
    assert [e["type"] for e in events].count("wake") == 1
    assert any("endpoint=silence" in line and "silence_processing_seconds=" in line for line in logs)


def test_interruption_ack_still_discards_trigger_transition_and_queued_audio(monkeypatch):
    def acknowledge(pending):
        pending.extend([np.full(1280, 0.73), np.full(1280, 0.79)])
        assert voice.acknowledge_interruption(voice._current_interruption_id)

    script = [(0.23, None), (0.59, None), (0.61, acknowledge), (0.37, None)]
    script += [(0.0, None)] * 20
    audio, vad_frames, logs, purged, events = _run_capture_script(monkeypatch, script, ducked=True)
    np.testing.assert_equal(audio[:1280], np.full(1280, 0.37, dtype=np.float32))
    assert set(np.unique(audio)) == {np.float32(0.0), np.float32(0.37)}
    processed = np.concatenate(vad_frames)
    np.testing.assert_equal(processed, audio[:len(processed)])
    assert purged == 2
    assert any(e.get("interrupt") is True for e in events)
    assert any("endpoint=silence" in line for line in logs)


def test_detection_chunk_counts_toward_exact_audio_limit(monkeypatch):
    script = [(0.23, None)] + [(0.37, None)] * 10
    audio, _, logs, _, _ = _run_capture_script(monkeypatch, script, max_seconds=0.2)
    assert len(audio) == int(voice.SAMPLE_RATE * 0.2)
    assert np.count_nonzero(audio == np.float32(0.23)) == 1280
    assert any("endpoint=limit" in line and "audio_seconds=0.200" in line for line in logs)


def test_decode_timing_is_monotonic_and_excludes_model_load(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    logs = []

    class FakeModel:
        def transcribe(self, source, **kwargs):
            clock.now += 0.4

            def segments():
                clock.now += 0.6
                yield SimpleNamespace(text="private test words")

            return segments(), SimpleNamespace(duration_after_vad=2.0)

    def load():
        clock.now += 7.0
        return FakeModel()

    monkeypatch.setattr(voice, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(voice, "_get_whisper", load)
    monkeypatch.setattr(voice, "_log", logs.append)
    assert voice.transcribe_source("synthetic source") == "private test words"
    assert len(logs) == 1 and "decode_seconds=1.000" in logs[0]
    assert "excludes model load" in logs[0]
    assert "private test words" not in logs[0]


def test_audio_thread_first_stream_reaches_enter_without_unbound_local(monkeypatch):
    """First stream reaches fake InputStream.__enter__ without UnboundLocalError."""
    entered = []

    class FakeInputStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            entered.append(True)
            raise StopAudioLoop("first stream entered")

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_sd = SimpleNamespace(
        InputStream=FakeInputStream,
        query_devices=lambda: [{"name": "Default Mic", "max_input_channels": 1}],
        default=SimpleNamespace(device=[0, -1]),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    class FakeOWWModel:
        def __init__(self, **kwargs):
            self.prediction_buffer = {}

        def reset(self):
            pass

    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openwakeword.model", SimpleNamespace(Model=FakeOWWModel))
    monkeypatch.setattr(voice, "resolve_wake_word_model", lambda: "fake/hey_jarvis.onnx")
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda name: (True, None))
    monkeypatch.setattr(voice, "_load_silero_vad", lambda: None)
    monkeypatch.setattr(voice, "_preload_whisper", lambda: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    monkeypatch.setattr(voice, "_resolve_input_device", lambda: (0, "Default Mic"))

    with pytest.raises(StopAudioLoop):
        voice._audio_thread()

    assert entered == [True]


def test_audio_thread_pending_and_injected_device_changes_consumed_without_infinite_loop(monkeypatch):
    """A pending device change before resolution is consumed, and device change injected by resolver forces retry."""
    resolver_calls = 0
    opened_devices = []

    class FakeInputStream:
        def __init__(self, device=None, **kwargs):
            self.device = device

        def __enter__(self):
            opened_devices.append(self.device)
            raise StopAudioLoop("stream opened successfully")

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_sd = SimpleNamespace(
        InputStream=FakeInputStream,
        query_devices=lambda: [{"name": "Dev0", "max_input_channels": 1}, {"name": "Dev1", "max_input_channels": 1}],
        default=SimpleNamespace(device=[0, -1]),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    class FakeOWWModel:
        def __init__(self, **kwargs):
            self.prediction_buffer = {}

        def reset(self):
            pass

    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openwakeword.model", SimpleNamespace(Model=FakeOWWModel))
    monkeypatch.setattr(voice, "resolve_wake_word_model", lambda: "fake/hey_jarvis.onnx")
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda name: (True, None))
    monkeypatch.setattr(voice, "_load_silero_vad", lambda: None)
    monkeypatch.setattr(voice, "_preload_whisper", lambda: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    # 1. Set pending device change before resolution:
    voice.set_mic_device("initial_override")

    def mock_resolver():
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 1:
            # Verify pending reconnect flag was consumed BEFORE resolution
            assert voice._stream_reconnect_requested is False
            # Simulate another change occurring during resolution:
            voice.set_mic_device("during_resolution_device")
            return (0, "initial_override")
        elif resolver_calls == 2:
            return (1, "during_resolution_device")
        else:
            raise StopAudioLoop("resolver bound exceeded")

    monkeypatch.setattr(voice, "_resolve_input_device", mock_resolver)

    with pytest.raises(StopAudioLoop) as exc_info:
        voice._audio_thread()

    assert "stream opened successfully" in str(exc_info.value)
    assert resolver_calls == 2
    assert opened_devices == [1]


def test_audio_thread_device_change_inside_input_stream_reopens_next_stream(monkeypatch):
    """Change device inside fake InputStream.__enter__ reopens next stream without terminating audio thread."""
    entered_streams = []

    class FakeInputStream:
        def __init__(self, device=None, **kwargs):
            self.device = device

        def __enter__(self):
            entered_streams.append(self.device)
            if len(entered_streams) == 1:
                # Trigger device change during stream open / initial entry:
                voice.set_mic_device("dev1")
                return self
            # Second stream opens on dev1!
            raise StopAudioLoop("second stream entered successfully")

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_sd = SimpleNamespace(
        InputStream=FakeInputStream,
        query_devices=lambda: [{"name": "dev0", "max_input_channels": 1}, {"name": "dev1", "max_input_channels": 1}],
        default=SimpleNamespace(device=[0, -1]),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    class FakeOWWModel:
        def __init__(self, **kwargs):
            self.prediction_buffer = {}

        def reset(self):
            pass

    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openwakeword.model", SimpleNamespace(Model=FakeOWWModel))
    monkeypatch.setattr(voice, "resolve_wake_word_model", lambda: "fake/hey_jarvis.onnx")
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda name: (True, None))
    monkeypatch.setattr(voice, "_load_silero_vad", lambda: None)
    monkeypatch.setattr(voice, "_preload_whisper", lambda: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)

    monkeypatch.setattr(voice, "_resolve_input_device", lambda: (0, "dev0") if len(entered_streams) == 0 else (1, "dev1"))

    with pytest.raises(StopAudioLoop) as exc_info:
        voice._audio_thread()

    assert "second stream entered successfully" in str(exc_info.value)
    assert entered_streams == [0, 1]


def test_audio_thread_external_requests_do_not_mutate_models_synchronously_and_old_audio_rejected(monkeypatch):
    """External reset requests do NOT synchronously mutate model state; owning audio thread resets models and rejects old audio."""
    class FakeOWWModel:
        def __init__(self, **kwargs):
            self.prediction_buffer = {"hey_jarvis": [0.0]}
            self.reset_count = 0
            self.predict_count = 0

        def predict(self, audio):
            self.predict_count += 1
            self.prediction_buffer = {"hey_jarvis": [0.99]}

        def reset(self):
            self.reset_count += 1
            self.prediction_buffer = {"hey_jarvis": [0.0]}

    fake_oww_instance = None

    def oww_factory(**kwargs):
        nonlocal fake_oww_instance
        fake_oww_instance = FakeOWWModel(**kwargs)
        return fake_oww_instance

    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openwakeword.model", SimpleNamespace(Model=oww_factory))

    class FakeVAD:
        def __init__(self):
            self.reset_states_count = 0

        def reset_states(self):
            self.reset_states_count += 1

    fake_vad = FakeVAD()
    monkeypatch.setattr(voice, "_vad_model", fake_vad)

    captured_submissions = []

    def fake_submit(frames, session_token):
        captured_submissions.append((frames, session_token))
        return True

    monkeypatch.setattr(voice, "_submit_transcription", fake_submit)

    class FakeInputStream:
        def __init__(self, callback=None, **kwargs):
            self.callback = callback

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_sd = SimpleNamespace(
        InputStream=FakeInputStream,
        query_devices=lambda: [{"name": "Mic", "max_input_channels": 1}],
        default=SimpleNamespace(device=[0, -1]),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    monkeypatch.setattr(voice, "resolve_wake_word_model", lambda: "fake/hey_jarvis.onnx")
    monkeypatch.setattr(voice, "verify_cached_whisper_assets", lambda name: (True, None))
    monkeypatch.setattr(voice, "_load_silero_vad", lambda: None)
    monkeypatch.setattr(voice, "_preload_whisper", lambda: None)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    monkeypatch.setattr(voice, "_resolve_input_device", lambda: (0, "Mic"))

    # Part A: External request must NOT synchronously mutate model state
    fake_standalone_oww = FakeOWWModel()
    monkeypatch.setattr(voice, "_active_oww", fake_standalone_oww)
    voice._reset_capture_state()
    assert fake_standalone_oww.reset_count == 0
    assert fake_vad.reset_states_count == 0
    assert voice._capture_reset_flag.is_set() is True

    # Part B: In the actual audio thread, old audio cannot be newly authorized after invalidation
    step = 0

    class ControlledQueue:
        def qsize(self):
            return 1

        def put_nowait(self, item):
            pass

        def empty(self):
            return True

        def get_nowait(self):
            raise Exception()

        def get(self, timeout=0.2):
            nonlocal step
            step += 1
            if step == 1:
                # First chunk for mic health check
                return np.full(voice.CHUNK_SAMPLES, 0.05, dtype=np.float32)
            elif step == 2:
                # Invalidate stream session while audio chunk is in flight!
                voice._invalidate_session("external invalidation before wake processing")
                # Return chunk
                return np.full(voice.CHUNK_SAMPLES, 0.05, dtype=np.float32)
            else:
                raise StopAudioLoop("captured audio step finished")

    import queue as pyqueue
    monkeypatch.setattr(pyqueue, "Queue", lambda maxsize=100: ControlledQueue())

    with pytest.raises(StopAudioLoop):
        voice._audio_thread()

    # No submission was made with the invalidated session or blessed with the new session!
    assert captured_submissions == []


def test_delayed_enqueue_callback_and_delayed_delivery_both_suppressed(monkeypatch):
    """Old transcripts suppressed at async enqueue callback AND before websocket delivery."""
    # Boundary A: delayed async enqueue callback
    async def scenario_callback():
        queue = asyncio.Queue()
        monkeypatch.setattr(voice, "_event_queue", queue)
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(voice, "_event_loop", loop)
        tok = voice.get_session_generation()
        # Enqueue scheduled via call_soon_threadsafe
        voice._enqueue_scoped({"type": "transcript", "text": "suppressed text"}, tok)
        # Invalidate before callback executes
        voice._invalidate_session("test callback invalidation")
        await asyncio.sleep(0.02)
        assert queue.empty()

    asyncio.run(scenario_callback())

    # Boundary B: delayed queued delivery
    async def scenario_delivery():
        queue = asyncio.Queue()
        monkeypatch.setattr(voice, "_event_queue", queue)
        tok = voice.get_session_generation()
        # Item already in queue with token
        queue.put_nowait({"type": "transcript", "text": "queued text", "_session_token": tok})
        # Invalidate before delivery loop dequeues
        voice._invalidate_session("test delivery invalidation")

        class FakeWS:
            def __init__(self):
                self.sent = []

            async def send_json(self, msg):
                self.sent.append(msg)

        ws = FakeWS()
        monkeypatch.setattr(voice, "_clients", [ws])
        task = asyncio.create_task(voice._dispatch_events())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert ws.sent == []

    asyncio.run(scenario_delivery())


def test_old_partial_recording_rejected_by_new_generation_and_reset_state():
    """Gating uses session where recording began; reset state clears partial frames."""
    frames = [np.full(voice.CHUNK_SAMPLES, 0.5, dtype=np.float32) for _ in range(5)]
    g1 = voice.get_session_generation()
    voice._reset_capture_state()
    voice._invalidate_session("stream reconnect")
    g2 = voice.get_session_generation()
    assert g2 > g1

    # Attempting to submit recording under g1 fails closed
    assert voice._submit_transcription(frames, g1) is False
    assert voice._capture_reset_flag.is_set()


def test_admission_released_on_skip_error_and_start_failure(monkeypatch):
    """Busy admission released in finally on skip, error, and worker start failure."""
    frames = [np.full(voice.CHUNK_SAMPLES, 0.5, dtype=np.float32) for _ in range(10)]
    tok = voice.get_session_generation()

    # 1. Skip release (empty frames -> skip reason)
    assert voice._submit_transcription([], tok) is True
    for _ in range(50):
        if not voice.is_transcription_busy():
            break
        time.sleep(0.02)
    assert voice.is_transcription_busy() is False

    # 2. Error release
    def failing_transcribe(src):
        raise RuntimeError("synthetic whisper decode crash")

    monkeypatch.setattr(voice, "transcribe_source", failing_transcribe)
    assert voice._submit_transcription(frames, tok) is True
    for _ in range(50):
        if not voice.is_transcription_busy():
            break
        time.sleep(0.02)
    assert voice.is_transcription_busy() is False

    # 3. Worker start failure release and truthful terminal error
    emitted = []

    def fake_enqueue(ev, session_token=None, *args, **kwargs):
        emitted.append((dict(ev), session_token))

    monkeypatch.setattr(voice, "_enqueue_scoped", fake_enqueue)

    def failing_start(self):
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(threading.Thread, "start", failing_start)
    assert voice._submit_transcription(frames, tok) is False
    assert voice.is_transcription_busy() is False
    start_errors = [e for e, s in emitted if e.get("type") == "error" and s == tok]
    assert len(start_errors) == 1
    assert "Failed to start" in start_errors[0]["text"]

    # 4. Valid transcript arrives with clean public schema
    monkeypatch.undo()
    monkeypatch.setattr(voice, "transcribe_source", lambda src: "open firefox")
    valid_emitted = []

    def valid_fake_enqueue(ev, session_token=None, *args, **kwargs):
        valid_emitted.append((dict(ev), session_token))

    monkeypatch.setattr(voice, "_enqueue_scoped", valid_fake_enqueue)
    tok_fresh = voice.get_session_generation()
    assert voice._submit_transcription(frames, tok_fresh) is True
    for _ in range(50):
        if not voice.is_transcription_busy():
            break
        time.sleep(0.02)
    assert voice.is_transcription_busy() is False

    transcripts = [e for e, _ in valid_emitted if e.get("type") == "transcript"]
    assert transcripts == [{"type": "transcript", "text": "open firefox"}]
    assert "_session_token" not in transcripts[0]


def test_busy_worker_remains_bounded_across_invalidation(monkeypatch):
    """Busy worker retains single slot across invalidation; slot released when thread exits."""
    frames = [np.full(voice.CHUNK_SAMPLES, 0.5, dtype=np.float32) for _ in range(10)]
    stall_event = threading.Event()
    worker_started = threading.Event()

    def stalled_transcribe(src):
        worker_started.set()
        stall_event.wait(timeout=5)
        return "finished text"

    monkeypatch.setattr(voice, "transcribe_source", stalled_transcribe)
    tok = voice.get_session_generation()
    try:
        assert voice._submit_transcription(frames, tok) is True
        assert worker_started.wait(timeout=2)
        assert voice.is_transcription_busy() is True

        # Invalidate session during active decode
        voice._invalidate_session("test active decode invalidation")
        assert not voice.is_session_valid(tok)
        # Busy slot must STILL be occupied to prevent multiple workers
        assert voice.is_transcription_busy() is True
        assert voice._submit_transcription(frames, voice.get_session_generation()) is False
    finally:
        stall_event.set()
        for _ in range(50):
            if not voice.is_transcription_busy():
                break
            time.sleep(0.02)
        voice._release_transcription_admission()

    assert voice.is_transcription_busy() is False
