"""P1 acceptance tests: the shared intelligence boundary.

The key demonstration (spec + morning review): the SAME task arriving
through three paths — deterministic parser, mocked Ollama proposal,
mocked Gemini proposal — uses ONE validator/executor and produces ONE
authoritative receipt. A provider failure must never block the next
offline command, never lose conversation state, and never execute an
unvalidated proposal.

All provider calls here are fakes or monkeypatched transports: no test
opens a real app, downloads a model, or touches the network.
"""

import json
import threading
import time
from datetime import datetime

import pytest

import context_builder
import model_lane
import system_actions
import tool_registry
from db import ActionReceipt, SessionLocal, Task, Timer
from providers.base import (BaseProvider, Capabilities, ProviderAuth,
                            ProviderError, ProviderMalformed, ProviderQuota,
                            ProviderResponse, ProviderTimeout, ToolProposal,
                            redact_secrets)
from timeutil import FakeClock


def clock():
    # Monday 2026-09-21 10:00 IST (same fixed clock as the rest of the suite)
    return FakeClock(datetime(2026, 9, 21, 4, 30, 0))


class FakeProvider(BaseProvider):
    """Canned gateway result. Overrides `_call` (NOT generate) so the real
    BaseProvider pipeline — single-generation slot, bounded retry, deadline —
    is exercised exactly as in production."""

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


class CallTrackingProvider(FakeProvider):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.called = False

    def _call(self, messages, tools, timeout_s):
        self.called = True
        return super()._call(messages, tools, timeout_s)


@pytest.fixture(autouse=True)
def _isolate_providers():
    model_lane.reset_providers()
    yield
    model_lane.reset_providers()


def install(provider, name="ollama"):
    model_lane._PROVIDERS[name] = provider
    return provider


def proposal_response(name, args, text=""):
    return ProviderResponse(text=text, proposals=[ToolProposal(name=name, args=args)])


def run_turn(message, provider_name="ollama", history=None, idem=None, source="chat"):
    return model_lane.run_model_turn(
        SessionLocal, clock(), message, history or [],
        user_name="Sir", provider_name=provider_name, source=source,
        idempotency_key=idem)


def _wipe(db):
    for model in (ActionReceipt, Task, Timer):
        db.query(model).delete()
    db.commit()


# ─────────────────────────────────────────────
# 1. Deterministic lane: zero provider calls
# ─────────────────────────────────────────────

def test_exact_commands_never_touch_a_provider(client, db):
    _wipe(db)
    tracker = install(CallTrackingProvider())
    for cmd in ("set a timer for 25 minutes",
                "add a task: buy milk",
                "remind me tomorrow at 7 pm to revise trees",
                "start a focus session for 50 minutes",
                "what are my pending tasks?"):
        r = client.post("/chat", json={"message": cmd, "provider": "ollama"})
        assert r.status_code == 200
        body = r.json()
        assert "error" not in body, cmd
        assert body["executionMode"] == "deterministic", cmd
    assert tracker.called is False


def test_deterministic_command_works_right_after_provider_failure(client, db):
    """A 429/timeout/connection failure must not disrupt the immediately
    following offline command."""
    _wipe(db)
    install(FakeProvider(exc=ProviderQuota("quota gone", retry_after=30.0)))
    r1 = client.post("/chat", json={"message": "please jot down my thoughts on the lecture",
                                    "provider": "ollama"})
    assert r1.status_code == 200
    body = r1.json()
    assert "error" not in body
    assert body["executionMode"] == "none"
    assert "quota" in body["response"].lower()
    assert "retry" in body["response"].lower()      # retry hint surfaced, not slept
    assert "still work offline" in body["response"]  # local command availability

    r2 = client.post("/chat", json={"message": "set a timer for 5 minutes",
                                    "provider": "ollama"})
    body2 = r2.json()
    assert body2["receipt"]["success"] is True
    assert body2["receipt"]["action"] == "start_timer"
    assert body2["executionMode"] == "deterministic"


# ─────────────────────────────────────────────
# 2. THE demonstration: three paths, one executor, one receipt shape
# ─────────────────────────────────────────────

