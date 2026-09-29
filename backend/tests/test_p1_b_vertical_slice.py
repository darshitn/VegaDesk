"""P1-B Vertical Slice Tests: Registry -> Policy -> Executor -> Verifier -> Audit -> Response.

Acceptance criteria:
- Smallest safe vertical slice through registry, policy, executor, verifier, audit, and response.
- Reconciles existing safe URL/website action (open_website / system.open_url).
- Tests prove:
  1. Success (permitted action, verified launch, audit receipt written)
  2. Denial (policy denial on forbidden schemes/injection, launcher never invoked, refusal receipt written)
  3. Failure (launcher error or return-false, failure receipt written)
  4. Receipt never asserts unverified success (success is False when verification fails)
  5. Idempotent replay (receipt replayed, no duplicate launch)
  6. Fast path /chat integration (receipt persisted, verified response)

Note on desktop behavior:
All tests in this suite mock the OS browser launcher (browser_launcher parameter or mock)
to verify deterministic execution and prevent spawning uncontrolled browser tabs.
Real desktop launch behavior is labeled and separated.
"""

import pytest
import webbrowser
from db import ActionReceipt
import policy
import verifier
import system_actions
import tools
import tool_registry
import model_lane
from providers.base import BaseProvider, Capabilities, ProviderResponse, ToolProposal
from fastapi.testclient import TestClient
import main


class FakeProvider(BaseProvider):
    def __init__(self, name="ollama", local=True, response=None, exc=None,
                 context_window=8192, reserved_output=1024):
        caps = Capabilities(local=local, tools=True,
                            context_window=context_window,
                            reserved_output=reserved_output,
                            data_policy="local_only" if local else "user_configured_cloud")
        super().__init__(caps)
        self.name = name
        self.calls = []
        self._response = response or ProviderResponse(text="ok")
        self._exc = exc

    def _call(self, messages, tools, timeout_s):
        self.calls.append({"messages": messages, "tools": tools or []})
        if self._exc:
            raise self._exc
        return self._response


# ─────────────────────────────────────────────
# 1. Permitted Action (Success Path)
# ─────────────────────────────────────────────

def test_p1_b_success_url_direct(db, clock):
    """Permitted action: valid https URL, launcher returns True -> verified receipt."""
    launched_urls = []

    def mock_launcher(url):
        launched_urls.append(url)
        return True  # OS browser launch verified

    res = system_actions.execute_open_url(
        "https://github.com",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="test-perm-1",
        browser_launcher=mock_launcher,
    )

    assert res["success"] is True
    assert res["opened"] is True
    assert res["verified"] is True
    assert "Opening" in res["response"] and ("Github" in res["response"] or "github.com" in res["response"])
    assert launched_urls == ["https://github.com"]

    # Verify audit receipt persisted in temporary SQLite db
    receipts = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "test-perm-1").all()
    assert len(receipts) == 1
    r = receipts[0]
    assert r.success is True
    assert r.action == "open_website"
    assert "Github" in r.message or "github.com" in r.message


def test_p1_b_success_alias_resolution(db, clock):
    """Permitted action: valid alias 'youtube' resolves to https://www.youtube.com."""
    launched_urls = []

    def mock_launcher(url):
        launched_urls.append(url)
        return True

    res = system_actions.execute_open_url(
        "youtube",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="test-alias-1",
        browser_launcher=mock_launcher,
    )

    assert res["success"] is True
    assert res["opened"] is True
    assert launched_urls == ["https://www.youtube.com"]

    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "test-alias-1").first()
    assert receipt is not None
    assert receipt.success is True
    assert "Youtube" in receipt.message


# ─────────────────────────────────────────────
# 2. Policy Denial (Forbidden Schemes & Injection)
# ─────────────────────────────────────────────

