"""Tests for durable workspaces, 'Resume my work', and session closure.
Uses temporary SQLite databases only — user data is never touched. Covers:
create project workspace + next action + session note, backend restart persistence
(new engine/session on the same file), resume via typed command and read path,
exact stored state verification, replay deduplication, ambiguous project clarification
with zero writes, and verifying edited session draft persistence.
"""

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base, Task, Workspace, SessionNote, ActionReceipt
import migrations
import tools
import dispatcher
import timeutil


# ── executor-level behaviours (temp `db`/`clock` fixtures) ──────────

def test_register_resume_and_note_roundtrip(db, clock):
    reg = tools.execute_intent(db, clock, "register_workspace",
                               {"name": "Thesis", "type": "academic",
                                "next_action": "write the intro"}, source="ui")
    assert reg["success"] and reg["entity_type"] == "workspace"
    ws_id = reg["entity_id"]

    note = tools.execute_intent(db, clock, "add_session_note",
                                {"workspace_id": ws_id, "outcome": "finished lit review",
                                 "next_action": "draft methodology"}, source="ui")
    assert note["success"] and note["entity_type"] == "session_note"

    res = tools.execute_intent(db, clock, "resume_workspace",
                               {"workspace_id": ws_id}, source="ui")
    msg = res["message"]
    assert res["read_only"] is True
    assert "draft methodology" in msg           # next action refreshed from the note
    assert "finished lit review" in msg          # last session note surfaced


def test_register_validates_path_and_never_scans_drives(db, clock):
    bad = tools.execute_intent(db, clock, "register_workspace",
                               {"name": "Ghost", "path": "Z:\\definitely\\not\\here"},
                               source="ui")
    assert bad["success"] is False and "doesn't exist" in bad["message"]
    # a rejected write stored a failure receipt but created no workspace row
    assert db.query(Workspace).filter(Workspace.name == "Ghost").count() == 0


def test_ambiguous_resume_clarifies_and_writes_nothing(db, clock):
    for name in ("Alpha One", "Alpha Two"):
        assert tools.execute_intent(db, clock, "register_workspace",
                                    {"name": name}, source="ui")["success"]
    before_ws = db.query(Workspace).count()
    before_receipts = db.query(ActionReceipt).count()

    res = tools.execute_intent(db, clock, "resume_workspace", {"name": "alpha"},
                               source="ui")
    assert res["clarification"] is True and res["success"] is False
    db.expire_all()
    # an ambiguous read must not create a receipt or mutate the workspace set
    assert db.query(Workspace).count() == before_ws
    assert db.query(ActionReceipt).count() == before_receipts


def test_link_task_only_when_explicit_and_preserves_task(db, clock):
    reg = tools.execute_intent(db, clock, "register_workspace", {"name": "Portal"}, source="ui")
    ws_id = reg["entity_id"]
    task = Task(text="apply for the grant", completed=False, source="ui",
                created_at=clock.now_utc(), updated_at=clock.now_utc())
    db.add(task); db.commit(); db.refresh(task)

    link = tools.execute_intent(db, clock, "link_task_to_workspace",
                                {"task_id": task.id, "workspace_id": ws_id}, source="ui")
    assert link["success"]
    db.expire_all()
    t = db.get(Task, task.id)
    assert t.workspace_id == ws_id and t.text == "apply for the grant"  # other fields preserved


def test_resume_default_targets_latest_active(db, clock):
    tools.execute_intent(db, clock, "register_workspace", {"name": "Old"}, source="ui")
    clock.advance(minutes=5)
    new = tools.execute_intent(db, clock, "register_workspace", {"name": "Newest"}, source="ui")
    res = tools.execute_intent(db, clock, "resume_workspace", {}, source="ui")
    assert res["success"] and res["entity_id"] == new["entity_id"]


def test_session_draft_is_read_only_and_never_fabricates(db, clock):
    empty = tools.execute_intent(db, clock, "build_session_draft", {}, source="ui")
    assert empty["read_only"] is True
    assert "No completed work" in empty["draft"]["outcome"]
    # a draft produces no entities and no receipt
    assert db.query(SessionNote).count() == 0
    assert db.query(ActionReceipt).count() == 0


def test_typed_command_path_registers_and_resumes_offline(db, clock):
    r1 = dispatcher.dispatch_command(db, clock,
                                     "register a project called Thesis (academic) with next action write intro",
                                     source="typed")
    assert r1["handled"] and "Registered" in r1["response"]
    assert r1.get("executionMode") in ("deterministic", None)
    r2 = dispatcher.dispatch_command(db, clock, "resume thesis", source="typed")
    assert r2["handled"] and "Resuming 'Thesis'" in r2["response"]


# ── restart persistence + replay idempotency on a throwaway DB file ──

