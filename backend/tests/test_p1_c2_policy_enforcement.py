"""Focused tests for P1-C2: Authoritative policy enforcement across existing tools.

Verifies:
1. Tool inventory and risk classification across all 26 tools.
2. Equivalent policy decisions and enforcement across all four entry paths:
   - Deterministic route (dispatcher.dispatch_command)
   - REST API route (FastAPI endpoints)
   - Fast-path launch route (system_actions.check_fast_path)
   - Model proposal route (model_lane.run_model_turn / tool_registry.execute_proposal)
3. Denial with zero side effects (no process execution, no browser launch, no database mutations).
4. Read-only invariance under policy (zero ActionReceipt records created, truthful output).
5. Future Risk 2/3 confirmation contract refusal (fails closed).
"""

import pytest

import policy
import system_actions
import tool_registry
import tools
from db import ActionReceipt, Task, Timer, Workspace
from providers.base import ToolProposal
from tests.test_model_lane import FakeProvider, proposal_response, install


@pytest.fixture()
def fake_catalog(monkeypatch):
    monkeypatch.setattr(system_actions, "_CATALOG_READY", True)
    monkeypatch.setattr(system_actions, "DYNAMIC_APP_CATALOG", {
        "calculator": "calc.exe",
        "notepad": "notepad.exe",
        "terminal": "cmd.exe",
    })


# ── 1. Tool inventory and risk level classification ─────────────────────────

def test_tool_inventory_and_risk_classifications():
    """All 26 registered tools have explicit, verified Risk 0 or Risk 1 classifications."""
    assert len(policy.RISK_0_READ_ONLY_TOOLS) == 8
    assert len(policy.RISK_1_MUTATING_TOOLS) == 15
    assert len(policy.RISK_1_LAUNCH_TOOLS) == 3
    total_tools = (len(policy.RISK_0_READ_ONLY_TOOLS) +
                   len(policy.RISK_1_MUTATING_TOOLS) +
                   len(policy.RISK_1_LAUNCH_TOOLS))
    assert total_tools == 26

    # Verify every read-only tool is Risk 0 and requires no confirmation
    for tool_name in policy.RISK_0_READ_ONLY_TOOLS:
        decision = policy.evaluate_policy(tool_name, {})
        assert decision.allowed is True
        assert decision.risk_level == policy.RISK_LEVEL_0_READ_ONLY
        assert decision.requires_confirmation is False
        assert policy.get_tool_risk_level(tool_name) == 0

    # Verify every mutating tool is Risk 1 and requires no confirmation
    for tool_name in policy.RISK_1_MUTATING_TOOLS:
        decision = policy.evaluate_policy(tool_name, {})
        assert decision.allowed is True
        assert decision.risk_level == policy.RISK_LEVEL_1_BOUNDED_SAFE
        assert decision.requires_confirmation is False
        assert policy.get_tool_risk_level(tool_name) == 1

    # Verify every launch tool is Risk 1
    for tool_name in policy.RISK_1_LAUNCH_TOOLS:
        assert policy.get_tool_risk_level(tool_name) == 1

    # Verify unknown tool fails closed as Risk 3 Critical
    unknown = policy.evaluate_policy("execute_arbitrary_shell", {})
    assert unknown.allowed is False
    assert unknown.risk_level == policy.RISK_LEVEL_3_CRITICAL
    assert "Policy denied" in unknown.reason
    assert policy.get_tool_risk_level("execute_arbitrary_shell") == 3


def test_tool_registry_metadata_matches_policy_risk_level():
    """Tool registry entries declare risk_level matching the policy engine."""
    for tool_id, entry in tool_registry.REGISTRY.items():
        expected_risk = policy.get_tool_risk_level(tool_id)
        assert entry["risk_level"] == expected_risk, f"Mismatch for tool {tool_id}"


# ── 2. Equivalent policy decision across all entry routes ─────────────────────

def test_permitted_action_succeeds_across_routes(client, db, clock, fake_catalog, monkeypatch):
    """A permitted Risk 1 action succeeds consistently across routes."""
    # Route A: Direct Executor
    res_direct = tools.execute_intent(
        db, clock, "create_task", {"text": "Direct executor task"}, source="direct"
    )
    assert res_direct["success"] is True
    assert res_direct["policy_decision"]["allowed"] is True
    assert res_direct["policy_decision"]["risk_level"] == 1

    # Route B: Deterministic Command (via dispatcher)
    import dispatcher
    res_det = dispatcher.dispatch_command(
        db, clock, "add a task to study algorithms", source="typed"
    )
    assert res_det["handled"] is True
    assert res_det["receipt"]["success"] is True
    assert res_det["receipt"]["policy_decision"]["allowed"] is True

    # Route C: REST Endpoint
    r_rest = client.post("/api/tasks", json={"text": "REST endpoint task"})
    assert r_rest.status_code == 200
    assert r_rest.json()["text"] == "REST endpoint task"

    # Route D: Model Proposal
    import model_lane
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "Model proposal task"}
    )))
    res_model = model_lane.run_model_turn(
        lambda: db, clock, "schedule a task", history=[]
    )
    assert res_model["receipt"]["success"] is True
    assert res_model["receipt"]["policy_decision"]["allowed"] is True


