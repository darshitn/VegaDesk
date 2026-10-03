"""P1-E1 Readiness and Fault Isolation Tests.

Covers:
1. /health liveness and rich readiness contract (legacy fields preserved).
2. Database probe failure degradation without raw secrets/paths.
3. Scheduler task lifecycle, last tick, and error state tracking.
4. Voice service status truthfulness (disabled vs active).
5. Explicit optional diagnostics (bounded local Ollama metadata check).
6. Gemini request timeout applied to SDK HttpOptions.
7. BaseProvider queue wait bounded by remaining request deadline.
8. Late proposal rejection after deadline expiry without entity mutation.
9. Deterministic offline commands working seamlessly during provider failures.
10. Slow in-flight provider calls not blocking concurrent deterministic commands.
11. Failed/expired proposals never mutating entities or creating success receipts.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from db import Task, Timer, Reminder, ActionReceipt, Base
from providers.base import (
    BaseProvider, Capabilities, ProviderResponse, ToolProposal,
    ProviderUnavailable, ProviderTimeout, ProviderQuota, ProviderMalformed,
)
from providers.gemini import GeminiProvider
from scheduler import AlertScheduler
import voice_service
import model_lane
import timeutil


# ── 1. /health Liveness and Rich Readiness Contract ──────────────────────────

def test_health_preserves_legacy_and_adds_readiness(client):
    """GET /health must preserve legacy 'status' and 'voice' while providing rich readiness."""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()

    # Legacy contract preserved
    assert data["status"] == "ok"
    assert "voice" in data
    assert isinstance(data["voice"], bool)

    # Rich readiness contract
    assert "database" in data
    assert data["database"]["status"] == "ready"
    assert "checked_at" in data["database"]
    assert data["database"]["error"] is None

    assert "scheduler" in data
    assert data["scheduler"]["status"] in ("ready", "starting", "stopped")
    assert "running" in data["scheduler"]

    assert "voice_service" in data
    assert data["voice_service"]["status"] in ("ready", "disabled", "starting", "unavailable")

    assert "provider" in data
    assert "name" in data["provider"]
    assert data["provider"]["status"] in ("configured", "unconfigured", "ready", "unavailable")


def test_health_database_failure_degrades_status(client, monkeypatch):
    """When the database probe fails, /health must report 'degraded' with sanitized error."""
    import main

    def _failing_session():
        raise RuntimeError("Fake database connection failure at C:/Users/secret/jarvis.db")

    monkeypatch.setattr(main, "SessionLocal", _failing_session)

    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "degraded"
    assert data["database"]["status"] == "unavailable"
    assert "RuntimeError" in data["database"]["error"]
    # Verify no raw user paths are exposed
    assert "C:/Users" not in data["database"]["error"]


# ── 2. Scheduler State Tracking ──────────────────────────────────────────────

def test_scheduler_status_lifecycle():
    """AlertScheduler tracks running state, last tick, and error truthfully."""
    clock = timeutil.FakeClock(datetime(2026, 10, 2, 8, 0, 0, tzinfo=timezone.utc))
    sched = AlertScheduler(lambda: None, clock=clock)

    # Stopped state initially
    status = sched.get_status()
    assert status["running"] is False
    assert status["status"] == "stopped"
    assert status["last_tick"] is None
    assert status["error"] is None

    # Simulate tick execution
    sched._is_running = True
    sched._last_tick_utc = clock.now_utc()
    status = sched.get_status()
    assert status["running"] is True
    assert status["status"] == "ready"
    assert status["last_tick"] == "2026-10-02T08:00:00"

    # Simulate tick error
    sched._last_error = "OperationalError: database locked"
    status = sched.get_status()
    assert status["running"] is True
    assert status["status"] == "degraded"
    assert status["error"] == "OperationalError: database locked"


# ── 3. Voice Service State ───────────────────────────────────────────────────

def test_voice_service_status_disabled_and_active(monkeypatch):
    """Voice service returns truthful status when disabled via env vs active."""
    monkeypatch.setenv("VEGA_DISABLE_VOICE", "1")
    status = voice_service.get_status()
    assert status["status"] == "disabled"
    assert status["active"] is False

    monkeypatch.setenv("VEGA_DISABLE_VOICE", "0")
    monkeypatch.setattr(voice_service, "_voice_active", True)
    status = voice_service.get_status()
    assert status["status"] == "ready"
    assert status["active"] is True
    assert voice_service.is_voice_active() is True


# ── 4. Diagnostics and Metadata Checks ───────────────────────────────────────

def test_health_optional_diagnostics_ollama(client, monkeypatch):
    """Optional ?diagnostics=1 probes local Ollama tags without generating tokens."""
    import main
    import requests

    monkeypatch.setattr(main, "LLM_PROVIDER", "ollama")

    # Regular health check does NOT contact Ollama
    probed = []
    def _tracking_get(url, *args, **kwargs):
        probed.append(url)
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"models": [{"name": "llama3:latest"}]}
        return mock

    monkeypatch.setattr(requests, "get", _tracking_get)

    r = client.get("/health")
    assert r.status_code == 200
    assert len(probed) == 0  # Regular GET does not probe network

    # With diagnostics=True
    r_diag = client.get("/health?diagnostics=1")
    assert r_diag.status_code == 200
    data = r_diag.json()
    assert len(probed) == 1
    assert "api/tags" in probed[0]
    assert data["provider"]["status"] in ("ready", "missing_model")


# ── 5. Request Deadlines & Gemini HttpOptions ────────────────────────────────

def test_gemini_request_timeout_applied_to_client_and_config(monkeypatch):
    """GeminiProvider._call applies timeout_s to types.HttpOptions and GenerateContentConfig."""
    provider = GeminiProvider(api_key="fake-test-key")

    captured_http_options = []
    captured_config = []

    class FakeClient:
        def __init__(self, api_key=None, http_options=None):
            captured_http_options.append(http_options)
            self.models = MagicMock()

            def _fake_generate(model, contents, config):
                captured_config.append(config)
                resp = MagicMock()
                resp.text = "Hello"
                resp.function_calls = None
                return resp

            self.models.generate_content = _fake_generate

    monkeypatch.setattr("google.genai.Client", FakeClient)

    res = provider._call(messages=[{"role": "user", "content": "hi"}], tools=None, timeout_s=12.5)
    assert res.text == "Hello"

    # Verify Client received timeout in milliseconds
    assert len(captured_http_options) == 1
    assert captured_http_options[0].timeout == 12500

    # Verify GenerateContentConfig received timeout in milliseconds
    assert len(captured_config) == 1
    assert captured_config[0].http_options.timeout == 12500


def test_base_provider_queue_timeout_bounded_by_deadline(monkeypatch):
    """BaseProvider.generate bounds queue acquisition wait by remaining deadline."""
    class DummyProvider(BaseProvider):
        def _call(self, messages, tools, timeout_s):
            return ProviderResponse(text="ok")

    provider = DummyProvider(Capabilities(local=True))

    # Hold the semaphore to simulate a busy generation slot
    acquired = provider._slot.acquire(blocking=False)
    assert acquired is True

    try:
        # Request with a 0.2s deadline — queue wait must not block for full 10s
        t0 = time.monotonic()
        with pytest.raises(ProviderTimeout, match="took too long to answer"):
            provider.generate(
                messages=[{"role": "user", "content": "hello"}],
                deadline=t0 + 0.2,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"Queue wait exceeded deadline: elapsed {elapsed}s"
    finally:
        provider._slot.release()


# ── 6. Late Proposal Expiry Guard ───────────────────────────────────────────

def test_late_proposal_rejected_after_deadline_expiry(db):
    """If provider returns proposals after deadline expired, proposal is rejected before execution."""
    clock = timeutil.FakeClock(datetime(2026, 10, 2, 8, 0, 0))

    class LateProvider(BaseProvider):
        name = "ollama"
        def _call(self, messages, tools, timeout_s):
            # Advance past deadline during generation
            time.sleep(0.08)
            return ProviderResponse(
                proposals=[ToolProposal(name="create_task", args={"text": "Late task"})]
            )

    provider = LateProvider(Capabilities(local=True, tools=True))

    import model_lane
    monkeypatch_lane = pytest.MonkeyPatch()
    monkeypatch_lane.setattr(model_lane, "get_provider", lambda name: provider)

    from db import SessionLocal
    # Deadline that will expire while the provider is executing _call
    t_deadline = time.monotonic() + 0.02

    res = model_lane.run_model_turn(
        SessionLocal, clock, "make a task", [],
        provider_name="ollama", deadline=t_deadline
    )
    monkeypatch_lane.undo()

    assert "timed out before the proposed action could be executed" in res["response"]
    assert res["receipt"] is None

    # Prove no task was created
    assert db.query(Task).count() == 0
    assert db.query(ActionReceipt).count() == 0


# ── 7. Deterministic Commands Work During Provider Outages ──────────────────

@pytest.mark.parametrize("error_cls,kwargs", [
    (ProviderUnavailable, {"message": "Ollama is down"}),
    (ProviderQuota, {"message": "Rate limited 429", "retry_after": 30.0}),
    (ProviderMalformed, {"message": "Malformed JSON from provider"}),
    (ProviderTimeout, {"message": "Request timed out"}),
])
def test_deterministic_commands_work_when_provider_fails(client, db, monkeypatch, error_cls, kwargs):
    """Deterministic commands succeed immediately even when the LLM provider is broken/failing."""
    import model_lane

    class FailingProvider(BaseProvider):
        name = "failing"
        def _call(self, messages, tools, timeout_s):
            raise error_cls(**kwargs)

    monkeypatch.setattr(model_lane, "get_provider", lambda name: FailingProvider(Capabilities(local=True)))

    # 1. Deterministic task creation succeeds
    r_task = client.post("/chat", json={"message": "create task Buy apples", "provider": "failing"})
    assert r_task.status_code == 200
    body_task = r_task.json()
    assert body_task["executionMode"] == "deterministic"
    assert body_task["receipt"]["success"] is True
    assert db.query(Task).filter_by(text="Buy apples").count() == 1

    # 2. Deterministic timer succeeds
    r_timer = client.post("/chat", json={"message": "start timer for 15 minutes", "provider": "failing"})
    assert r_timer.status_code == 200
    body_timer = r_timer.json()
    assert body_timer["executionMode"] == "deterministic"
    assert body_timer["receipt"]["success"] is True

    # 3. Deterministic reminder succeeds
    r_rem = client.post("/chat", json={"message": "remind me in 30 minutes to stretch", "provider": "failing"})
    assert r_rem.status_code == 200
    body_rem = r_rem.json()
    assert body_rem["executionMode"] == "deterministic"
    assert body_rem["receipt"]["success"] is True

    # 4. Read-only API endpoints work
    r_list = client.get("/api/tasks")
    assert r_list.status_code == 200
    assert len(r_list.json()) >= 1


# ── 8. Slow In-flight Provider Does Not Block Deterministic Commands ─────────

def test_slow_in_flight_provider_does_not_block_concurrent_deterministic_command(client, db, monkeypatch):
    """A slow in-flight model call must not block concurrent deterministic commands on /chat."""
    import model_lane

    slow_call_started = threading.Event()
    can_slow_call_finish = threading.Event()

    class SlowProvider(BaseProvider):
        name = "ollama"
        def _call(self, messages, tools, timeout_s):
            slow_call_started.set()
            can_slow_call_finish.wait(timeout=10.0)
            return ProviderResponse(text="Slow answer finished")

    monkeypatch.setattr(model_lane, "get_provider", lambda name: SlowProvider(Capabilities(local=True)))

    # Launch slow conversational turn in background thread
    slow_thread_result = []
    def _run_slow():
        r = client.post("/chat", json={"message": "explain quantum physics in depth", "provider": "ollama"})
        slow_thread_result.append(r)

    t = threading.Thread(target=_run_slow, daemon=True)
    t.start()

    # Wait until the slow call is actively running inside the provider
    assert slow_call_started.wait(timeout=5.0) is True

    try:
        # Concurrently send deterministic command
        t0 = time.monotonic()
        r_det = client.post("/chat", json={"message": "add a task: Urgent grocery item"})
        elapsed = time.monotonic() - t0

        assert r_det.status_code == 200
        body = r_det.json()
        assert body["executionMode"] == "deterministic"
        assert body["receipt"]["success"] is True
        assert db.query(Task).filter_by(text="Urgent grocery item").count() == 1
        # Proves it did not block on the slow provider
        assert elapsed < 2.0, f"Deterministic command blocked on provider: elapsed {elapsed}s"
    finally:
        can_slow_call_finish.set()
        t.join(timeout=5.0)

    assert len(slow_thread_result) == 1
    assert slow_thread_result[0].status_code == 200


# ── 9. Failed/Expired Proposals Never Mutate or Create Success Receipts ───────

def test_failed_or_expired_proposals_never_mutate_or_create_success_receipts(client, db, monkeypatch):
    """When a model call times out or throws an error, zero tasks are created and zero success receipts exist."""
    import model_lane

    class BrokenProvider(BaseProvider):
        name = "ollama"
        def _call(self, messages, tools, timeout_s):
            raise ProviderTimeout("Simulated provider timeout during turn")

    monkeypatch.setattr(model_lane, "get_provider", lambda name: BrokenProvider(Capabilities(local=True)))

    init_tasks = db.query(Task).count()
    init_receipts = db.query(ActionReceipt).count()

    r = client.post("/chat", json={"message": "please schedule a meeting tomorrow", "provider": "ollama"})
    assert r.status_code == 200
    body = r.json()
    assert body["executionMode"] == "none"
    assert "timed out" in body["response"].lower() or "too long" in body["response"].lower()
    assert body.get("receipt") is None

    # Verify zero mutations
    assert db.query(Task).count() == init_tasks
    assert db.query(ActionReceipt).filter_by(success=True).count() == init_receipts
