"""Hermetic P1-C1 app-launch and cross-action replay checks."""

import pytest

from db import ActionReceipt
import policy
import system_actions
import tool_registry
import tools
import verifier
from providers.base import ToolProposal


@pytest.fixture()
def catalog(monkeypatch):
    monkeypatch.setattr(system_actions, "_CATALOG_READY", True)
    monkeypatch.setattr(system_actions, "DYNAMIC_APP_CATALOG", {
        "calculator": "calc.exe", "notepad": "notepad.exe",
        "terminal": "cmd.exe", "legacy": "start winword",
    })


@pytest.mark.parametrize("name", ["", " ", "x" * 201, "cmd", "powershell",
                                  "python.exe", "bash", "wscript", "Windows PowerShell",
                                  "notepad; calc"])
def test_app_policy_denies_unsafe_names(name):
    assert policy.evaluate_app_policy(name).allowed is False


def test_verifier_reports_initiation_without_desktop_claim():
    result = verifier.verify_app_launch("Calculator", True)
    assert result.status == "launch_accepted"
    assert result.verified_initiation is True
    assert result.desktop_verified is False
    assert result.evidence["desktop_observation"] == "unobserved"


def test_direct_app_launch_and_replay_are_single_receipt(db, clock, catalog):
    called = []
    launcher = lambda command: called.append(command) or True
    first = system_actions.execute_open_app(
        "calculator", db=db, clock=clock, idempotency_key="app-1", app_launcher=launcher)
    second = system_actions.execute_open_app(
        "calculator", db=db, clock=clock, idempotency_key="app-1", app_launcher=launcher)
    assert first["success"] is True
    assert first["verification"]["desktop_verified"] is False
    assert second["receipt"]["replayed"] is True
    assert called == ["calc.exe"]
    assert db.query(ActionReceipt).count() == 1


def test_app_policy_denial_and_launcher_failure_are_audited(db, clock, catalog):
    called = []
    denied = system_actions.execute_open_app(
        "cmd", db=db, clock=clock, idempotency_key="deny-app",
        app_launcher=lambda command: called.append(command) or True)
    assert denied["success"] is False and called == []
    failed = system_actions.execute_open_app(
        "notepad", db=db, clock=clock, idempotency_key="fail-app",
        app_launcher=lambda command: (_ for _ in ()).throw(OSError("mock launch failure")))
    assert failed["success"] is False
    assert failed["verification"]["status"] == "failed"
    assert db.query(ActionReceipt).count() == 2


def test_ambiguous_app_match_does_not_launch(db, clock, monkeypatch):
    monkeypatch.setattr(system_actions, "_CATALOG_READY", True)
    monkeypatch.setattr(system_actions, "DYNAMIC_APP_CATALOG", {
        "notepad plus": "one.exe", "notepad pro": "two.exe"})
    called = []
    result = system_actions.execute_open_app(
        "notepad", db=db, clock=clock,
        app_launcher=lambda command: called.append(command) or True)
    assert result["success"] is False
    assert "Several applications" in result["message"]
    assert called == []


def test_legacy_shell_and_interpreter_commands_refused(db, clock, catalog):
    for name in ("legacy", "terminal"):
        result = system_actions.execute_open_app(
            name, db=db, clock=clock, app_launcher=lambda _: pytest.fail("launched"))
        assert result["success"] is False
    assert db.query(ActionReceipt).count() == 2


def test_executor_and_model_proposal_write_one_receipt(db, clock, catalog, monkeypatch):
    called = []
    monkeypatch.setattr(system_actions, "get_app_launcher",
                        lambda: lambda command: called.append(command) or True)
    first = tools.execute_intent(db, clock, "open_app", {"name": "calculator"},
                                 idempotency_key="executor-app")
    assert first["success"] is True
    assert db.query(ActionReceipt).count() == 1
    validated = tool_registry.validate_proposal(
        ToolProposal(name="open_app", args={"name": "notepad"}), clock)
    from db import SessionLocal
    second = tool_registry.execute_proposal(
        validated, SessionLocal, clock, idempotency_key="model-app")
    assert second["opened"] is True
    assert second["receipt"]["action"] == "open_app"
    assert db.query(ActionReceipt).count() == 2
    assert called == ["calc.exe", "notepad.exe"]


def test_fast_path_app_receipt_and_replay(client, db, catalog, monkeypatch):
    called = []
    monkeypatch.setattr(system_actions, "get_app_launcher",
                        lambda: lambda command: called.append(command) or True)
    payload = {"message": "open calculator", "idempotencyKey": "fast-app"}
    first = client.post("/chat", json=payload).json()
    second = client.post("/chat", json=payload).json()
    assert first["opened"] is True
    assert first["receipt"]["action"] == "open_app"
    assert second["receipt"]["replayed"] is True
    assert called == ["calc.exe"]
    assert db.query(ActionReceipt).count() == 1


def test_reused_key_for_different_action_or_target_conflicts(db, clock, catalog):
    launcher = lambda command: True
    system_actions.execute_open_app("calculator", db=db, clock=clock,
                                    idempotency_key="shared", app_launcher=launcher)
    with pytest.raises(tools.ToolError, match="another action or target"):
        tools.execute_intent(db, clock, "open_app", {"name": "notepad"},
                             idempotency_key="shared")
    with pytest.raises(tools.ToolError, match="another action or target"):
        system_actions.execute_open_app("notepad", db=db, clock=clock,
                                        idempotency_key="shared", app_launcher=lambda _: pytest.fail("launched"))
    with pytest.raises(tools.ToolError, match="another action or target"):
        tools.execute_intent(db, clock, "create_task", {"text": "wrong replay"},
                             idempotency_key="shared")
    with pytest.raises(tools.ToolError, match="another action or target"):
        system_actions.execute_open_url("https://github.com", db=db, clock=clock,
                                        idempotency_key="shared", browser_launcher=lambda _: pytest.fail("launched"))
    assert db.query(ActionReceipt).count() == 1


def test_fast_path_conflict_returns_message_without_launch(client, db, catalog, monkeypatch):
    called = []
    monkeypatch.setattr(system_actions, "get_app_launcher",
                        lambda: lambda command: called.append(command) or True)
    first = client.post("/chat", json={"message": "open calculator", "idempotencyKey": "conflicting-fast"})
    second = client.post("/chat", json={"message": "open notepad", "idempotencyKey": "conflicting-fast"})
    assert first.json()["opened"] is True
    assert second.status_code == 200
    assert second.json()["opened"] is False
    assert "another action or target" in second.json()["response"]
    assert called == ["calc.exe"]
    assert db.query(ActionReceipt).count() == 1


def test_same_action_with_different_payload_cannot_replay(db, clock):
    first = tools.execute_intent(db, clock, "create_task", {"text": "first task"},
                                 idempotency_key="task-payload")
    assert first["success"] is True
    with pytest.raises(tools.ToolError, match="another action or target"):
        tools.execute_intent(db, clock, "create_task", {"text": "second task"},
                             idempotency_key="task-payload")
    assert db.query(ActionReceipt).count() == 1
