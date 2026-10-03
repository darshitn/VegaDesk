"""P1-E2A Interaction, Freshness, and Readiness Hardening Tests.

Covers:
1. provider="none" deterministic-only mode (truthful refusal of conversational queries).
2. Deterministic offline commands working seamlessly when provider="none".
3. GET /health behavior when LLM_PROVIDER="none" (disabled status, model="none").
4. Sanitization of sensitive paths and secrets in AlertScheduler._sanitize_error.
5. Sanitization of sensitive paths and secrets in voice_service._sanitize_voice_error.
6. Voice service status truthfulness (disabled never reported as starting).
7. Strict boolean return contract for is_voice_active().
"""

import os
import pytest
from unittest.mock import patch, MagicMock

import model_lane
from scheduler import AlertScheduler
import voice_service


# ── 1. provider="none" Deterministic-Only Mode ──────────────────────────────

def test_model_lane_provider_none_refusal():
    """model_lane.run_model_turn with provider='none' returns truthful refusal without contacting cloud."""
    res = model_lane.run_model_turn(
        None, None, "Explain quantum computing in detail", [],
        provider_name="none"
    )
    assert res is not None
    assert "deterministic-only mode" in res.get("response", "")
    assert res.get("executionMode") == "none"
    assert res.get("clarification") is True


def test_chat_endpoint_provider_none_conversational_refusal(client):
    """POST /chat with provider='none' on unfamiliar query returns deterministic refusal."""
    r = client.post("/chat", json={
        "message": "Write a 500-word essay about space exploration",
        "provider": "none",
    })
    assert r.status_code == 200
    data = r.json()
    assert "deterministic-only mode" in data.get("response", "")
    assert data.get("executionMode") == "none"
    assert data.get("clarification") is True


def test_chat_endpoint_provider_none_deterministic_command(client):
    """POST /chat with provider='none' still executes deterministic commands (e.g. tasks/timers)."""
    r = client.post("/chat", json={
        "message": "add task Buy fresh groceries",
        "provider": "none",
    })
    assert r.status_code == 200
    data = r.json()
    assert data.get("executionMode") == "deterministic"
    assert "Buy fresh groceries" in data.get("response", "")


# ── 2. GET /health with provider="none" ──────────────────────────────────────

def test_health_with_provider_none(client):
    """GET /health when LLM_PROVIDER is 'none' reports provider_status='disabled' and model='none'."""
    with patch.dict(os.environ, {"LLM_PROVIDER": "none"}):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "provider" in data
        assert data["provider"]["status"] == "disabled"
        assert data["provider"]["model"] == "none"
        assert data["provider"]["error"] is None


# ── 3. Error Sanitization in Scheduler & Voice Service ───────────────────────

def test_scheduler_sanitize_error_strips_paths_and_secrets():
    """AlertScheduler._sanitize_error must scrub local paths and secrets."""
    # Windows path
    exc_win = Exception("OperationalError: unable to open database file at C:\\Users\\Darshit N\\AppData\\Local\\secret.db")
    safe_win = AlertScheduler._sanitize_error(exc_win)
    assert "C:\\" not in safe_win
    assert "secret.db" not in safe_win
    assert "Darshit N" not in safe_win

    # Unix path
    exc_unix = Exception("PermissionError: /home/darshit/project/secure_key.pem permission denied")
    safe_unix = AlertScheduler._sanitize_error(exc_unix)
    assert "/home/darshit" not in safe_unix
    assert "secure_key.pem" not in safe_unix

    # Long random hex / api key
    exc_key = Exception("Invalid key AIzaSyA1234567890abcdef1234567890abcdef")
    safe_key = AlertScheduler._sanitize_error(exc_key)
    assert "AIzaSy" not in safe_key

    # None input
    assert AlertScheduler._sanitize_error(None) is None


def test_voice_sanitize_error_strips_paths_and_secrets():
    """voice_service._sanitize_voice_error must scrub local paths and secrets."""
    exc_path = Exception("Failed to load model from C:\\Users\\Darshit N\\.cache\\vosk\\model.zip")
    safe_path = voice_service._sanitize_voice_error(exc_path)
    assert "C:\\" not in safe_path
    assert "Darshit N" not in safe_path

    exc_key = Exception("Microphone error with token abcdef0123456789abcdef0123456789")
    safe_key = voice_service._sanitize_voice_error(exc_key)
    assert "abcdef0123456789" not in safe_key

    assert voice_service._sanitize_voice_error(None) is None


# ── 4. Voice Service Status Truthfulness ─────────────────────────────────────

def test_voice_status_disabled_never_reported_as_starting():
    """voice_service.get_status() returns 'disabled' when voice is turned off, never 'starting'."""
    orig_enabled = voice_service._enabled.is_set()
    try:
        voice_service._enabled.clear()
        status = voice_service.get_status()
        assert status["status"] == "disabled"
        assert status["active"] is False
        assert status["enabled"] is False
        assert status["error"] is None
    finally:
        if orig_enabled:
            voice_service._enabled.set()


def test_is_voice_active_returns_strict_bool():
    """voice_service.is_voice_active() returns strict boolean even if error was set."""
    orig_active = voice_service._voice_active
    try:
        voice_service._voice_active = 0
        res = voice_service.is_voice_active()
        assert isinstance(res, bool)
        assert res is False
    finally:
        voice_service._voice_active = orig_active

    # When active is 1 / True
    voice_service._voice_active = 1
    res = voice_service.is_voice_active()
    assert isinstance(res, bool)
    assert res is True

    # Restore to False
    voice_service._voice_active = 0