@pytest.mark.parametrize("dangerous_target,expected_violation", [
    ("file:///C:/Windows/System32/calc.exe", "forbidden"),
    ("file:///etc/passwd", "forbidden"),
    ("javascript:alert(document.cookie)", "forbidden"),
    ("data:text/html,<script>alert(1)</script>", "forbidden"),
    ("ms-settings:privacy", "forbidden"),
    ("shell:startup", "forbidden"),
    ("powershell -c calc", "disallowed shell characters"),
    ("https://github.com; calc.exe", "disallowed characters"),
])
def test_p1_b_denial_policy_blocks_dangerous_targets(db, clock, dangerous_target, expected_violation):
    """Policy denial: forbidden schemes and shell injection must fail closed without launching."""
    launcher_called = []

    def mock_launcher(url):
        launcher_called.append(url)
        return True

    res = system_actions.execute_open_url(
        dangerous_target,
        db=db,
        clock=clock,
        source="test",
        idempotency_key=f"deny-{hash(dangerous_target)}",
        browser_launcher=mock_launcher,
    )

    # Launcher must NEVER be called
    assert len(launcher_called) == 0
    assert res["success"] is False
    assert res["opened"] is False
    assert res["verified"] is False
    assert "Policy denied" in res["response"]

    # Denial audit receipt must be stored in SQLite
    receipt = db.query(ActionReceipt).filter(
        ActionReceipt.idempotency_key == f"deny-{hash(dangerous_target)}"
    ).first()
    assert receipt is not None
    assert receipt.success is False
    assert "Policy denied" in receipt.message


# ─────────────────────────────────────────────
# 3. Failure & Unverified Launch Protection
# ─────────────────────────────────────────────

def test_p1_b_failure_launcher_returns_false_never_asserts_success(db, clock):
    """Failure case: launcher returns False (OS browser failed to start).
    Crucial guarantee: a receipt never asserts unverified success.
    """
    def mock_failing_launcher(url):
        return False  # browser launch failed or could not be confirmed

    res = system_actions.execute_open_url(
        "https://github.com",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="fail-ret-false",
        browser_launcher=mock_failing_launcher,
    )

    assert res["success"] is False
    assert res["opened"] is False
    assert res["verified"] is False
    assert "Failed to open website" in res["response"]

    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "fail-ret-false").first()
    assert receipt is not None
    assert receipt.success is False  # Must NOT assert success!
    assert "Failed to open website" in receipt.message


def test_p1_b_failure_launcher_raises_exception(db, clock):
    """Failure case: launcher raises OS/webbrowser error."""
    def mock_exploding_launcher(url):
        raise webbrowser.Error("No browser executable found on system PATH.")

    res = system_actions.execute_open_url(
        "https://github.com",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="fail-exception",
        browser_launcher=mock_exploding_launcher,
    )

    assert res["success"] is False
    assert res["opened"] is False
    assert res["verified"] is False
    assert "No browser executable found" in res["response"]

    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "fail-exception").first()
    assert receipt is not None
    assert receipt.success is False
    assert "No browser executable found" in receipt.message


def test_p1_b_failure_unresolvable_target(db, clock):
    """Failure case: random nonsense that is neither an alias nor a valid URL."""
    res = system_actions.execute_open_url(
        "nonexistentwebsiteunknown12345",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="fail-unresolvable",
    )

    assert res["success"] is False
    assert res["opened"] is False
    assert "I couldn't find a website matching" in res["response"]

    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "fail-unresolvable").first()
    assert receipt is not None
    assert receipt.success is False


# ─────────────────────────────────────────────
# 4. Idempotency Replay
# ─────────────────────────────────────────────

