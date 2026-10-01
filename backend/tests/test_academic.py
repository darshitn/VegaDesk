"""Tests for academic coursework and study suggestion loop.

Covers: logging coursework (subject/kind/due/effort) offline via the typed
command path; timezone + past/ambiguous due-date validation that writes NOTHING;
the "I have N minutes" suggestion using due date + the user's own effort estimate
with an editable alternative list; the daily briefing surfacing a due-soon item
with a reason; completion; subject linkage (and the no-fabrication / ambiguity
guards); and RESTART persistence. Temp/copied DBs only — user data is untouched.
"""

from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import dispatcher
import migrations
import timeutil
import tools
from db import Base, Coursework, Workspace


def _cw(db, clock, **kw):
    """Insert a coursework row directly (bypasses parsing) for suggestion tests."""
    now = clock.now_utc()
    c = Coursework(title=kw["title"], kind=kw.get("kind", "assignment"),
                   workspace_id=kw.get("workspace_id"),
                   due_utc=now + timeutil.timedelta(days=kw["due_in_days"]) if "due_in_days" in kw else None,
                   effort_minutes=kw.get("effort"), completed=kw.get("completed", False),
                   source="test", created_utc=now, updated_utc=now)
    db.add(c)
    db.commit()
    return c


# ── offline typed-command path ──────────────────────────────────────

def test_add_coursework_offline_via_typed_command(db, clock):
    # dispatch_command only consults the deterministic parser, so a handled
    # result here proves the flow works with no model (Ollama/Gemini offline).
    r = dispatcher.dispatch_command(
        db, clock, "add assignment calculus problem set due friday about 90 minutes",
        source="typed")
    assert r["handled"] and not r["clarification"]
    assert r["receipt"]["action"] == "add_coursework"
    cw = db.query(Coursework).one()
    assert cw.kind == "assignment"
    assert cw.effort_minutes == 90
    assert cw.due_utc is not None and cw.due_utc > clock.now_utc()
    assert "problem set" in cw.title.lower()


def test_word_number_effort_is_parsed(db, clock):
    r = dispatcher.dispatch_command(
        db, clock, "add exam midterm review due next wednesday about forty five minutes",
        source="typed")
    assert r["handled"] and r["receipt"]["action"] == "add_coursework"
    cw = db.query(Coursework).one()
    assert cw.kind == "exam"                                  # test/quiz normalise to exam
    assert cw.effort_minutes == 45


# ── date validation: past / unparseable write NOTHING ───────────────

def test_past_due_rejected_and_writes_nothing(db, clock):
    before = db.query(Coursework).count()
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "late thing", "due": "yesterday"}, source="ui")
    assert res["success"] is False
    db.expire_all()
    assert db.query(Coursework).count() == before             # rejected write, no row


def test_unparseable_due_rejected(db, clock):
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "mystery", "due": "sometime blahblah"}, source="ui")
    assert res["success"] is False


def test_due_is_optional(db, clock):
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "no-deadline reading", "kind": "reading"},
                               source="ui")
    assert res["success"]
    cw = db.get(Coursework, res["entity_id"])
    assert cw.due_utc is None and cw.kind == "reading"


# ── "I have N minutes" suggestion (due date + user's own estimate) ───

def test_suggest_prefers_a_task_that_fits_the_window(db, clock):
    _cw(db, clock, title="quick warmup", due_in_days=5, effort=20)      # fits 25, but later
    _cw(db, clock, title="big essay", due_in_days=1, effort=180)         # soonest, doesn't fit
    res = tools.execute_intent(db, clock, "suggest_study", {"minutes": 25}, source="ui")
    assert res["read_only"] is True
    sugg = res["items"]["suggestion"]
    assert sugg["title"] == "quick warmup"                # prefers completable-now
    assert "fits" in sugg["fit"]
    assert res["items"]["alternatives"]                   # editable choice present


def test_suggest_falls_back_to_due_soonest_when_nothing_fits(db, clock):
    _cw(db, clock, title="later project", due_in_days=6, effort=200)
    _cw(db, clock, title="urgent exam", due_in_days=1, effort=120)
    res = tools.execute_intent(db, clock, "suggest_study", {"minutes": 25}, source="ui")
    sugg = res["items"]["suggestion"]
    assert sugg["title"] == "urgent exam"                 # nothing fits → due-soonest
    assert "bigger than" in sugg["fit"]
    assert "due tomorrow" in sugg["why_due"]


