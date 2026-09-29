"""Stage 1 (overnight 5h run): close the two measured P1 gaps.

Covers, all offline against the temp DB:
  * bounded English word-number durations (the 'twenty minutes' miss), on the
    shared parse_duration_seconds used by BOTH the deterministic parser and the
    model 'duration_text' lane;
  * deterministic completion routing (the misroute-to-list_tasks miss) incl. the
    exact compound phrase from the live eval;
  * the safety rule that a read is never turned into an unintended write: an
    ambiguous 'similarly named pair' clarifies and mutates nothing.
"""

import pytest

import command_parser
import timeutil
import tools
from timeutil import parse_duration_seconds

from db import Task, ActionReceipt


# ── word-number durations ──────────────────────────────────────
@pytest.mark.parametrize("text,seconds", [
    ("twenty minutes", 1200),
    ("an hour", 3600),
    ("a minute", 60),
    ("two hours", 7200),
    ("half an hour", 1800),
    ("half hour", 1800),
    ("an hour and a half", 5400),
    ("forty five minutes", 2700),
    ("twenty-five minutes", 1500),
    ("thirty minutes", 1800),
    ("ninety seconds", 90),
    ("one hour and thirty minutes", 5400),
    # digit forms must be unchanged
    ("25 minutes", 1500),
    ("1.5 hours", 5400),
    ("45-minute", 2700),
])
def test_word_number_durations(text, seconds):
    assert parse_duration_seconds(text) == seconds


@pytest.mark.parametrize("text", ["a few minutes", "later", "soon", "", "the thesis"])
def test_non_durations_stay_none(text):
    assert parse_duration_seconds(text) is None


def test_timer_word_duration_via_parser(clock):
    r = command_parser.parse_command("set a timer for twenty minutes", clock)
    assert r["intent"] == "start_timer" and r["params"]["duration_seconds"] == 1200


def test_focus_word_duration_via_parser(clock):
    # The 'focus for <dur> on <obj>' form extracts the duration; word-numbers
    # flow through the same shared parse_duration_seconds as the timer lane.
    r = command_parser.parse_command("focus for forty five minutes on thesis", clock)
    assert r["intent"] == "start_focus_session" and r["params"]["duration_seconds"] == 2700
    assert r["params"]["objective"] == "thesis"


# ── deterministic completion routing ───────────────────────────
@pytest.mark.parametrize("text,target", [
    ("complete the scholarship task", "scholarship"),
    ("finish the thesis task", "thesis"),
    ("mark the lab record done", "lab record"),
    ("I wrapped up the scholarship thing, mark it as done", "scholarship thing"),
    ("I finished the report", "report"),
    ("data check is done", "data check"),
])
def test_completion_phrases_route_offline(clock, text, target):
    r = command_parser.parse_command(text, clock)
    assert r and r["type"] == "intent" and r["intent"] == "set_task_completed"
    assert r["params"].get("task_text") == target


@pytest.mark.parametrize("text", [
    "finish the assignment by Friday at 6 PM",   # creation-style, must NOT be a completion
    "add a task: finish the report by friday",   # create
    "what tasks do I have",                       # list
])
def test_completion_patterns_do_not_hijack_other_intents(clock, text):
    r = command_parser.parse_command(text, clock)
    assert not (r and r.get("intent") == "set_task_completed")


# ── the safety rule: never turn a read into an unintended write ─
def _seed(db, *texts):
    now = timeutil.datetime(2026, 9, 21, 4, 30, 0)
    for t in texts:
        db.add(Task(text=t, completed=False, source="ui", created_at=now, updated_at=now))
    db.commit()


def test_ambiguous_similar_pair_clarifies_and_writes_nothing(db, clock):
    _seed(db, "scholarship application", "scholarship renewal")
    res = tools.execute_intent(db, clock, "set_task_completed",
                               {"task_text": "scholarship", "completed": True}, source="model")
    assert res["clarification"] is True and res["success"] is False
    db.expire_all()
    # both still open, no mutation, and a failed/clarify action wrote no receipt row
    assert all(not t.completed for t in db.query(Task).all())
    assert db.query(ActionReceipt).count() == 0


def test_unique_partial_match_completes_exactly_one(db, clock):
    _seed(db, "scholarship application", "data check")
    res = tools.execute_intent(db, clock, "set_task_completed",
                               {"task_text": "renewal", "completed": True}, source="model")
    assert res["success"] is False  # no open task matches 'renewal'
    db.expire_all()
    assert all(not t.completed for t in db.query(Task).all())

    res2 = tools.execute_intent(db, clock, "set_task_completed",
                                {"task_text": "application", "completed": True}, source="model")
    assert res2["success"] is True
    db.expire_all()
    done = [t.text for t in db.query(Task).filter(Task.completed.is_(True)).all()]
    assert done == ["scholarship application"]


# ── follow-up: the two phrasings the live eval showed the model getting wrong ──
# Eval found 'start a pomodoro of twenty five minutes' built a plain Timer (wrong
# entity) and 'the shopping errand is done, tick it off' misrouted to read-only
# list_tasks. Both are now handled offline so they never depend on model quality.
def test_pomodoro_phrasing_starts_focus_offline(clock):
    r = command_parser.parse_command("start a pomodoro of twenty five minutes", clock)
    assert r["intent"] == "start_focus_session"
    assert r["params"]["duration_seconds"] == 1500  # a real focus session, not a timer
    r2 = command_parser.parse_command("do a pomodoro", clock)
    assert r2["intent"] == "start_focus_session" and r2["params"]["duration_seconds"] == 1500


def test_done_clause_completes_offline_not_read(clock):
    r = command_parser.parse_command("the shopping errand is done, tick it off", clock)
    assert r["intent"] == "set_task_completed"
    assert r["params"]["task_text"] == "the shopping errand"
    # creation-style phrasing must NOT be captured as a completion
    assert command_parser.parse_command("finish the assignment by friday", clock) is None


# ── follow-up: the exact phrasing tool_registry's own rejection hint prints
# ('…by friday 6 pm') silently lost the time: the bare '6 pm' matched no when-
# branch, so the deadline fell back to end-of-day AND '6 pm' stayed in the title.
def test_bare_time_in_create_task_hint_phrasing(clock):
    import timeutil
    r = command_parser.parse_command(
        "add a task: submit the lab record by friday 6 pm", clock)
    assert r["intent"] == "create_task"
    assert r["params"]["text"] == "submit the lab record"  # no stale '6 pm'
    local = timeutil.to_local(r["params"]["deadline_utc"])
    assert (local.month, local.day, local.hour, local.minute) == (9, 25, 18, 0)