def test_p1_b_idempotency_replay(db, clock):
    """Retrying with the same idempotency key returns the stored receipt without re-launching."""
    launch_count = []

    def mock_launcher(url):
        launch_count.append(url)
        return True

    # First attempt
    res1 = system_actions.execute_open_url(
        "github",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="idem-key-42",
        browser_launcher=mock_launcher,
    )
    assert res1["success"] is True
    assert len(launch_count) == 1

    # Second attempt with same idempotency key
    res2 = system_actions.execute_open_url(
        "github",
        db=db,
        clock=clock,
        source="test",
        idempotency_key="idem-key-42",
        browser_launcher=mock_launcher,
    )
    assert res2["success"] is True
    assert res2["receipt"]["replayed"] is True
    # Launcher was NOT called again
    assert len(launch_count) == 1


# ─────────────────────────────────────────────
# 5. Pipeline Integration via tools.execute_intent
# ─────────────────────────────────────────────

def test_p1_b_tools_execute_intent_integration(db, clock):
    """Executing open_website through tools.execute_intent runs the full pipeline."""
    called = []

    def mock_launcher(url):
        called.append(url)
        return True

    receipt = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "leetcode", "browser_launcher": mock_launcher},
        source="test",
        idempotency_key="tools-exec-1"
    )

    assert receipt["success"] is True
    assert receipt["action"] == "open_website"
    assert "Opening Leetcode..." in receipt["message"]
    assert called == ["https://leetcode.com"]

    # Stored in SQLite
    stored = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "tools-exec-1").first()
    assert stored is not None
    assert stored.success is True


def test_p1_b_system_open_url_alias_in_tools(db, clock):
    """Executing system.open_url through tools.execute_intent succeeds with receipt."""
    called = []

    def mock_launcher(url):
        called.append(url)
        return True

    receipt = tools.execute_intent(
        db, clock, "system.open_url",
        {"url": "https://python.org", "browser_launcher": mock_launcher},
        source="test",
        idempotency_key="sys-url-1"
    )

    assert receipt["success"] is True
    assert receipt["action"] == "system.open_url"
    assert called == ["https://python.org"]


# ─────────────────────────────────────────────
# 6. /chat and Fast-Path End-to-End Integration
# ─────────────────────────────────────────────