def test_suggest_offline_via_typed_command(db, clock):
    _cw(db, clock, title="read ch5", due_in_days=2, effort=15)
    r = dispatcher.dispatch_command(db, clock, "I have 25 minutes", source="typed")
    # dispatch_command only consults the deterministic parser → offline proof.
    assert r["handled"] and r["receipt"]["action"] == "suggest_study"
    assert "read ch5" in r["response"]


def test_suggest_empty_is_a_clean_read(db, clock):
    res = tools.execute_intent(db, clock, "suggest_study", {"minutes": 30}, source="ui")
    assert res["success"] and res["items"]["suggestion"] is None


# ── daily briefing explains why an item is due soon ─────────────────

def test_get_today_surfaces_due_soon_coursework_with_reason(db, clock):
    _cw(db, clock, title="statistics problem set", due_in_days=1, effort=60)
    res = tools.execute_intent(db, clock, "get_today", {}, source="ui")
    assert res["items"]["coursework"]
    top = res["items"]["coursework"][0]
    assert top["title"] == "statistics problem set"
    assert "tomorrow" in top["why"]
    assert "Tackle" in res["message"] and "statistics problem set" in res["message"]


# ── completion + linkage guards ─────────────────────────────────────

def test_complete_coursework_and_reopen_guards(db, clock):
    c = _cw(db, clock, title="finish lab", due_in_days=3, effort=40)
    res = tools.execute_intent(db, clock, "complete_coursework",
                               {"coursework_id": c.id}, source="ui")
    assert res["success"]
    db.expire_all()
    assert db.get(Coursework, c.id).completed is True
    # unknown id is rejected, never guessed
    bad = tools.execute_intent(db, clock, "complete_coursework",
                               {"coursework_id": 9999}, source="ui")
    assert bad["success"] is False


def test_coursework_links_to_registered_subject(db, clock):
    reg = tools.execute_intent(db, clock, "register_workspace",
                               {"name": "Thesis", "type": "academic"}, source="ui")
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "chapter 4 draft", "subject": "thesis"},
                               source="ui")
    assert res["success"]
    cw = db.get(Coursework, res["entity_id"])
    assert cw.workspace_id == reg["entity_id"]


def test_unknown_subject_does_not_create_a_workspace(db, clock):
    before_ws = db.query(Workspace).count()
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "orphan", "subject": "Never Registered"},
                               source="ui")
    assert res["success"] is False
    db.expire_all()
    assert db.query(Workspace).count() == before_ws         # no fabricated project


def test_ambiguous_subject_clarifies_and_writes_nothing(db, clock):
    for n in ("Biology 101", "Biology 201"):
        tools.execute_intent(db, clock, "register_workspace",
                             {"name": n, "type": "academic"}, source="ui")
    before = db.query(Coursework).count()
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "worksheet", "subject": "biology"}, source="ui")
    assert res["clarification"] is True
    db.expire_all()
    assert db.query(Coursework).count() == before


# ── restart persistence over a throwaway DB file ────────────────────

def test_coursework_survives_restart(tmp_path):
    db_file = str(tmp_path / "p3.db")

    def open_session():
        eng = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
        migrations.run_migrations(eng, Base, db_file)
        return eng, sessionmaker(bind=eng)()

    clock = timeutil.FakeClock(datetime(2026, 9, 21, 4, 30, 0))
    eng, db = open_session()
    res = tools.execute_intent(db, clock, "add_coursework",
                               {"title": "dissertation chapter", "kind": "project",
                                "due": "next friday", "effort_text": "two hours"},
                               source="ui")
    cid = res["entity_id"]
    db.close(); eng.dispose()

    eng2, db2 = open_session()
    cw = db2.get(Coursework, cid)
    assert cw is not None and cw.title == "dissertation chapter"
    assert cw.effort_minutes == 120                          # "two hours" persisted
    tools.execute_intent(db2, clock, "complete_coursework", {"coursework_id": cid}, source="ui")
    db2.close(); eng2.dispose()

    eng3, db3 = open_session()
    assert db3.get(Coursework, cid).completed is True
    db3.close(); eng3.dispose()