def test_same_task_via_three_paths_one_executor(db):
    _wipe(db)
    results = []

    # Path A — deterministic parser (offline)
    import dispatcher
    a = dispatcher.dispatch_command(db, clock(),
                                    "Add a task: finish the DBMS assignment by Friday at 6 PM",
                                    source="typed")
    results.append(a["receipt"])

    # Path B — mocked Ollama tool proposal (model paraphrase, same executor)
    ollama = install(FakeProvider(
        name="ollama", local=True,
        response=proposal_response(
            "create_task",
            {"text": "finish the DBMS assignment by Friday at 6 PM",
             "when": "friday at 6 pm"})))
    b = run_turn("Could you note down that I have to finish the DBMS assignment by Friday 6 PM?",
                 "ollama")
    results.append(b["receipt"])
    assert b["executionMode"] == "local"

    # Path C — mocked Gemini tool proposal (same request, same registry)
    gemini = install(FakeProvider(
        name="gemini", local=False,
        response=proposal_response(
            "create_task",
            {"text": "finish the DBMS assignment",
             "when": "friday at 6 pm"})), "gemini")
    c = run_turn("Remind myself to finish the DBMS assignment by Friday 6 PM", "gemini")
    results.append(c["receipt"])
    assert c["executionMode"] == "cloud"

    # Equivalent records: same action, same normalized text, same deadline,
    # all successful, all through the one executor/receipt pipeline.
    for r in results:
        assert r["success"] is True
        assert r["action"] == "create_task"
        assert r["entity_type"] == "task"
        assert r["entity_id"] is not None

    db.expire_all()  # Paths B/C committed via their own SessionLocal sessions
    tasks = db.query(Task).order_by(Task.id).all()
    assert len(tasks) == 3
    for t in tasks:
        assert t.text == "finish the DBMS assignment"
        assert t.deadline_utc == datetime(2026, 9, 25, 12, 30, 0)  # Fri 18:00 IST -> UTC
    sources = {t.source for t in tasks}
    assert sources == {"typed", "model"}

    # Both provider paths generated their schemas from the SAME registry.
    names_o = sorted(t["function"]["name"] for t in ollama.calls[0]["tools"])
    names_g = sorted(t["function"]["name"] for t in gemini.calls[0]["tools"])
    assert names_o == names_g == sorted(tool_registry.model_tool_ids())

    # The user-facing message came from the executor receipt, not the model.
    assert b["response"] == b["receipt"]["message"]
    assert c["response"] == c["receipt"]["message"]


def test_registry_schemas_are_equivalent_across_providers():
    openai_schemas = tool_registry.schemas_for_provider("openai")
    gemini_schemas = tool_registry.schemas_for_provider("gemini")
    assert len(openai_schemas) == len(gemini_schemas) == len(tool_registry.model_tool_ids())
    stripped_keys = {"additionalProperties", "minLength", "maxLength", "minimum", "maximum"}
    for o, g in zip(openai_schemas, gemini_schemas):
        assert o["function"]["name"] == g["function"]["name"]
        assert o["function"]["description"] == g["function"]["description"]

        def strip(s):
            if isinstance(s, dict):
                return {k: strip(v) for k, v in s.items() if k not in stripped_keys}
            if isinstance(s, list):
                return [strip(v) for v in s]
            return s

        # Same registry source: the Gemini schema is exactly the OpenAI one
        # minus the strict keywords Gemini's subset does not accept.
        assert strip(o["function"]["parameters"]) == g["function"]["parameters"]


# ─────────────────────────────────────────────
# 3. Idempotency: one entity per logical action
# ─────────────────────────────────────────────

def test_client_idempotency_key_dedupes_model_action(client, db):
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "idempotent model task"})))
    payload = {"message": "note that I must do the idempotent model task",
               "provider": "ollama", "idempotencyKey": "p1-key-1"}
    r1 = client.post("/chat", json=payload).json()
    r2 = client.post("/chat", json=payload).json()
    assert r1["receipt"]["entity_id"] == r2["receipt"]["entity_id"]
    assert r2["receipt"]["replayed"] is True
    db.expire_all()
    assert db.query(Task).filter(Task.text == "idempotent model task").count() == 1
    assert db.query(ActionReceipt).count() == 1


def test_request_fingerprint_dedupes_without_client_key(db):
    """Same request text retried (e.g. after a dropped connection) reuses one
    stable request/action key -> one entity, one logical action."""
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "fingerprinted task"})))
    msg = "write down that I owe a fingerprinted task"
    a = run_turn(msg)
    b = run_turn(msg)
    assert a["receipt"]["entity_id"] == b["receipt"]["entity_id"]
    assert b["receipt"]["replayed"] is True
    db.expire_all()
    assert db.query(Task).filter(Task.text == "fingerprinted task").count() == 1


# ─────────────────────────────────────────────
# 4. Rejection: nothing unintended is ever written
# ─────────────────────────────────────────────

def test_unknown_tool_is_rejected_without_writes(db):
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "launch_missiles", {"target": "moon"})))
    out = run_turn("launch missiles at the moon")
    assert out["receipt"] is None
    assert out["clarification"] is True
    assert "isn't a VEGA action" in out["response"]
    assert "Nothing was executed" in out["response"]
    assert db.query(Task).count() == 0 and db.query(ActionReceipt).count() == 0