def test_p1_b_chat_fast_path_permitted_creates_receipt(client, db, monkeypatch):
    """Full HTTP /chat request for 'open github' executes via fast-path, returns opened=True,
    and commits a verified ActionReceipt in the database."""
    called = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: called.append(url) or True)

    resp = client.post("/chat", json={
        "message": "open github",
        "idempotencyKey": "chat-fast-perm-1",
        "source": "chat",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["opened"] is True
    assert "Opening Github..." in data["response"]
    assert data["receipt"] is not None
    assert data["receipt"]["success"] is True
    assert called == ["https://github.com"]

    # Verify receipt committed in DB
    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "chat-fast-perm-1").first()
    assert receipt is not None
    assert receipt.success is True


def test_p1_b_chat_fast_path_policy_denial(client, db, monkeypatch):
    """Full HTTP /chat request with a forbidden scheme is denied by policy,
    returns opened=False, and commits an unverified/denied ActionReceipt."""
    called = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda url: called.append(url) or True)

    resp = client.post("/chat", json={
        "message": "open file:///C:/Windows/System32/drivers/etc/hosts",
        "idempotencyKey": "chat-fast-deny-1",
        "source": "chat",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["opened"] is False
    assert len(called) == 0  # Never called
    assert "Policy denied" in data["response"]
    assert data["receipt"] is not None
    assert data["receipt"]["success"] is False

    # Stored in DB as policy refusal
    receipt = db.query(ActionReceipt).filter(ActionReceipt.idempotency_key == "chat-fast-deny-1").first()
    assert receipt is not None
    assert receipt.success is False
    assert "Policy denied" in receipt.message


# ─────────────────────────────────────────────
# 7. Focused Source-Review Regression Tests (P1-B Correction Milestone)
# ─────────────────────────────────────────────

def test_model_lane_open_website_idempotency_and_replay(db, clock, monkeypatch):
    """Finding 1: URL actions via model lane get an action key and obey replay contract."""
    from db import SessionLocal

    launcher_calls = []
    monkeypatch.setattr(system_actions, "get_browser_launcher",
                        lambda: (lambda url: launcher_calls.append(url) or True))

    provider = FakeProvider(response=ProviderResponse(
        text="", proposals=[ToolProposal(name="open_website", args={"site_or_url": "github"})]
    ))
    model_lane._PROVIDERS["ollama"] = provider

    try:
        # First model turn with client idempotency key
        out1 = model_lane.run_model_turn(
            SessionLocal, clock, "open github", [],
            provider_name="ollama", source="chat", idempotency_key="client-key-abc"
        )
        assert out1["opened"] is True
        assert out1["receipt"] is not None
        assert out1["receipt"]["success"] is True
        assert out1["receipt"].get("replayed") is not True
        assert launcher_calls == ["https://github.com"]

        # Exactly 1 receipt in DB
        receipts = db.query(ActionReceipt).all()
        assert len(receipts) == 1
        assert receipts[0].idempotency_key == "client-key-abc:open_website"

        # Second model turn with the SAME client idempotency key
        out2 = model_lane.run_model_turn(
            SessionLocal, clock, "open github", [],
            provider_name="ollama", source="chat", idempotency_key="client-key-abc"
        )
        assert out2["opened"] is True
        assert out2["receipt"] is not None
        assert out2["receipt"]["replayed"] is True
        # Launcher was NOT called again on replay
        assert len(launcher_calls) == 1
        # Still exactly 1 receipt in DB
        assert db.query(ActionReceipt).count() == 1
    finally:
        model_lane.reset_providers()


def test_model_lane_read_only_tool_has_no_action_key_and_no_receipt(db, clock, monkeypatch):
    """Finding 1: Read-only tools (produces_receipt=False) must receive no action key and produce no receipt."""
    from db import SessionLocal

    provider = FakeProvider(response=ProviderResponse(
        text="", proposals=[ToolProposal(name="list_tasks", args={})]
    ))
    model_lane._PROVIDERS["ollama"] = provider

    try:
        out = model_lane.run_model_turn(
            SessionLocal, clock, "show my tasks", [],
            provider_name="ollama", source="chat", idempotency_key="client-key-ro"
        )
        # Read-only tool produces no ActionReceipt in the database
        assert db.query(ActionReceipt).count() == 0
        assert out["opened"] is False
    finally:
        model_lane.reset_providers()


def test_direct_executor_single_receipt_on_success(db, clock):
    """Finding 3: Direct executor route (tools.execute_intent) produces exactly ONE authoritative receipt on success."""
    calls = []
    mock_launcher = lambda url: calls.append(url) or True

    receipt = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "github", "browser_launcher": mock_launcher},
        source="typed",
        idempotency_key="direct-success-key"
    )

    assert receipt["success"] is True
    assert receipt["action"] == "open_website"
    assert "Opening Github..." in receipt["message"]
    assert calls == ["https://github.com"]

    # Critical check: exactly 1 receipt written to DB (no double write between handle_open_website and execute_intent)
    receipts = db.query(ActionReceipt).all()
    assert len(receipts) == 1
    assert receipts[0].idempotency_key == "direct-success-key"
    assert receipts[0].success is True


def test_direct_executor_single_receipt_on_policy_denial(db, clock):
    """Finding 3: Direct executor route produces exactly ONE authoritative receipt on policy denial."""
    calls = []
    mock_launcher = lambda url: calls.append(url) or True

    receipt = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "file:///etc/passwd", "browser_launcher": mock_launcher},
        source="typed",
        idempotency_key="direct-denial-key"
    )

    assert receipt["success"] is False
    assert receipt["action"] == "open_website"
    assert "Policy denied" in receipt["message"]
    assert len(calls) == 0  # Launcher never invoked

    # Exactly 1 receipt in DB
    receipts = db.query(ActionReceipt).all()
    assert len(receipts) == 1
    assert receipts[0].idempotency_key == "direct-denial-key"
    assert receipts[0].success is False


