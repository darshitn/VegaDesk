"""
Automated regression tests for Opt-In Voice Acceptance in Isolated Beta Profile.

Verifies:
1. Beta profile disables voice by default (VEGA_DISABLE_VOICE=1).
2. Opt-in voice mode (VEGA_BETA_VOICE=1) permits voice without enabling cloud inference.
3. Truthful GET /health, GET /api/voice/enabled, and POST /api/voice/enabled behaviors.
4. Rejection of POST /api/voice/enabled in voice-disabled profile mode.
5. Local wake-word model resolution and pre-flight readiness checks without network downloads.
6. Microphone stream enable/disable lifecycle and speech ducking invariants.
"""

import os
import sys
import importlib
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient
from beta_test_isolation import isolated_bootstrap

backend_dir = os.path.dirname(os.path.dirname(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


@pytest.fixture
def beta_voice_env(tmp_path):
    """Isolated environment fixture for beta voice testing."""
    saved_env = dict(os.environ)
    beta_db = str(tmp_path / "jarvis-beta.db")

    os.environ["JARVIS_DB_PATH"] = beta_db
    os.environ["VEGA_PROFILE"] = "beta"
    os.environ["VEGA_PROFILE_ROOT"] = str(tmp_path)
    os.environ["VEGA_RUN_ID"] = "test-beta-voice-run"
    os.environ["VEGA_PORT"] = "8005"
    os.environ["GEMINI_API_KEY"] = "leak-check-key"

    import db
    importlib.reload(db)
    import migrations
    migrations.run_migrations(db.engine, db.Base, beta_db)
    db.Base.metadata.create_all(bind=db.engine)

    yield beta_db

    db.engine.dispose()
    os.environ.clear()
    os.environ.update(saved_env)
    importlib.reload(db)


@pytest.fixture
def readiness_stub(monkeypatch):
    import voice_service

    report = {"deps_ready": False, "errors": ["Synthetic voice readiness"]}
    monkeypatch.setattr(voice_service, "check_voice_readiness", lambda: report)
    return report


@isolated_bootstrap
def test_beta_default_disables_voice(beta_voice_env, readiness_stub):
    """Ordinary beta profile must keep voice disabled by default."""
    # Ensure opt-in flags are unset
    os.environ.pop("VEGA_BETA_VOICE", None)
    os.environ.pop("VEGA_ENABLE_VOICE", None)

    import main
    importlib.reload(main)

    assert os.getenv("VEGA_DISABLE_VOICE") == "1"
    assert os.getenv("LLM_PROVIDER") == "none"
    assert os.getenv("GEMINI_API_KEY") is None  # Cloud keys stripped

    client = TestClient(main.app)

    # 1. GET /health reports voice_test_mode=False
    health_res = client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["profile"] == "beta"
    assert health_data["voice_test_mode"] is False
    assert health_data["voice_service"]["status"] == "disabled"
    assert health_data["deterministic_only"] is True

    # 2. GET /api/voice/enabled reports enabled=False
    enabled_res = client.get("/api/voice/enabled")
    assert enabled_res.status_code == 200
    enabled_data = enabled_res.json()
    assert enabled_data["enabled"] is False
    assert enabled_data["voice_test_mode"] is False
    assert enabled_data["readiness"] == readiness_stub

    # 3. POST /api/voice/enabled rejects activation with actionable error
    toggle_res = client.post("/api/voice/enabled", json={"enabled": True})
    assert toggle_res.status_code == 200
    toggle_data = toggle_res.json()
    assert toggle_data["enabled"] is False
    assert "npm run dev:beta:voice" in toggle_data["error"]


@isolated_bootstrap
def test_beta_opt_in_voice_mode(beta_voice_env, readiness_stub):
    """Beta profile with VEGA_BETA_VOICE=1 enables voice while preserving zero cloud leakage."""
    os.environ["VEGA_BETA_VOICE"] = "1"
    os.environ["GEMINI_API_KEY"] = "leak-check-key"

    import main
    importlib.reload(main)

    assert os.getenv("VEGA_DISABLE_VOICE") is None  # Voice enabled
    assert os.getenv("LLM_PROVIDER") == "none"      # Cloud inference disabled
    assert os.getenv("GEMINI_API_KEY") is None      # Cloud keys still stripped

    client = TestClient(main.app)

    # 1. GET /health reports voice_test_mode=True
    health_res = client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["profile"] == "beta"
    assert health_data["voice_test_mode"] is True
    assert health_data["deterministic_only"] is True

    # 2. GET /api/voice/enabled reports voice_test_mode=True
    enabled_res = client.get("/api/voice/enabled")
    assert enabled_res.status_code == 200
    enabled_data = enabled_res.json()
    assert enabled_data["voice_test_mode"] is True
    assert enabled_data["readiness"] == readiness_stub

    # 3. POST /api/voice/enabled can toggle listening state
    toggle_off = client.post("/api/voice/enabled", json={"enabled": False})
    assert toggle_off.status_code == 200
    assert toggle_off.json()["enabled"] is False

    toggle_on = client.post("/api/voice/enabled", json={"enabled": True})
    assert toggle_on.status_code == 200
    assert toggle_on.json()["enabled"] is True


def test_local_wake_word_resolution_no_download(tmp_path, monkeypatch):
    """resolve_wake_word_model resolves local files without downloading."""
    import voice_service

    # 1. Test custom path resolution
    fake_model = tmp_path / "custom_hey_jarvis.onnx"
    fake_model.write_bytes(b"ONNX_MODEL_CONTENT")

    monkeypatch.delenv("VEGA_WAKE_MODEL_PATH", raising=False)
    monkeypatch.setenv("JARVIS_WAKE_MODEL_PATH", str(fake_model))
    assert voice_service.resolve_wake_word_model() == str(fake_model)

    # A missing explicit path cannot fall back to any installed wake model.
    monkeypatch.setenv("JARVIS_WAKE_MODEL_PATH", str(tmp_path / "non_existent.onnx"))
    assert voice_service.resolve_wake_word_model() is None


def test_voice_readiness_reporting(tmp_path, monkeypatch):
    """Exercise readiness against synthetic files and a fake audio device."""
    import voice_service

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        query_devices=lambda: [{"name": "Synthetic mic", "max_input_channels": 1}],
    ))
    monkeypatch.setitem(sys.modules, "openwakeword", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=object))
    monkeypatch.setattr(voice_service, "_whisper_model", None)
    wake_path = tmp_path / "wake.onnx"
    wake_path.write_bytes(b"synthetic wake")
    whisper_path = tmp_path / "whisper"
    whisper_path.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (whisper_path / name).write_bytes(b"synthetic asset")
    monkeypatch.setenv("VEGA_WAKE_MODEL_PATH", str(wake_path))
    monkeypatch.setenv("WHISPER_MODEL_PATH", str(whisper_path))

    readiness = voice_service.check_voice_readiness()
    assert readiness["deps_ready"] is True
    assert readiness["wake_word_ready"] is True
    assert readiness["whisper_ready"] is True
    assert readiness["mic_ready"] is True
    assert readiness["mic_device"] == "Synthetic mic"
    assert readiness["errors"] == []