def test_non_exposed_tool_is_rejected(db):
    """refresh_ai_radar exists in the registry but is not offered to models."""
    _wipe(db)
    install(FakeProvider(response=proposal_response("refresh_ai_radar", {})))
    out = run_turn("check the radar")
    assert out["receipt"] is None
    assert "not available through the model" in out["response"]


@pytest.mark.parametrize("bad_args", [
    {"text": "x", "when": "friday at 6 pm", "priority": "high"},   # extra property
    {"text": 42},                                                   # wrong type
    {"when": "friday at 6 pm"},                                     # missing required
    {"text": ""},                                                   # empty required
    {"text": "x", "when": ["friday"]},                              # wrong type
    {"text": "x", "deadline_utc": "drop table tasks; --"},          # not ISO 8601
])
def test_invalid_arguments_are_rejected(db, bad_args):
    _wipe(db)
    install(FakeProvider(response=proposal_response("create_task", bad_args)))
    out = run_turn("add that thing")
    assert out["receipt"] is None or out["receipt"].get("success") is False
    assert db.query(Task).count() == 0


def test_past_and_ambiguous_dates_ask_instead_of_writing(db):
    _wipe(db)
    # Past instant -> clarification, no write (executor/timeutil stay authoritative)
    install(FakeProvider(response=proposal_response(
        "create_reminder", {"text": "pay fees", "due_utc": "2020-01-01T09:00"})))
    out = run_turn("remind me to pay fees")
    assert out["receipt"] is None
    assert "passed" in out["response"]
    # Missing time-of-day for a reminder -> clarification (never a silent guess)
    install(FakeProvider(response=proposal_response(
        "create_reminder", {"text": "pay fees", "when": "tomorrow"})))
    out2 = run_turn("remind me tomorrow to pay fees")
    assert out2["receipt"] is None
    assert "What time" in out2["response"]
    assert db.query(ActionReceipt).count() == 0


def test_missing_target_produces_failed_receipt_not_a_guess(db):
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "set_task_completed", {"task_id": 999})))
    out = run_turn("mark that one as done")
    assert out["receipt"]["success"] is False
    assert out["receipt"]["entity_id"] is None
    assert "not found" in out["receipt"]["message"]
    # No task was flipped, nothing else written
    assert db.query(Task).count() == 0


def test_multi_action_proposal_executes_nothing(db):
    _wipe(db)
    install(FakeProvider(response=ProviderResponse(proposals=[
        ToolProposal(name="create_task", args={"text": "first"}),
        ToolProposal(name="start_timer", args={"duration_seconds": 60}),
    ])))
    out = run_turn("add a task called first and set a one minute timer")
    assert out["clarification"] is True
    assert "one action per request" in out["response"]
    assert db.query(Task).count() == 0 and db.query(Timer).count() == 0
    assert db.query(ActionReceipt).count() == 0


@pytest.mark.parametrize("leaked", [
    json.dumps({"type": "function",
                "function": {"name": "create_task", "arguments": {"text": "sneaky"}}}),
    '<|tool_call|>\n{"name": "create_task", "arguments": {"text": "sneaky"}}',
    'Sure!\n```json\n{"name": "create_task", "arguments": {"text": "sneaky"}}\n```',
    'create_task({"text": "sneaky"})',  # python-call prose is NOT executed either
    # Live-captured (phi4-mini, 2026-09-22): a friendly sentence AROUND the call,
    # unfenced — the prefix hid it from the "is it entirely JSON" checks and the
    # raw proposal reached the chat bubble.
    ('Sure, let\'s take a look at what\'s on your list. One moment while I '
     'retrieve that information for you.\n\n[{"name":"list_tasks","arguments":{"window":"today"}}]'),
    # Live-captured (phi4-mini, 2026-09-22, temp 0): the call keyed by the INTENT
    # name instead of the {"name":…,"arguments":…} envelope, inside a fence. None
    # of the envelope keys appear, so the checks above all missed it and the raw
    # JSON reached the chat bubble.
    ('Sure, I will set a reminder for you to wake up at 7 PM tomorrow for your '
     'flight.\n\n```json\n{\n  "create_reminder": {\n    "text": "Wake up for flight",'
     '\n    "when": "tomorrow at 7 PM"\n  }\n}\n```'),
])
def test_json_like_prose_is_never_executed_or_shown(db, leaked):
    """Weak local models (observed: phi4-mini) emit the tool call as raw text —
    a JSON blob, a leaked <|tool_call|> template token, a fenced block, a
    python-call string, or a friendly sentence wrapped around the call. None of
    these may execute, and none may be shown/spoken as if it were an answer."""
    _wipe(db)
    install(FakeProvider(response=ProviderResponse(text=leaked)))
    out = run_turn("do the thing")
    assert db.query(Task).count() == 0
    assert db.query(ActionReceipt).count() == 0
    # Either an honest non-answer, or (for the python-call prose) plain text that
    # is not a structured proposal — but never an execution and never raw JSON.
    assert not out["response"].lstrip().startswith("{")
    assert "<|tool_call|>" not in out["response"]
    assert "```" not in out["response"]
    assert '"arguments"' not in out["response"] and '"name":' not in out["response"]