def test_state_survives_restart_and_replay_does_not_duplicate(tmp_path):
    db_file = str(tmp_path / "p2.db")

    def open_session():
        eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        migrations.run_migrations(eng, Base, db_file)
        return eng, sessionmaker(bind=eng)()

    clock = timeutil.FakeClock(datetime(2026, 9, 21, 4, 30, 0))

    # --- "process 1": register + note + next action, then shut down ---
    eng, db = open_session()
    reg = tools.execute_intent(db, clock, "register_workspace",
                               {"name": "Grant Renewal", "type": "academic",
                                "next_action": "gather transcripts"}, source="ui")
    ws_id = reg["entity_id"]
    tools.execute_intent(db, clock, "add_session_note",
                         {"workspace_id": ws_id, "outcome": "collected forms",
                          "next_action": "submit draft"}, source="ui",
                         idempotency_key="note-1")
    stored_ws = db.get(Workspace, ws_id).next_action
    stored_note_id = db.query(SessionNote).first().id
    db.close(); eng.dispose()

    # --- "restart": fresh engine/session over the SAME file, nothing reseeded ---
    eng2, db2 = open_session()
    assert db2.query(Workspace).count() == 1
    assert db2.get(Workspace, ws_id).next_action == "submit draft"   # persisted note update
    assert db2.query(SessionNote).count() == 1

    # replay the SAME idempotency key on the new session -> no second note row
    tools.execute_intent(db2, clock, "add_session_note",
                         {"workspace_id": ws_id, "outcome": "collected forms",
                          "next_action": "submit draft"}, source="ui",
                         idempotency_key="note-1")
    assert db2.query(SessionNote).count() == 1

    # resume from the fresh session reads back the exact stored state
    res = tools.execute_intent(db2, clock, "resume_workspace", {"workspace_id": ws_id}, source="ui")
    assert "submit draft" in res["message"] and "collected forms" in res["message"]
    db2.close(); eng2.dispose()


# ── full REST vertical slice (the HTTP layer the UI actually drives) ──

def test_resume_then_session_note_rest_flow(client):
    """Register -> resume -> end-session draft -> save the note SCOPED to the
    resumed project (the exact UI flow), then prove replay does not duplicate and
    a re-resume surfaces the note. Guards the contract the dashboard consumes."""
    reg = client.post("/api/workspaces", json={
        "name": "Scholarship", "type": "academic", "next_action": "draft essay"})
    assert reg.status_code == 200, reg.text
    ws_id = reg.json()["receipt"]["entity_id"]

    assert client.post(f"/api/workspaces/{ws_id}/resume").status_code == 200
    draft = client.get("/api/session/draft")
    assert draft.status_code == 200
    assert "No completed work" in draft.json()["draft"]["outcome"]  # non-fabricating

    body = {"outcome": "drafted intro", "next_action": "add achievements",
            "workspace_id": ws_id, "idempotency_key": "ui-note-1"}
    save = client.post("/api/session/notes", json=body)
    assert save.status_code == 200, save.text
    assert save.json()["note"]["workspace_id"] == ws_id          # note is scoped, not orphaned

    # replay the same idempotency key -> no second note row on the project
    client.post("/api/session/notes", json=body)
    notes = client.get(f"/api/workspaces/{ws_id}/notes").json()
    assert len(notes) == 1 and notes[0]["outcome"] == "drafted intro"

    # re-resume surfaces the last note (the durable-context loop closes)
    res = client.post(f"/api/workspaces/{ws_id}/resume").json()
    assert res["receipt"]["read_only"] is True
    assert "Last session note" in res["receipt"]["message"]
    assert res["workspace"]["next_action"] == "add achievements"


def test_edited_draft_wins_over_generated_text_on_save(client, db):
    """The saved note must carry the USER's words, not the draft it was edited from.

    The gate says "let me edit or confirm the note before saving", and until now the
    only proof was JSX reading `value={draft.outcome}`. The other REST test posts
    free-form text that was never derived from anything, so a save handler that
    re-derived the outcome instead of storing the submitted one would pass it.
    Real HTTP path end to end, with a genuinely non-empty generated draft.
    """
    ws_id = client.post("/api/workspaces", json={
        "name": "Renewal", "type": "academic",
        "next_action": "fill renewal form"}).json()["receipt"]["entity_id"]

    created = client.post("/api/tasks", json={"text": "fill renewal form"}).json()
    assert client.post(f"/api/tasks/{created['id']}/complete",
                       json={"completed": True}).status_code == 200

    draft = client.get("/api/session/draft").json()["draft"]
    assert "fill renewal form" in draft["outcome"], (
        f"precondition failed - draft should derive from real activity: {draft['outcome']!r}")
    assert draft["blocker"] == ""

    save = client.post("/api/session/notes", json={
        "workspace_id": ws_id,
        "outcome": "Wrote the essay, not the form",
        "blocker": "waiting on the transcript email",
        "next_action": "chase the registrar",
        "idempotency_key": "edited-note-1"})
    assert save.status_code == 200, save.text

    notes = client.get(f"/api/workspaces/{ws_id}/notes").json()
    assert len(notes) == 1
    stored = notes[0]
    assert stored["outcome"] == "Wrote the essay, not the form"
    assert "fill renewal form" not in stored["outcome"], "generated text overwrote the edit"
    assert stored["blocker"] == "waiting on the transcript email"
    assert stored["next_action"] == "chase the registrar"
    assert stored["created_utc"], "a note without a timestamp is not durable context"

    # the edit also propagates to the project, exactly like a generated note would
    assert db.get(Workspace, ws_id).next_action == "chase the registrar"