def test_wake_model_discovery_does_not_import_native_package(tmp_path, monkeypatch):
    import voice_service

    package = tmp_path / "openwakeword"
    models = package / "resources" / "models"
    models.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('native package executed')")
    artifact = models / "hey_jarvis_v0.1.onnx"
    artifact.write_bytes(b"synthetic model")
    monkeypatch.delitem(sys.modules, "openwakeword", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("VEGA_WAKE_MODEL_PATH", raising=False)
    monkeypatch.delenv("JARVIS_WAKE_MODEL_PATH", raising=False)
    monkeypatch.setenv("VEGA_WAKE_PHRASE", "Hey Jarvis")

    assert voice_service.resolve_wake_word_model() == str(artifact)
    assert "openwakeword" not in sys.modules


def test_voice_listening_toggle_and_ducking():
    """Voice service listening toggle and speech ducking invariants."""
    import voice_service

    # 1. Toggle enabled state
    voice_service.set_voice_enabled(False)
    assert voice_service.is_enabled() is False
    assert voice_service.is_voice_active() is False

    voice_service.set_voice_enabled(True)
    assert voice_service.is_enabled() is True

    # 2. Ducking lifecycle
    assert voice_service.is_ducked() is False

    voice_service.set_duck(True, ttl=10.0)
    assert voice_service.is_ducked() is True

    voice_service.set_duck(False)
    assert voice_service.is_ducked() is False

    # 3. Ducking expiration
    voice_service.set_duck(True, ttl=10.0)
    assert voice_service.is_ducked() is True
    import time
    voice_service._duck_deadline = time.time() - 0.1
    assert voice_service.is_ducked() is False


def test_whisper_cache_directory_alone_does_not_imply_usable_model(tmp_path):
    """An existing directory alone must not imply a usable Whisper model."""
    import voice_service

    saved_hf = os.getenv("HF_HOME")
    model_name = "test-check.en"
    hub_dir = tmp_path / "hub"
    repo_dir = hub_dir / f"models--Systran--faster-whisper-{model_name}"

    try:
        os.environ["HF_HOME"] = str(tmp_path)
        # 1. Non-existent directory
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is False
        assert "not cached locally" in err

        # 2. Directory exists but empty (no snapshots)
        repo_dir.mkdir(parents=True)
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is False
        assert "incomplete" in err

        # 3. Snapshots dir exists but has no snapshots
        snapshots_dir = repo_dir / "snapshots"
        snapshots_dir.mkdir()
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is False
        assert "incomplete" in err

        # 4. Snapshot exists but missing model.bin and config.json
        snap_a = snapshots_dir / "snap123"
        snap_a.mkdir()
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is False
        assert "incomplete" in err

        # 5. Snapshot has 0-byte model.bin
        bin_file = snap_a / "model.bin"
        bin_file.write_bytes(b"")
        cfg_file = snap_a / "config.json"
        cfg_file.write_bytes(b"{}")
        tok_file = snap_a / "vocabulary.txt"
        tok_file.write_bytes(b"vocab")
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is False
        assert "incomplete" in err

        # 6. Snapshot has all required non-empty files
        bin_file.write_bytes(b"CT2_WEIGHTS_BINARY_DATA")
        ready, err = voice_service.verify_cached_whisper_assets(model_name)
        assert ready is True
        assert err is None

    finally:
        if saved_hf is not None:
            os.environ["HF_HOME"] = saved_hf
        else:
            os.environ.pop("HF_HOME", None)


def test_zero_byte_wake_model_rejected(tmp_path, monkeypatch):
    """A 0-byte wake model file must be rejected and not reported as ready."""
    import voice_service

    empty_model = tmp_path / "empty_wake.onnx"
    empty_model.write_bytes(b"")

    monkeypatch.setenv("VEGA_WAKE_MODEL_PATH", str(empty_model))
    assert voice_service.resolve_wake_word_model() is None


@isolated_bootstrap
def test_inherited_voice_env_does_not_enable_beta_voice(beta_voice_env):
    """Inherited VEGA_ENABLE_VOICE must NOT enable voice in ordinary beta profile."""
    os.environ.pop("VEGA_BETA_VOICE", None)
    os.environ["VEGA_ENABLE_VOICE"] = "1"

    import main
    importlib.reload(main)

    assert os.getenv("VEGA_DISABLE_VOICE") == "1", "Must disable voice in ordinary beta profile"
    assert os.getenv("VEGA_ENABLE_VOICE") is None, "Must scrub inherited VEGA_ENABLE_VOICE"

    client = TestClient(main.app)
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["voice_test_mode"] is False
    assert health_res.json()["voice_service"]["status"] == "disabled"


def test_voice_interruption_ducking_lifecycle():
    """Verify ducking interruption semantics:
    1. While ducked, wake detection is bounded and active.
    2. When wake_fired triggers during ducking, voice_service enters pending-interruption state.
    3. Stale or mismatching acks are rejected; ducking and pending state remain.
    4. Valid ack un-ducks, clears pending state, and signals recording arming.
    """
    import voice_service

    # Start clean
    voice_service.set_duck(False)
    voice_service.cancel_interruption("test setup")
    assert voice_service.is_ducked() is False
    assert voice_service.is_interruption_pending() is False

    # 1. Engage ducking (VEGA starts speaking)
    voice_service.set_duck(True, ttl=30.0)
    assert voice_service.is_ducked() is True

    # 2. Below threshold: does not trigger
    below_scores = {"hey_jarvis": [0.1]}
    assert voice_service.wake_fired(below_scores, voice_service.WAKE_THRESHOLD) is False
    assert voice_service.is_ducked() is True

    # 3. Crossing threshold: enters pending interruption
    above_scores = {"hey_jarvis": [0.85]}
    assert voice_service.wake_fired(above_scores, voice_service.WAKE_THRESHOLD) is True

    int_id = voice_service.start_interruption()
    assert int_id and len(int_id) > 0
    assert voice_service.is_interruption_pending() is True
    # Ducking MUST remain active while waiting for client ack
    assert voice_service.is_ducked() is True

    # 4. Mismatching or stale ack is rejected
    assert voice_service.acknowledge_interruption("stale-id-999") is False
    assert voice_service.is_interruption_pending() is True
    assert voice_service.is_ducked() is True

    # 5. Matching ack succeeds: clears pending, un-ducks, sets ack event
    assert voice_service.acknowledge_interruption(int_id) is True
    assert voice_service.is_interruption_pending() is False
    assert voice_service.is_ducked() is False
    assert voice_service._interruption_ack_event.is_set() is True

    # Clean up ack event
    voice_service._interruption_ack_event.clear()


def test_interruption_ack_timeout_and_fail_closed():
    """Verify fail-closed behavior on timeout, disconnect, and mic disable."""
    import time
    import voice_service

    voice_service.set_duck(True, ttl=30.0)
    int_id = voice_service.start_interruption()
    assert voice_service.is_interruption_pending() is True

    # 1. Timeout expires
    voice_service._interruption_deadline = time.time() - 0.1
    # is_interruption_pending detects expiration and fails closed
    assert voice_service.is_interruption_pending() is False

    # Stale ack arriving after timeout is rejected
    assert voice_service.acknowledge_interruption(int_id) is False

    # 2. Client disconnect / mic disable cancels pending interruption
    voice_service.set_duck(True, ttl=30.0)
    int_id2 = voice_service.start_interruption()
    assert voice_service.is_interruption_pending() is True

    voice_service.cancel_interruption("client disconnected")
    assert voice_service.is_interruption_pending() is False
    assert voice_service.acknowledge_interruption(int_id2) is False

    # Clean up
    voice_service.set_duck(False)


@isolated_bootstrap
def test_post_interrupt_ack_endpoint(beta_voice_env):
    """Test POST /api/voice/interrupt_ack route."""
    import main
    import voice_service

    client = TestClient(main.app)

    # 1. No pending interruption -> acknowledged: False
    res_none = client.post("/api/voice/interrupt_ack", json={"interruption_id": "no-such-id"})
    assert res_none.status_code == 200
    assert res_none.json()["acknowledged"] is False

    # 2. Active pending interruption -> acknowledged: True
    voice_service.set_duck(True, ttl=30.0)
    int_id = voice_service.start_interruption()

    res_ok = client.post("/api/voice/interrupt_ack", json={"interruption_id": int_id})
    assert res_ok.status_code == 200
    assert res_ok.json()["acknowledged"] is True
    assert voice_service.is_ducked() is False

    # 3. Replaying the same ack now returns False (stale)
    res_stale = client.post("/api/voice/interrupt_ack", json={"interruption_id": int_id})
    assert res_stale.status_code == 200
    assert res_stale.json()["acknowledged"] is False