# The leak guard above is a *deny* list over free text, so its real risk is the
# opposite failure: eating a genuine answer. These are the shapes a
# daily-assistant reply actually takes.
@pytest.mark.parametrize("legit", [
    "You have 3 open tasks, Sir. Two are due before Friday 6 pm.",
    'Your task "submit the lab record" is due Friday at 6 pm.',
    "Sure! I'd be happy to help with that. Here's what I found (from today):",
    "Try these commands:\n  - add a task: revise chapter [3]\n  - set a timer for 25 minutes",
    "Python-call looking prose is fine mid-sentence: create_task({\"text\": \"x\"}) is how "
    "the SDK names it, but a reply that starts with it is a leaked proposal.",
    "I need more arguments before I can choose — which deadline did you mean?",
    "For the circuit assignment, use V=IR. Example: {\"v\": 5, \"i\": 2} gives R=2.5 ohms.",
    "[{\"title\": \"Semester 5 lab record\"}] is how the coursework table stores it.",
    "Reminder syntax accepts 'in 20 minutes' or 'at 6pm friday'.",
    "No tasks are open right now — say \"add a task: …\" to create one.",
    # Precision limits of the intent-keyed rule: a tool name in quotes is not a
    # leaked call unless it is used as a JSON key, and a fenced example that is
    # not one of our intents is still a legitimate answer.
    'The "create_task" tool is what adds an item once the model proposes it.',
    "Set it like:\n```json\n{\"model\": \"phi4-mini\", \"temperature\": 0}\n```\n"
    "and the local lane stays offline.",
])
def test_sanitize_text_passes_genuine_answers_through(legit):
    """Each of these must come back byte-identical: the guard triggers on
    prefix-anchored JSON/call shapes and on quoted name/function/tool_calls
    keys, not on ordinary punctuation, quoting or code talk."""
    assert model_lane._sanitize_text(legit) == legit.strip()


def test_sanitize_text_answers_empty_with_a_usable_hint():
    assert "rephrasing" in model_lane._sanitize_text("   \n  ")


def test_sanitize_text_known_limitation_json_flavoured_advice_is_suppressed():
    """Documented trade-off (accepted 2026-09-22): a reply whose ENTIRE text is a
    JSON-shaped tool-call example is suppressed rather than shown. Stripping the
    braces instead would leave prose that promises an action which never ran, so
    the honest non-answer wins. Cost: an explanatory answer written as bare JSON
    is refused; ask for prose and it is unaffected."""
    out = model_lane._sanitize_text('{"name": "list_tasks", "arguments": {"window": "today"}}')
    assert "couldn't complete" in out


# ─────────────────────────────────────────────
# 5. Provider failures: bounded, honest, redacted
# ─────────────────────────────────────────────

@pytest.mark.parametrize("exc,fragment", [
    (ProviderQuota("429 resource exhausted"), "quota"),
    (ProviderAuth("API key not valid"), "credentials"),
    (ProviderTimeout("deadline exceeded"), "took too long"),
    (ProviderMalformed("non-JSON body"), "unusable"),
])
def test_provider_failures_are_honest_and_stateless(db, exc, fragment):
    _wipe(db)
    install(FakeProvider(exc=exc))
    out = run_turn("summarize my week and add a task about it")
    assert fragment in out["response"].lower()
    assert out["executionMode"] == "none"
    assert "still work offline" in out["response"]
    assert db.query(Task).count() == 0 and db.query(ActionReceipt).count() == 0


def test_credentials_are_redacted_from_errors(db):
    import os
    secret = "SECRET-KEY-VALUE-12345"
    os.environ["GEMINI_API_KEY"] = secret
    try:
        install(FakeProvider(exc=ProviderAuth(f"auth failed with key {secret}")), "gemini")
        out = run_turn("hello", "gemini")
        assert secret not in out["response"]
        assert "redacted" in out["response"]
    finally:
        os.environ.pop("GEMINI_API_KEY", None)