def test_direct_executor_single_receipt_on_launcher_failure(db, clock):
    """Finding 3: Direct executor route produces exactly ONE authoritative receipt on launcher failure."""
    mock_launcher = lambda url: False  # Launcher returned False (not accepted)

    receipt = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "github", "browser_launcher": mock_launcher},
        source="typed",
        idempotency_key="direct-failure-key"
    )

    assert receipt["success"] is False
    assert receipt["action"] == "open_website"
    assert "not accepted" in receipt["message"]

    # Exactly 1 receipt in DB
    receipts = db.query(ActionReceipt).all()
    assert len(receipts) == 1
    assert receipts[0].idempotency_key == "direct-failure-key"
    assert receipts[0].success is False


def test_direct_executor_replay_preserves_single_receipt(db, clock):
    """Finding 1 & 3: Calling execute_intent twice with the same idempotency key replays the receipt without extra writes."""
    calls = []
    mock_launcher = lambda url: calls.append(url) or True

    r1 = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "github", "browser_launcher": mock_launcher},
        source="typed",
        idempotency_key="direct-replay-key"
    )
    assert r1["success"] is True
    assert len(calls) == 1
    assert db.query(ActionReceipt).count() == 1

    r2 = tools.execute_intent(
        db, clock, "open_website",
        {"site_or_url": "github", "browser_launcher": mock_launcher},
        source="typed",
        idempotency_key="direct-replay-key"
    )
    assert r2["success"] is True
    assert r2["replayed"] is True
    assert len(calls) == 1  # Not called again
    assert db.query(ActionReceipt).count() == 1


def test_truthful_verifier_status_and_evidence():
    """Finding 4: Verifier must report 'launch_accepted' with desktop_verified=False and unobserved desktop evidence."""
    # When launcher accepts the request:
    ver_ok = verifier.verify_url_launch("https://github.com", launcher_return=True)
    assert ver_ok.verified_initiation is True
    assert ver_ok.status == "launch_accepted"
    assert ver_ok.desktop_verified is False
    assert ver_ok.evidence["desktop_observation"] == "unobserved"
    assert ver_ok.evidence["launch_accepted"] is True
    assert "limitation" in ver_ok.evidence

    # When launcher refuses/fails:
    ver_unver = verifier.verify_url_launch("https://github.com", launcher_return=False)
    assert ver_unver.verified_initiation is False
    assert ver_unver.status == "unverified"
    assert ver_unver.desktop_verified is False
    assert ver_unver.evidence["desktop_observation"] == "unobserved"

    # When launcher raises an exception:
    ver_err = verifier.verify_url_launch("https://github.com", None, error=RuntimeError("Subprocess failed"))
    assert ver_err.verified_initiation is False
    assert ver_err.status == "failed"
    assert ver_err.desktop_verified is False
    assert ver_err.evidence["desktop_observation"] == "unobserved"
    assert "Subprocess failed" in ver_err.evidence["error"]


def test_tool_registry_proposal_enforces_policy_and_produces_receipt(db, clock, monkeypatch):
    """Finding 2: Registry execution has no bypass; always enforces policy, verification, and audit."""
    from db import SessionLocal

    calls = []
    monkeypatch.setattr(system_actions, "get_browser_launcher",
                        lambda: (lambda url: calls.append(url) or True))

    validated = tool_registry.validate_proposal(
        ToolProposal(name="open_website", args={"site_or_url": "github"}), clock)

    outcome = tool_registry.execute_proposal(
        validated, SessionLocal, clock, source="model",
        idempotency_key="registry-test-1", command_text="open github"
    )

    assert outcome["opened"] is True
    assert outcome["receipt"] is not None
    assert outcome["receipt"]["success"] is True
    assert calls == ["https://github.com"]
    assert db.query(ActionReceipt).count() == 1