def test_denied_target_fails_closed_across_routes(client, db, clock, fake_catalog, monkeypatch):
    """A policy-denied target fails closed across all routes without executing side effects."""
    launcher_calls = []
    monkeypatch.setattr(system_actions, "get_app_launcher",
                        lambda: lambda cmd: launcher_calls.append(cmd) or True)

    # Route A: Direct Executor denial
    res_direct = tools.execute_intent(
        db, clock, "open_app", {"name": "cmd.exe"}, source="direct"
    )
    assert res_direct["success"] is False
    assert "Policy denied" in res_direct["message"]
    assert launcher_calls == []

    # Route B: Fast-Path Launch denial
    is_fast, resp, receipt = system_actions.check_fast_path(
        "open cmd.exe", db=db, clock=clock, source="system"
    )
    assert is_fast is True
    assert "Policy denied" in resp
    assert launcher_calls == []

    # Route C: Model Proposal denial
    import model_lane
    install(FakeProvider(response=proposal_response(
        "open_app", {"name": "powershell.exe"}
    )))
    res_model = model_lane.run_model_turn(
        lambda: db, clock, "open powershell.exe", history=[]
    )
    assert res_model["opened"] is False
    assert launcher_calls == []


def test_unknown_tool_fails_closed_across_routes(db, clock):
    """Unknown tools fail closed with Risk 3 policy denial across all routes."""
    # Direct executor
    with pytest.raises(tools.ToolError, match="Policy denied: Unknown or unregistered action"):
        tools.execute_intent(db, clock, "dangerous_system_wipe", {})

    # Model proposal route
    proposal = ToolProposal(name="dangerous_system_wipe", args={})
    with pytest.raises(tool_registry.ProposalRejected, match="Policy denied: Unknown or unregistered action"):
        tool_registry.validate_proposal(proposal, clock)


# ── 3. Denial with zero side effects ──────────────────────────────────────────

def test_policy_denial_leaves_zero_entity_side_effects(db, clock, fake_catalog, monkeypatch):
    """Policy denial rolls back any uncommitted state and launches no processes."""
    app_calls = []
    browser_calls = []
    monkeypatch.setattr(system_actions, "get_app_launcher",
                        lambda: lambda cmd: app_calls.append(cmd) or True)
    monkeypatch.setattr(system_actions, "get_browser_launcher",
                        lambda: lambda url: browser_calls.append(url) or True)

    initial_tasks = db.query(Task).count()
    initial_timers = db.query(Timer).count()

    # Attempt forbidden URL scheme
    res_url = tools.execute_intent(
        db, clock, "open_website", {"site_or_url": "javascript:steal_token()"},
        idempotency_key="deny-url-1"
    )
    assert res_url["success"] is False
    assert "forbidden" in res_url["message"]
    assert browser_calls == []

    # Attempt forbidden app
    res_app = tools.execute_intent(
        db, clock, "open_app", {"name": "bash"},
        idempotency_key="deny-app-1"
    )
    assert res_app["success"] is False
    assert "interpreters" in res_app["message"]
    assert app_calls == []

    # Verify zero side effects on entities
    assert db.query(Task).count() == initial_tasks
    assert db.query(Timer).count() == initial_timers

    # Verify failure receipts were recorded truthfully for audit
    receipts = db.query(ActionReceipt).all()
    assert len(receipts) == 2
    for r in receipts:
        assert r.success is False
        assert "Policy denied" in r.message


# ── 4. Read-only invariance under policy ─────────────────────────────────────

@pytest.mark.parametrize("tool_name,params", [
    ("list_tasks", {}),
    ("get_today", {}),
    ("list_workspaces", {}),
    ("resume_workspace", {}),
    ("get_ai_radar_digest", {}),
    ("list_coursework", {}),
    ("suggest_study", {"minutes": 25}),
    ("build_session_draft", {}),
])
def test_read_only_tools_produce_zero_receipts_and_zero_mutations(db, clock, tool_name, params):
    """All 8 read-only tools execute cleanly under policy without creating any ActionReceipt rows."""
    # Seed a workspace so resume_workspace has a registered project to inspect
    if db.query(Workspace).count() == 0:
        db.add(Workspace(name="Project Alpha", key="project-alpha", type="personal",
                          updated_utc=clock.now_utc(), created_utc=clock.now_utc()))
        db.commit()

    receipt_count_before = db.query(ActionReceipt).count()

    result = tools.execute_intent(db, clock, tool_name, params, source="test")

    assert result["success"] is True
    assert result.get("read_only") is True
    assert result["policy_decision"]["allowed"] is True
    assert result["policy_decision"]["risk_level"] == policy.RISK_LEVEL_0_READ_ONLY

    # No ActionReceipt rows created
    assert db.query(ActionReceipt).count() == receipt_count_before


# ── 5. Future confirmation contract refusal ───────────────────────────────────

def test_future_risk_tiers_refuse_without_confirmation(monkeypatch):
    """Simulated Risk 2 and Risk 3 tools fail closed and demand confirmation."""
    monkeypatch.setattr(policy, "RISK_2_MODIFICATION_TOOLS", {"edit_project_file"})
    monkeypatch.setattr(policy, "RISK_3_CRITICAL_TOOLS", {"delete_database"})

    # Risk 2 tool
    dec_2 = policy.evaluate_policy("edit_project_file", {"path": "main.py"})
    assert dec_2.allowed is False
    assert dec_2.risk_level == policy.RISK_LEVEL_2_MODIFICATION
    assert dec_2.requires_confirmation is True
    assert "Risk 2 user confirmation" in dec_2.reason

    # Risk 3 tool
    dec_3 = policy.evaluate_policy("delete_database", {})
    assert dec_3.allowed is False
    assert dec_3.risk_level == policy.RISK_LEVEL_3_CRITICAL
    assert dec_3.requires_confirmation is True
    assert "Risk 3 user confirmation" in dec_3.reason