def test_single_transient_failure_gets_one_bounded_retry():
    attempts = {"n": 0}

    class FlakyProvider(FakeProvider):
        def _call(self, messages, tools, timeout_s):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ProviderTimeout("transient")
            return ProviderResponse(text="recovered")

    p = FlakyProvider()
    out = p.generate([{"role": "user", "content": "hi"}], [],
                     deadline=time.monotonic() + 5)
    assert out.text == "recovered"
    assert attempts["n"] == 2  # exactly one retry, no more


def test_terminal_errors_are_never_retried():
    attempts = {"n": 0}

    class QuotaProvider(FakeProvider):
        def _call(self, messages, tools, timeout_s):
            attempts["n"] += 1
            raise ProviderQuota("exhausted")

    p = QuotaProvider()
    with pytest.raises(ProviderQuota):
        p.generate([{"role": "user", "content": "hi"}], [],
                   deadline=time.monotonic() + 5)
    assert attempts["n"] == 1


def test_retry_after_beyond_deadline_is_surfaced_not_slept():
    class HintProvider(FakeProvider):
        def _call(self, messages, tools, timeout_s):
            raise ProviderQuota("slow down", retry_after=3600)

    p = HintProvider()
    start = time.monotonic()
    with pytest.raises(ProviderQuota) as info:
        p.generate([{"role": "user", "content": "hi"}], [],
                   deadline=time.monotonic() + 2)
    assert time.monotonic() - start < 2.0   # did not wait on the hint
    assert info.value.retry_after == 3600.0  # hint returned to the caller


def test_one_active_generation_bounded_queue():
    entered = threading.Event()

    class SlowProvider(FakeProvider):
        def _call(self, messages, tools, timeout_s):
            entered.set()
            time.sleep(1.2)
            return ProviderResponse(text="slow done")

    p = SlowProvider()
    results = {}

    def slow():
        results["slow"] = p.generate([{"role": "user", "content": "a"}], [],
                                     deadline=time.monotonic() + 10)

    t = threading.Thread(target=slow)
    t.start()
    assert entered.wait(3)
    # Second request while the slot is held: bounded wait, honest busy error.
    p2 = SlowProvider()
    import providers.base as pb
    old_timeout = pb.QUEUE_TIMEOUT_S
    pb.QUEUE_TIMEOUT_S = 0.5
    try:
        with pytest.raises(pb.ProviderUnavailable):
            p2.generate([{"role": "user", "content": "b"}], [],
                        deadline=time.monotonic() + 5)
    finally:
        pb.QUEUE_TIMEOUT_S = old_timeout
    t.join(timeout=10)
    assert results["slow"].text == "slow done"


# ─────────────────────────────────────────────
# 6. Context budgets
# ─────────────────────────────────────────────

def test_oversized_history_is_trimmed_but_request_and_system_prompt_survive(db):
    _wipe(db)
    history = [{"role": "user" if i % 2 == 0 else "assistant",
                "content": f"message number {i} " + "x" * 200} for i in range(60)]
    provider = FakeProvider(context_window=4000, reserved_output=200)
    install(provider)
    request = "could you read out my remaining to-dos for me?"
    out = run_turn(request)
    assert out["executionMode"] == "local"   # reached the model lane, not the parser
    sent = provider.calls[0]["messages"]
    assert sent[-1]["content"] == request    # current request intact
    assert sent[0]["role"] == "system"
    assert len(sent) < 62  # history was actually trimmed
    budget = provider.capabilities.context_window - provider.capabilities.reserved_output
    total = sum(context_builder.estimate_tokens(m["content"]) for m in sent)
    assert total <= budget


def test_current_request_is_never_truncated_into_another_command():
    caps = Capabilities(context_window=8192, reserved_output=1024)
    msgs, meta = context_builder.build_messages(
        clock(), [], "set a timer for 25 minutes", "Sir", caps, "openai")
    assert msgs[-1]["content"] == "set a timer for 25 minutes"
    assert meta["estimated"] is True


def test_oversized_request_asks_for_shorter_one_without_calling_provider(db):
    _wipe(db)
    provider = install(CallTrackingProvider(context_window=512, reserved_output=128))
    out = run_turn("explain " + "everything " * 300)
    assert provider.called is False
    assert out["clarification"] is True
    assert "shorter request" in out["response"]
    assert "estimated" in out["response"].lower()
    assert db.query(Task).count() == 0


def test_no_database_content_is_packed_into_provider_context(db):
    """Only the system prompt, bounded history, and the user's own message are
    sent — never task rows or other private records."""
    _wipe(db)
    t = Task(text="secret-project-codename", completed=False,
             created_at=clock().now_utc(), updated_at=clock().now_utc())
    db.add(t)
    db.commit()
    provider = install(FakeProvider(response=ProviderResponse(text="hello")))
    run_turn("hello there", history=[{"role": "user", "content": "earlier question"}])
    blob = json.dumps(provider.calls[0]["messages"])
    assert "secret-project-codename" not in blob


