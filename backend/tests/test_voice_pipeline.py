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
