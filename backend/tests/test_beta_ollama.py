"""Automated regression tests for Opt-In Local Ollama in Isolated Beta Profile.

Verifies:
1. Ordinary beta remains deterministic-only (LLM_PROVIDER=none, deterministic_only=True).
2. Opt-in local Ollama mode (VEGA_BETA_OLLAMA=1) permits Ollama while cloud keys remain scrubbed.
3. Truthful GET /health provider and deterministic_only reporting.
4. Loopback endpoint enforcement (rejects remote/LAN IPs, accepts localhost, 127.0.0.1, ::1).
5. check_ollama_readiness probes local /api/tags without downloads or token generation.
6. Deterministic commands take precedence before Ollama is consulted (zero model calls).
7. Model proposals execute via tool_registry -> policy -> executor -> ActionReceipt.
8. Unreachable Ollama returns friendly error with ZERO cloud fallback.
"""

import os
import sys
import importlib
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from beta_test_isolation import isolated_bootstrap

backend_dir = os.path.dirname(os.path.dirname(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


@pytest.fixture
def beta_ollama_env(tmp_path):
    """Isolated environment fixture for beta Ollama testing."""
    saved_env = dict(os.environ)
    beta_db = str(tmp_path / "jarvis-beta.db")

    os.environ["JARVIS_DB_PATH"] = beta_db
    os.environ["VEGA_PROFILE"] = "beta"
    os.environ["VEGA_PROFILE_ROOT"] = str(tmp_path)
    os.environ["VEGA_RUN_ID"] = "test-beta-ollama-run"
    os.environ["VEGA_PORT"] = "8005"
    os.environ["GEMINI_API_KEY"] = "leak-check-cloud-key"
    os.environ["OPENAI_API_KEY"] = "leak-check-openai-key"

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


@isolated_bootstrap
def test_ordinary_beta_remains_deterministic_only(beta_ollama_env):
    """Ordinary beta (without VEGA_BETA_OLLAMA) must force deterministic-only mode."""
    os.environ.pop("VEGA_BETA_OLLAMA", None)

    import main
    importlib.reload(main)

    assert os.getenv("LLM_PROVIDER") == "none"
    assert os.getenv("GEMINI_API_KEY") is None
    assert os.getenv("OPENAI_API_KEY") is None

    client = TestClient(main.app)

    # 1. GET /health reports deterministic_only=True and provider status=disabled
    health_res = client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert health_data["profile"] == "beta"
    assert health_data["deterministic_only"] is True
    assert health_data["ollama_test_mode"] is False
    assert health_data["provider"]["status"] == "disabled"
    assert health_data["provider"]["name"] == "none"

    # 2. Freeform conversational query returns deterministic clarification refusal
    chat_res = client.post("/chat", json={"message": "Explain quicksort in detail."})
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data.get("executionMode") in ("none", "deterministic")
    assert "deterministic-only" in chat_data.get("response", "").lower() or "offline" in chat_data.get("response", "").lower()


@isolated_bootstrap
def test_beta_opt_in_ollama_mode_activation(beta_ollama_env):
    """Beta profile with VEGA_BETA_OLLAMA=1 enables Ollama while scrubbing cloud keys."""
    os.environ["VEGA_BETA_OLLAMA"] = "1"
    os.environ["OLLAMA_MODEL"] = "llama3"
    os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"

    import main
    importlib.reload(main)

    assert os.getenv("LLM_PROVIDER") == "ollama"
    assert os.getenv("GEMINI_API_KEY") is None
    assert os.getenv("OPENAI_API_KEY") is None

    client = TestClient(main.app)

    # Mock Ollama /api/tags for readiness probe
    fake_tags_resp = MagicMock()
    fake_tags_resp.status_code = 200
    fake_tags_resp.json.return_value = {
        "models": [{"name": "llama3:latest"}, {"name": "qwen2.5:latest"}]
    }

    with patch("requests.get", return_value=fake_tags_resp) as probe:
        health_res = client.get("/health")
        assert health_res.status_code == 200
        health_data = health_res.json()
        assert health_data["profile"] == "beta"
        assert health_data["deterministic_only"] is False
        assert health_data["ollama_test_mode"] is True
        assert health_data["provider"]["name"] == "ollama"
        assert health_data["provider"]["status"] == "configured"
        assert health_data["provider"]["model"] == "llama3"
        probe.assert_not_called()

        diagnostics = client.get("/health?diagnostics=1")
        assert diagnostics.status_code == 200
        assert diagnostics.json()["provider"]["status"] == "ready"
        probe.assert_called_once()


def test_loopback_url_validation():
    """Ollama base URL must strictly target local loopback interfaces."""
    from providers.ollama import validate_loopback_url, is_loopback_host

    # Valid loopback hosts
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("127.0.0.42") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("[::1]") is True

    # Invalid / prohibited non-loopback hosts
    assert is_loopback_host("192.168.1.100") is False
    assert is_loopback_host("10.0.0.1") is False
    assert is_loopback_host("169.254.169.254") is False
    assert is_loopback_host("example.com") is False
    assert is_loopback_host("ollama.internal.net") is False

    # URL validation success
    assert validate_loopback_url("http://localhost:11434") == "http://localhost:11434"
    assert validate_loopback_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert validate_loopback_url("http://127.0.1.1:11434") == "http://127.0.1.1:11434"
    assert validate_loopback_url("https://[::1]:11434") == "https://[::1]:11434"

    # URL validation refusal
    with pytest.raises(ValueError, match="Non-loopback host"):
        validate_loopback_url("http://192.168.1.50:11434")

    with pytest.raises(ValueError, match="Non-loopback host"):
        validate_loopback_url("http://169.254.169.254/latest/meta-data")

    with pytest.raises(ValueError, match="must use http or https scheme"):
        validate_loopback_url("ftp://localhost:11434")


def test_check_ollama_readiness_probes():
    """check_ollama_readiness reports truthful statuses from local /api/tags."""
    from providers.ollama import check_ollama_readiness

    # 1. Ready: service responds with configured model
    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.json.return_value = {"models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}]}

    with patch("requests.get", return_value=mock_ok):
        res = check_ollama_readiness("http://localhost:11434", "llama3")
        assert res["ready"] is True
        assert res["status"] == "ready"
        assert res["error"] is None

    # 2. Missing model: service online but model tag not present
    with patch("requests.get", return_value=mock_ok):
        res_missing = check_ollama_readiness("http://localhost:11434", "qwen2.5:7b")
        assert res_missing["ready"] is False
        assert res_missing["status"] == "missing_model"
        assert "not found in local Ollama tags" in res_missing["error"]
        assert "ollama pull" in res_missing["error"]

    # 3. Unavailable: service offline / connection error
    import requests
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
        res_down = check_ollama_readiness("http://localhost:11434", "llama3")
        assert res_down["ready"] is False
        assert res_down["status"] == "unavailable"
        assert "not reachable" in res_down["error"]

    # 4. Prohibited non-loopback URL fails closed
    res_remote = check_ollama_readiness("http://remote-server:11434", "llama3")
    assert res_remote["ready"] is False
    assert res_remote["status"] == "unavailable"
    assert "Non-loopback host" in res_remote["error"]


@isolated_bootstrap
def test_deterministic_commands_precede_ollama_in_beta(beta_ollama_env):
    """Deterministic commands are answered by dispatcher without consulting Ollama."""
    os.environ["VEGA_BETA_OLLAMA"] = "1"
    os.environ["OLLAMA_MODEL"] = "llama3"

    import main
    importlib.reload(main)

    client = TestClient(main.app)

    # Even if requests.post would throw or be called, dispatcher handles exact command first
    with patch("requests.post") as mock_post:
        res = client.post("/chat", json={"message": "add a task to review sorting algorithms"})
        assert res.status_code == 200
        data = res.json()
        assert data.get("executionMode") == "deterministic"
        assert "review sorting algorithms" in data.get("response", "").lower()
        # Verify Ollama was NOT consulted
        mock_post.assert_not_called()


@isolated_bootstrap
def test_model_proposal_pipeline_and_receipt(beta_ollama_env):
    """When Ollama returns a tool call, it executes via tool_registry -> policy -> ActionReceipt."""
    os.environ["VEGA_BETA_OLLAMA"] = "1"
    os.environ["OLLAMA_MODEL"] = "llama3"

    import main
    importlib.reload(main)
    import model_lane
    model_lane.reset_providers()

    client = TestClient(main.app)

    # Mock Ollama /api/chat returning a tool proposal to create a task
    fake_chat_resp = MagicMock()
    fake_chat_resp.status_code = 200
    fake_chat_resp.json.return_value = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "create_task",
                        "arguments": {"text": "Model proposed task from Ollama"}
                    }
                }
            ]
        },
        "model": "llama3:latest",
        "eval_count": 25,
        "prompt_eval_count": 150
    }

    with patch("requests.post", return_value=fake_chat_resp):
        res = client.post("/chat", json={"message": "Could you help me track a reminder to finish my assignment?"})
        assert res.status_code == 200
        data = res.json()
        assert data.get("executionMode") == "local"
        assert data.get("receipt") is not None
        assert data["receipt"]["action"] == "create_task"
        assert data["receipt"]["success"] is True

        # Verify task was actually stored in the isolated beta database
        from db import SessionLocal, Task
        with SessionLocal() as session:
            t = session.query(Task).filter(Task.text.contains("Model proposed task")).first()
            assert t is not None


@isolated_bootstrap
def test_unreachable_ollama_returns_friendly_error_no_cloud_fallback(beta_ollama_env):
    """When Ollama is unreachable, returns friendly offline explanation with zero cloud calls."""
    os.environ["VEGA_BETA_OLLAMA"] = "1"
    os.environ["OLLAMA_MODEL"] = "llama3"

    import main
    importlib.reload(main)
    import model_lane
    model_lane.reset_providers()

    client = TestClient(main.app)

    import requests
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError):
        res = client.post("/chat", json={"message": "Explain dynamic programming algorithms."})
        assert res.status_code == 200
        data = res.json()
        assert data.get("executionMode") == "none"
        resp_text = data.get("response", "")
        assert "Ollama is not reachable" in resp_text or "unavailable" in resp_text.lower()
        assert "tasks, timers, and reminders still work offline" in resp_text

        # Verify cloud keys were not created or accessed
        assert os.getenv("GEMINI_API_KEY") is None