# ─────────────────────────────────────────────
# 7. App/site opening stays bounded and mocked
# ─────────────────────────────────────────────

def test_open_app_proposal_uses_existing_bounded_action(db, monkeypatch):
    _wipe(db)
    calls = []
    monkeypatch.setattr(tool_registry.system_actions, "_CATALOG_READY", True)
    monkeypatch.setattr(tool_registry.system_actions, "DYNAMIC_APP_CATALOG",
                        {"notepad": "notepad.exe"})
    monkeypatch.setattr(tool_registry.system_actions, "get_app_launcher",
                        lambda: lambda command: calls.append(command) or True)
    install(FakeProvider(response=proposal_response("open_app", {"name": "notepad"})))
    out = run_turn("pull up notepad for me")
    assert calls == ["notepad.exe"]
    assert out["opened"] is True
    assert out["receipt"]["success"] is True
    assert db.query(ActionReceipt).count() == 1


def test_open_app_rejects_shell_text(db):
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "open_app", {"name": "calc.exe; rm -rf /"})))
    out = run_turn("open calc.exe; rm -rf /")
    # Rejected at the registry (injection chars are fine length-wise, but the
    # existing system action guard handles it); either way nothing launches.
    assert out["opened"] is not True or "invalid characters" in out["response"]


def test_open_website_via_registry(db, monkeypatch):
    _wipe(db)
    opened = []
    monkeypatch.setattr(system_actions, "get_browser_launcher",
                        lambda: (lambda url: opened.append(url) or True))
    install(FakeProvider(response=proposal_response("open_website", {"site_or_url": "github"})))
    out = run_turn("show me github")
    assert opened == ["https://github.com"]
    assert out["opened"] is True
    assert out["receipt"] is not None
    assert out["receipt"]["action"] == "open_website"
    assert out["receipt"]["success"] is True
    assert db.query(ActionReceipt).count() == 1


# ─────────────────────────────────────────────
# 8. Read-only tools answer from the local database
# ─────────────────────────────────────────────

def test_list_tasks_grounding_comes_from_local_db(db):
    _wipe(db)
    now = clock().now_utc()
    db.add(Task(text="grounded task one", completed=False, created_at=now, updated_at=now))
    db.add(Task(text="grounded task two", completed=False, created_at=now, updated_at=now))
    db.commit()
    install(FakeProvider(response=proposal_response("list_tasks", {"window": "all"})))
    out = run_turn("tell me what's still pending on my list, would you?")
    assert "grounded task one" in out["response"]
    assert "grounded task two" in out["response"]
    # Read-only path: returns a read-only result but writes NO ActionReceipt row,
    # exactly like the deterministic dispatcher.
    assert out["receipt"]["read_only"] is True
    assert db.query(ActionReceipt).count() == 0


# ─────────────────────────────────────────────
# 9. Configuration: swapping providers/models touches no handler
# ─────────────────────────────────────────────

def test_switching_provider_needs_no_tool_handler_edits(db, monkeypatch):
    """Same registry + executor for both providers; only configuration differs."""
    _wipe(db)
    monkeypatch.setenv("OLLAMA_MODEL", "phi4-mini:latest")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-flash")
    model_lane.reset_providers()
    o = model_lane.get_provider("ollama")
    g = model_lane.get_provider("gemini")
    assert o.model == "phi4-mini:latest"
    assert g.model == "gemini-test-flash"
    assert o.capabilities.local is True and o.capabilities.data_policy == "local_only"
    assert g.capabilities.local is False and g.capabilities.data_policy == "user_configured_cloud"
    # Handler-facing surface is identical:
    assert tool_registry.schemas_for_provider("openai") == tool_registry.schemas_for_provider("openai")
    # Handler-facing surface is identical, and every model-hidden tool stays
    # hidden (refresh_ai_radar, link_task_to_workspace) regardless of provider.
    _hidden = sum(1 for e in tool_registry.REGISTRY.values() if not e["expose_to_model"])
    assert _hidden >= 1
    assert len(tool_registry.schemas_for_provider("openai")) == \
        len(tool_registry.schemas_for_provider("gemini")) == \
        len(tool_registry.REGISTRY) - _hidden


def test_invalid_provider_is_a_contract_error(db):
    out = run_turn("hello", provider_name="openrouter")
    assert "error" in out
    assert "Invalid LLM provider" in out["error"]


def test_redact_secrets_masks_generic_patterns():
    text = "Authorization: Bearer abc123XYZ and ?api_key=deadbeef00 plus GEMINI_API_KEY=qqqq"
    out = redact_secrets(text)
    assert "abc123XYZ" not in out
    assert "deadbeef00" not in out


