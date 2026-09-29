"""HTTP API contract tests via FastAPI TestClient.

The client fixture boots the real app (lifespan runs: scheduler starts, voice
disabled). These tests assert endpoint shapes, status codes, and that typed
commands work offline through the same dispatcher as /chat. Times use the real
system clock, so we assert relationships (future due, status transitions)
rather than exact timestamps.
"""

from datetime import datetime, timezone, timedelta


def _wipe(db):
    from db import Task, Note, Timer, Reminder, FocusSession, ScheduledAlert, ActionReceipt
    for model in (ActionReceipt, ScheduledAlert, FocusSession, Reminder, Timer, Note, Task):
        db.query(model).delete()
    db.commit()


def _future_iso(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _past_iso(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


# ── assistant command endpoint ──────────────────────────────────────────

def test_assistant_command_creates_task(client, db):
    _wipe(db)
    r = client.post("/api/assistant/command",
                    json={"message": "add a task: finish the DBMS assignment"})
    assert r.status_code == 200
    body = r.json()
    assert body["handled"] is True
    assert body["receipt"]["success"] is True
    assert body["receipt"]["entity_type"] == "task"


def test_assistant_command_passthrough_for_chat(client, db):
    _wipe(db)
    r = client.post("/api/assistant/command", json={"message": "what is the meaning of life"})
    assert r.status_code == 200
    assert r.json()["handled"] is False


def test_chat_offline_command_path(client, db):
    """/chat must run the dispatcher before any provider call, so a task
    command succeeds offline with no GEMINI_API_KEY / Ollama."""
    _wipe(db)
    r = client.post("/chat", json={"message": "set a timer for 25 minutes"})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["receipt"]["success"] is True
    assert body["receipt"]["action"] == "start_timer"


def test_chat_idempotency_key_dedupes(client, db):
    _wipe(db)
    payload = {"message": "add a task: idempotent thing", "idempotencyKey": "key-abc-123"}
    r1 = client.post("/chat", json=payload)
    r2 = client.post("/chat", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["receipt"]["entity_id"] == r2.json()["receipt"]["entity_id"]
    assert r2.json()["receipt"]["replayed"] is True
    tasks = client.get("/api/tasks").json()
    assert len([t for t in tasks if t["text"] == "idempotent thing"]) == 1


def test_chat_voice_source_routed_offline(client, db):
    """M2a: a wake-word transcript (which often carries the spoken wake word)
    posted with source='voice' must be executed by the deterministic offline
    dispatcher and its receipt must be attributed to 'voice'."""
    _wipe(db)
    r = client.post("/chat", json={"message": "Hey Jarvis, set a timer for 5 minutes",
                                   "source": "voice"})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["receipt"]["action"] == "start_timer"
    assert body["receipt"]["source"] == "voice"


def test_chat_source_sanitized_to_chat(client, db):
    _wipe(db)
    r = client.post("/chat", json={"message": "set a timer for 6 minutes",
                                   "source": "not-voice"})
    assert r.status_code == 200
    assert r.json()["receipt"]["source"] == "chat"


# ── tasks CRUD ──────────────────────────────────────────────────────────

def test_task_crud_flow(client, db):
    _wipe(db)
    created = client.post("/api/tasks", json={"text": "write tests"}).json()
    assert created["id"] is not None
    assert created["completed"] is False
    assert created["source"] == "ui"

    listed = client.get("/api/tasks").json()
    assert any(t["id"] == created["id"] for t in listed)

    done = client.post(f"/api/tasks/{created['id']}/complete", json={"completed": True})
    assert done.status_code == 200
    assert done.json()["receipt"]["success"] is True

    after = client.get("/api/tasks").json()
    row = next(t for t in after if t["id"] == created["id"])
    assert row["completed"] is True

    deleted = client.delete(f"/api/tasks/{created['id']}")
    assert deleted.status_code == 200
    assert all(t["id"] != created["id"] for t in client.get("/api/tasks").json())


def test_task_create_with_deadline(client, db):
    _wipe(db)
    due = _future_iso(48)
    created = client.post("/api/tasks",
                          json={"text": "submit report", "deadline_utc": due}).json()
    assert created["deadline_utc"] is not None
    assert created["deadline_utc"].endswith("Z")


def test_task_create_bad_deadline_400(client, db):
    _wipe(db)
    r = client.post("/api/tasks", json={"text": "x", "deadline_utc": "not-a-date"})
    assert r.status_code == 400


def test_complete_missing_task_404(client, db):
    _wipe(db)
    r = client.post("/api/tasks/999999/complete", json={"completed": True})
    assert r.status_code == 404


# ── timers ──────────────────────────────────────────────────────────────

def test_timer_lifecycle(client, db):
    _wipe(db)
    created = client.post("/api/timers", json={"duration_seconds": 600, "label": "eggs"})
    assert created.status_code == 200
    timer = created.json()["timer"]
    assert timer["status"] == "active"
    assert timer["end_utc"].endswith("Z")

    active = client.get("/api/timers").json()
    assert any(t["id"] == timer["id"] for t in active)

    cancelled = client.delete(f"/api/timers/{timer['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["receipt"]["success"] is True

    still_active = client.get("/api/timers").json()
    assert all(t["id"] != timer["id"] for t in still_active)


def test_timer_rejects_bad_duration(client, db):
    _wipe(db)
    assert client.post("/api/timers", json={"duration_seconds": 0}).status_code == 422
    assert client.post("/api/timers", json={"duration_seconds": 999999}).status_code == 422


def test_cancel_absent_timer_404(client, db):
    _wipe(db)
    assert client.delete("/api/timers/999999").status_code == 404


# ── reminders ───────────────────────────────────────────────────────────

def test_reminder_create_and_snooze(client, db):
    _wipe(db)
    created = client.post("/api/reminders",
                          json={"text": "revise trees", "due_utc": _future_iso(2)})
    assert created.status_code == 200
    rem = created.json()["reminder"]
    assert rem["status"] == "pending"
    original_due = rem["due_utc"]

    snoozed = client.post(f"/api/reminders/{rem['id']}/snooze", json={"minutes": 10})
    assert snoozed.status_code == 200
    assert snoozed.json()["receipt"]["success"] is True

    pending = client.get("/api/reminders").json()
    updated = next(r for r in pending if r["id"] == rem["id"])
    assert updated["snooze_count"] == 1
    # A still-pending reminder postpones from its due time -> strictly later.
    assert updated["due_utc"] > original_due


def test_reminder_past_due_clarifies_409(client, db):
    _wipe(db)
    r = client.post("/api/reminders",
                    json={"text": "too late", "due_utc": _past_iso(2)})
    assert r.status_code == 409


def test_reminder_bad_due_400(client, db):
    _wipe(db)
    r = client.post("/api/reminders", json={"text": "x", "due_utc": "nope"})
    assert r.status_code == 400


# ── focus sessions ──────────────────────────────────────────────────────

def test_focus_start_end_flow(client, db):
    _wipe(db)
    started = client.post("/api/focus/start",
                          json={"duration_seconds": 2700, "objective": "DSA"})
    assert started.status_code == 200
    session = started.json()["session"]
    assert session["status"] == "active"

    # A second concurrent session is refused.
    again = client.post("/api/focus/start", json={"duration_seconds": 1500})
    assert again.status_code == 400

    state = client.get("/api/focus").json()
    assert state["active"] is not None
    assert state["active"]["id"] == session["id"]

    ended = client.post("/api/focus/end", json={"note": "finished recursion"})
    assert ended.status_code == 200
    assert ended.json()["receipt"]["success"] is True

    after = client.get("/api/focus").json()
    assert after["active"] is None
    assert any(s["id"] == session["id"] and s["status"] == "completed"
               for s in after["recent"])


def test_focus_end_without_active_409(client, db):
    _wipe(db)
    r = client.post("/api/focus/end", json={"note": "nothing running"})
    assert r.status_code == 409


# ── aggregated hub + receipts + alerts ──────────────────────────────────

def test_hub_state_shape(client, db):
    _wipe(db)
    client.post("/api/tasks", json={"text": "hub task"})
    client.post("/api/timers", json={"duration_seconds": 300, "label": "tea"})
    state = client.get("/api/hub/state").json()
    for key in ("now_utc", "timezone", "open_tasks", "active_timers",
                "next_reminder", "active_focus", "missed_alerts", "recent_alerts"):
        assert key in state
    assert any(t["text"] == "hub task" for t in state["open_tasks"])
    assert len(state["active_timers"]) == 1


def test_receipts_recorded(client, db):
    _wipe(db)
    client.post("/api/timers", json={"duration_seconds": 120, "label": "log"})
    receipts = client.get("/api/receipts").json()
    assert isinstance(receipts, list)
    assert any(r["action"] == "start_timer" for r in receipts)


def test_alerts_endpoint_lists_pending(client, db):
    _wipe(db)
    client.post("/api/timers", json={"duration_seconds": 3600, "label": "long"})
    alerts = client.get("/api/alerts").json()
    assert isinstance(alerts, list)
    assert any(a["kind"] == "timer_due" and a["status"] == "pending" for a in alerts)


def _delivered_alert(db, message, hours_ago, status="delivered"):
    from db import ScheduledAlert
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(ScheduledAlert(kind="timer_due", entity_type="timer", entity_id=1,
                          due_utc=now - timedelta(hours=hours_ago),
                          status=status, message=message,
                          delivered_at=now - timedelta(hours=hours_ago)))
    db.commit()


def test_hub_state_resurfaces_recently_delivered_alerts(client, db):
    """'delivered' only means a socket accepted the payload. Measured on
    2026-09-22: with the overlay hidden the renderer turns it into an invisible
    in-app toast and no OS notification appears, so the alert would otherwise
    vanish. Anything delivered inside the window must be listed for the next
    time the hub is opened; older deliveries age out."""
    _wipe(db)
    _delivered_alert(db, "Timer finished: 1 minute", 0.2)
    _delivered_alert(db, "Reminder: submit lab record", 8)
    _delivered_alert(db, "Deadline: DBMS assignment", 1, status="missed")

    state = client.get("/api/hub/state").json()
    recent = [a["message"] for a in state["recent_alerts"]]
    assert recent == ["Timer finished: 1 minute"]
    assert [a["message"] for a in state["missed_alerts"]] == ["Deadline: DBMS assignment"]


# ── Phase 2: local task answers (offline, deterministic) ─────────────────

def test_chat_answers_pending_tasks_offline(client, db):
    """"What are my pending tasks?" must be answered by the dispatcher reading
    SQLite — incomplete tasks listed, completed ones excluded, no provider."""
    _wipe(db)
    client.post("/api/tasks", json={"text": "write the report"})
    done = client.post("/api/tasks", json={"text": "file the taxes"}).json()
    client.post(f"/api/tasks/{done['id']}/complete", json={"completed": True})

    r = client.post("/chat", json={"message": "What are my pending tasks?"})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    text = body["response"]
    assert "write the report" in text
    assert "file the taxes" not in text  # completed tasks are never reported


def test_chat_pending_tasks_includes_deadline(client, db):
    _wipe(db)
    client.post("/api/tasks", json={"text": "submit assignment",
                                    "deadline_utc": _future_iso(hours=6)})
    r = client.post("/chat", json={"message": "show my tasks"})
    assert r.status_code == 200
    text = r.json()["response"]
    assert "submit assignment" in text
    assert "due" in text.lower()  # deadline rendered when present


def test_readonly_task_tool_reports_incomplete_only(client, db, clock):
    """The read-only list_tasks tool (reached by models through the P1 registry)
    binds to the same executor, reports only incomplete tasks, and writes no
    action receipt (strictly read-only)."""
    import tools
    _wipe(db)
    client.post("/api/tasks", json={"text": "revise trees"})
    done = client.post("/api/tasks", json={"text": "buy groceries"}).json()
    client.post(f"/api/tasks/{done['id']}/complete", json={"completed": True})

    db.expire_all()
    receipts_before = len(client.get("/api/receipts").json())
    result = tools.execute_intent(db, clock, "list_tasks", {"window": "all"}, source="model")
    receipts_after = len(client.get("/api/receipts").json())

    assert result["read_only"] is True
    assert "revise trees" in result["message"]
    assert "buy groceries" not in result["message"]
    assert receipts_after == receipts_before  # no mutation, no receipt


def test_readonly_task_tool_bad_window_falls_back(client, db, clock):
    import tools
    _wipe(db)
    client.post("/api/tasks", json={"text": "plan the week"})
    db.expire_all()
    # An invalid window must not error at the executor — it degrades to "all".
    # (The registry schema additionally rejects bad windows at the model
    # boundary, so a model can never send one.)
    result = tools.execute_intent(db, clock, "list_tasks", {"window": "nonsense"}, source="model")
    assert "plan the week" in result["message"]


def test_chat_question_not_misrouted_to_tasks(client, db, monkeypatch):
    """A general chat question must not be captured by the task parser: it falls
    through to the LLM lane, and that lane must fail gracefully when unavailable.

    Pinned to a refused Ollama port. Two reasons, both measured:
    1. Unpinned it took the live path: 12.4 s, and it loaded phi4-mini into
       memory (3.64 GB, 2.32 GB VRAM) on every full-suite run.
    2. The old assertion was `"error" in body or "task" not in response`. The
       offline body has no `error` key, and its recovery hint says "Your tasks,
       timers, and reminders still work offline" so it contains "task" too
       meaning the test FAILED whenever Ollama was actually down, blaming the
       app for a missing local service. Asserting structure fixes both.
    """
    from db import Task
    from model_lane import reset_providers
    _wipe(db)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    reset_providers()
    try:
        r = client.post("/chat", json={"message": "what is the capital of France"})
        assert r.status_code == 200
        body = r.json()
        assert body["receipt"] is None, f"chat must not execute anything: {body}"
        assert body["executionMode"] == "none"
        assert body["opened"] is False
        assert db.query(Task).count() == 0, "a chat question must never create a task"
        assert "unavailable" in body["response"].lower(), body["response"]
    finally:
        reset_providers()