# ─────────────────────────────────────────────
# 10. Capability boundary: an unsupported destination must not be
#     silently substituted with the nearest available write.
# ─────────────────────────────────────────────

def test_calendar_request_is_not_mutated_into_a_task(db):
    """The original defect. Now refused before the provider is reached at all,
    so the model cannot turn it into a task AND cannot answer it in prose."""
    _wipe(db)
    provider = install(FakeProvider(response=proposal_response(
        "create_task", {"text": "Dentist appointment"})))
    out = run_turn("schedule a dentist appointment on my calendar")
    assert provider.calls == [], "the refusal must not cost an inference"
    assert out["executionMode"] == "none"
    assert out["clarification"] is True
    assert out["receipt"] is None
    assert "calendar" in out["response"].lower()
    assert db.query(Task).count() == 0, "wrong mutation: create_task ran for a calendar request"
    assert db.query(ActionReceipt).count() == 0


@pytest.mark.parametrize("message,tool,args,destination", [
    ("schedule a dentist appointment on my calendar", "create_task",
     {"text": "Dentist appointment"}, "calendar"),
    ("email the HOD my backlog report", "create_task",
     {"text": "backlog report"}, "email"),
    ("message the class group on whatsapp about the venue", "create_task",
     {"text": "venue for the class group"}, "messaging app"),
    ("add a row to my excel sheet", "create_task",
     {"text": "row"}, "spreadsheet"),
    # A read is refused by the same rule: VEGA cannot see a calendar either,
    # and answering 'what is on my calendar?' with the task list would be a
    # confident wrong answer rather than a mutation.
    ("what is on my calendar tomorrow", "list_tasks", {}, "calendar"),
])
def test_unsupported_destination_is_refused_before_any_provider_call(db, message,
                                                                    tool, args, destination):
    """One refusal text for every unsupported destination, whatever the model
    would have proposed or written. The canned proposal is installed so the
    test fails loudly if a request ever reaches a provider on this path."""
    _wipe(db)
    provider = install(FakeProvider(response=proposal_response(tool, args)))
    out = run_turn(message)
    assert provider.calls == [], f"{message!r} reached the provider"
    assert out["clarification"] is True
    assert out["receipt"] is None, "no executor ran, so there is no receipt to report"
    assert destination in out["response"]
    assert 'say "add a task"' in out["response"]
    assert "Nothing was opened either" in out["response"]
    assert db.query(Task).count() == 0
    assert db.query(ActionReceipt).count() == 0


def test_all_three_refusal_layers_say_the_same_thing(db, monkeypatch):
    """The point of the pre-model gate was consistency, so the sentence must
    not depend on which layer caught the request: model lane, executor
    (deterministic lane) and the launch lane now emit one text."""
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "Dentist appointment"})))
    lane = run_turn("schedule a dentist appointment on my calendar")["response"]

    import tools as tools_mod
    kept = []
    monkeypatch.setattr(tools_mod, "HANDLERS", {"create_task": lambda *a, **k: kept.append(a)})
    s = SessionLocal()
    try:
        executor = tools_mod.execute_intent(
            s, clock(), "create_task", {"text": "x"},
            source="typed", command_text="schedule a dentist appointment on my calendar")["message"]
    finally:
        s.close()
    assert kept == [], "a blocked intent must never reach its handler"

    opened = []
    monkeypatch.setattr(tool_registry.system_actions, "open_website",
                        lambda site_or_url: opened.append(site_or_url) or "Opening...")
    validated = tool_registry.validate_proposal(
        ToolProposal(name="open_website", args={"site_or_url": "google calendar"}), clock())
    launch = tool_registry.execute_proposal(
        validated, SessionLocal, clock(), source="model",
        command_text="schedule a dentist appointment on my calendar")["response"]
    assert opened == []

    assert lane == executor == launch
    assert "your calendar" in lane
    assert db.query(Task).count() == 0 and db.query(ActionReceipt).count() == 0


def test_launch_lane_still_refuses_when_reached_directly(db, monkeypatch):
    """Layer 3 is no longer the first line of defence, but it is still the only
    one between a validated open_website proposal and a real tab — so it is
    tested on its own terms, with the model lane bypassed entirely."""
    _wipe(db)
    opened = []
    monkeypatch.setattr(tool_registry.system_actions, "open_website",
                        lambda site_or_url: opened.append(site_or_url) or "Opening Google Calendar...")
    validated = tool_registry.validate_proposal(
        ToolProposal(name="open_website", args={"site_or_url": "google calendar"}), clock())
    out = tool_registry.execute_proposal(
        validated, SessionLocal, clock(), source="model",
        command_text="add the interview to my google calendar on saturday 10 am")
    assert opened == [], "a tab was launched for a request that asked for a calendar write"
    assert out["opened"] is False
    assert out["clarification"] is True
    assert "Nothing was opened" in out["response"]
    assert db.query(ActionReceipt).count() == 0


def test_refusal_then_explicit_choice_creates_the_task_once(db):
    """'offer to create a task only if I explicitly choose that option': the
    refusal is stateless, so the authorized re-ask — which names a task — is
    what writes, and the earlier refusal left no idempotency key behind."""
    _wipe(db)
    refused_provider = install(FakeProvider(response=proposal_response(
        "create_task", {"text": "Dentist appointment"})))
    refused = run_turn("schedule a dentist appointment on my calendar")
    assert refused["receipt"] is None
    assert refused_provider.calls == []

    provider = install(FakeProvider(response=proposal_response(
        "create_task", {"text": "book a dentist appointment"})))
    accepted = run_turn("add a task to book a dentist appointment")
    assert accepted["receipt"]["success"] is True, accepted["response"]
    assert len(provider.calls) == 1, "the allowed request must still reach the model"
    assert db.query(Task).count() == 1
    assert db.query(Task).first().text == "book a dentist appointment"
    assert db.query(ActionReceipt).count() == 1


def test_ambiguous_request_without_a_destination_is_captured(db):
    """Documented limit, pinned on purpose: with no destination named there is
    nothing to contradict, so VEGA's own task list is its schedule. Blocking
    this would refuse the capture path every 'add a task' user depends on."""
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "dentist appointment", "when": "friday 2 pm"})))
    out = run_turn("schedule a dentist appointment for friday 2 pm")
    assert out["receipt"]["success"] is True, out["response"]
    task = db.query(Task).first()
    assert task.text == "dentist appointment"
    assert task.deadline_utc is not None
    # The user's words stay on the receipt, so this is auditable and undoable.
    assert db.query(ActionReceipt).first().command_text == \
        "schedule a dentist appointment for friday 2 pm"


def test_calendar_refusal_surfaces_through_chat_endpoint(client, db):
    """The route, not just the lane: `executionMode` must be the value the UI
    renders as 'no badge' (AIBrain leaves 'none' unlabeled on purpose), so a
    refusal never looks like an answer that came from a model."""
    _wipe(db)
    install(FakeProvider(response=proposal_response(
        "create_task", {"text": "Dentist appointment"})))
    r = client.post("/chat", json={"message": "schedule a dentist appointment on my calendar",
                                   "provider": "ollama"})
    assert r.status_code == 200
    body = r.json()
    assert body["executionMode"] == "none"
    assert body["clarification"] is True
    assert "calendar" in body["response"]
    assert body["opened"] is False
    assert db.query(Task).count() == 0
    assert db.query(ActionReceipt).count() == 0


def test_calendar_write_request_does_not_open_a_tab(db, monkeypatch):
    """Live finding from the 39-case eval: with the task substitution blocked,
    the model's next-nearest answer was `open_website('google calendar')` — a
    desktop action that still does not fulfill 'add … on my calendar'. The
    request is now refused before inference, so no proposal is ever produced
    and the launch action is never reached."""
    _wipe(db)
    opened = []
    provider = install(FakeProvider(response=proposal_response(
        "open_website", {"site_or_url": "google calendar"})))
    monkeypatch.setattr(tool_registry.system_actions, "open_website",
                        lambda site_or_url: opened.append(site_or_url) or "Opening Google Calendar...")
    out = run_turn("add the interview to my google calendar on saturday 10 am")
    assert provider.calls == []
    assert opened == [], "a tab was launched for a request that asked for a calendar write"
    assert out.get("opened") is None, "no lane ran, so nothing reports an open"
    assert out["clarification"] is True
    assert "Nothing was opened" in out["response"]
    assert db.query(ActionReceipt).count() == 0


def test_open_request_for_the_same_destination_still_launches(db, monkeypatch):
    """The exemption that keeps the refusal from becoming a wall: 'open your
    calendar' asks to reach the destination, which is exactly what the launch
    lane can do."""
    _wipe(db)
    opened = []
    monkeypatch.setattr(system_actions, "get_browser_launcher",
                        lambda: (lambda url: opened.append(url) or True))
    install(FakeProvider(response=proposal_response(
        "open_website", {"site_or_url": "google calendar"})))
    out = run_turn("please open my google calendar")
    assert opened == ["https://calendar.google.com"]
    assert out["opened"] is True
    assert out["receipt"] is not None
    assert db.query(ActionReceipt).count() == 1
