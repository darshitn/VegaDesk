"""Deterministic command parsing: the spec's behavioral examples must map to
the right intents (or clarifications) with no model involvement."""

from datetime import datetime

from command_parser import is_open_request, parse_command, unsupported_destination
from timeutil import FakeClock


def clock():
    # Monday 2026-09-21 10:00 IST
    return FakeClock(datetime(2026, 9, 21, 4, 30, 0))


def test_create_task_with_deadline():
    r = parse_command("Add a task: finish the DBMS assignment by Friday at 6 PM", clock())
    assert r["type"] == "intent" and r["intent"] == "create_task"
    assert r["params"]["text"] == "finish the DBMS assignment"
    assert r["params"]["deadline_utc"] == datetime(2026, 9, 25, 12, 30, 0)


def test_create_task_without_deadline():
    r = parse_command("create task: buy milk", clock())
    assert r["intent"] == "create_task"
    assert r["params"]["text"] == "buy milk"
    assert r["params"]["deadline_utc"] is None


def test_list_due_this_week():
    r = parse_command("What is due this week?", clock())
    assert r["intent"] == "list_tasks" and r["params"]["window"] == "week"


def test_list_tasks_variants():
    for cmd in ("list my tasks", "show tasks", "what tasks do i have"):
        r = parse_command(cmd, clock())
        assert r and r["intent"] == "list_tasks", cmd


def test_pending_task_queries_read_all_incomplete():
    # Audit finding #2: natural phrasings must reach the deterministic
    # list_tasks intent (window="all" -> only incomplete tasks) with no model.
    for cmd in ("What are my pending tasks?", "show my tasks", "any pending tasks?",
                "show my open tasks", "what are my incomplete tasks",
                "do i have any tasks", "what's on my plate", "what do i have to do"):
        r = parse_command(cmd, clock())
        assert r and r["intent"] == "list_tasks", cmd
        assert r["params"]["window"] == "all", cmd


def test_pending_query_with_window():
    r = parse_command("what are my pending tasks due today?", clock())
    assert r["intent"] == "list_tasks" and r["params"]["window"] == "today"


def test_list_tasks_still_reports_only_incomplete():
    # "pending" semantics: list_tasks filters completed.is_(False) at the tool
    # layer, so the parser only needs to route to it; assert intent here.
    r = parse_command("what is left", clock())
    assert r["intent"] == "list_tasks" and r["params"]["window"] == "all"


def test_set_timer():
    r = parse_command("Set a timer for 25 minutes", clock())
    assert r["intent"] == "start_timer"
    assert r["params"]["duration_seconds"] == 1500


def test_cancel_timer_no_id():
    r = parse_command("Cancel my timer", clock())
    assert r["intent"] == "cancel_timer"
    assert r["params"]["timer_id"] is None


def test_cancel_timer_with_id():
    r = parse_command("cancel timer 3", clock())
    assert r["intent"] == "cancel_timer" and r["params"]["timer_id"] == 3


def test_create_reminder():
    r = parse_command("Remind me tomorrow at 7 PM to revise trees", clock())
    assert r["intent"] == "create_reminder"
    assert r["params"]["text"] == "revise trees"
    assert r["params"]["due_utc"] == datetime(2026, 9, 22, 13, 30, 0)


def test_create_reminder_missing_time_asks_clarification():
    r = parse_command("Remind me tomorrow to revise trees", clock())
    assert r["type"] == "clarification"
    assert "what time" in r["question"].lower()


def test_snooze_reminder():
    r = parse_command("Snooze that reminder for 10 minutes", clock())
    assert r["intent"] == "snooze_reminder"
    assert r["params"]["snooze_seconds"] == 600
    assert r["params"]["reminder_id"] is None


def test_snooze_defaults_to_ten_minutes():
    r = parse_command("snooze that reminder", clock())
    assert r["intent"] == "snooze_reminder"
    assert r["params"]["snooze_seconds"] == 600


def test_start_focus_session():
    r = parse_command("Start a 45-minute focus session for DSA", clock())
    assert r["intent"] == "start_focus_session"
    assert r["params"]["duration_seconds"] == 2700
    assert r["params"]["objective"] == "DSA"


def test_end_focus_session_with_note():
    r = parse_command("End my focus session; I finished recursion practice", clock())
    assert r["intent"] == "end_focus_session"
    assert r["params"]["outcome_note"] == "I finished recursion practice"


def test_complete_task_by_id():
    r = parse_command("complete task 5", clock())
    assert r["intent"] == "set_task_completed"
    assert r["params"]["task_id"] == 5 and r["params"]["completed"] is True


def test_complete_task_by_text():
    r = parse_command("finish task buy milk", clock())
    assert r["intent"] == "set_task_completed"
    assert r["params"]["task_text"] == "buy milk"


def test_wake_prefixes_stripped():
    r = parse_command("hey jarvis, set a timer for 5 minutes", clock())
    assert r["intent"] == "start_timer" and r["params"]["duration_seconds"] == 300


def test_ordinary_chat_falls_through():
    for cmd in ("hello there", "what is the capital of France", "open chrome",
                "how do I sort a list in python"):
        assert parse_command(cmd, clock()) is None, cmd


def test_zero_duration_timer_asks_clarification():
    r = parse_command("set a timer for 0 minutes", clock())
    assert r["type"] == "clarification"


def test_ai_radar_refresh_intent():
    for cmd in ("refresh the ai radar", "run ai radar now", "update my ai news",
                "re-run the ai digest"):
        r = parse_command(cmd, clock())
        assert r is not None and r["intent"] == "refresh_ai_radar", cmd


def test_ai_radar_general_digest_intent():
    for cmd in ("what's new in ai", "show me the ai radar", "any new ai releases",
                "what ai news do you have"):
        r = parse_command(cmd, clock())
        assert r is not None and r["intent"] == "get_ai_radar_digest", cmd
        assert r["params"]["mode"] == "general", cmd


def test_ai_radar_free_model_digest_intent():
    for cmd in ("are there any free ai models", "which llm api is free right now",
                "any free credits for ai"):
        r = parse_command(cmd, clock())
        assert r is not None and r["intent"] == "get_ai_radar_digest", cmd
        assert r["params"]["mode"] == "free", cmd


def test_ai_radar_does_not_hijack_unrelated_chat():
    for cmd in ("what is the capital of France", "how do I sort a list in python",
                "tell me a joke about robots"):
        assert parse_command(cmd, clock()) is None, cmd


# ── M2a: wake-word residue in voice transcripts ─────────────────────
# Whisper frequently writes the spoken wake word into the captured command
# ("Hey Jarvis, set a timer..."). These must still hit the deterministic,
# offline intents — never fall through to the paid/cloud model.

def test_voice_transcripts_with_wake_word_prefix_route_offline():
    cases = {
        "Hey Jarvis, set a timer for 5 minutes": "start_timer",
        "hey jarvis what are my pending tasks": "list_tasks",
        "Hey Jarvis add a task buy groceries": "create_task",
        "hey vega remind me to stretch in 30 minutes": "create_reminder",
        "Hey Jarvis start a 45 minute focus session on DSA": "start_focus_session",
        "hey jarvis, refresh the ai radar": "refresh_ai_radar",
        # Noisy transcript: filler stays stripped, intent survives.
        "Hey Jarvis. Hey Jarvis. what's new in ai?": "get_ai_radar_digest",
    }
    for cmd, intent in cases.items():
        r = parse_command(cmd, clock())
        assert r and r["type"] == "intent" and r["intent"] == intent, cmd


def test_wake_word_prefix_alone_is_not_a_command():
    # A bare echo of the wake word (self-noise, TV) must parse to nothing so
    # no action and no model call happens.
    for cmd in ("Hey Jarvis", "hey jarvis", "hey vega!", "Hey, Jarvis."):
        assert parse_command(cmd, clock()) is None, cmd


# ── capability boundary: which destinations VEGA cannot reach ──────
# `tools.execute_intent` consults this before running anything, so the two
# lists below are the guard's whole contract: external systems blocked, and
# every request that names a VEGA object (or no destination at all) left
# alone. Checked here rather than only through the executor because a false
# positive would silently break a working offline command.

def test_unsupported_destinations_are_detected():
    cases = {
        "schedule a dentist appointment on my calendar": "your calendar",
        "add the dentist appointment to my google calendar": "your calendar",
        "put my labs in my calendar for next week": "your calendar",
        "schedule a dentist appointment into my planner": "your calendar",
        "put it on my google cal": "your calendar",
        "email the professor my lab record": "email",
        "send an email to the HOD about the backlog": "email",
        "drop a mail to the registrar": "email",
        "book a meeting room in outlook": "email",
        "message the class group on whatsapp about the venue": "a messaging app",
        "text mom that I'll be late": "a messaging app",
        "post this in the slack channel": "a messaging app",
        "add a row to my excel sheet": "a spreadsheet",
        "create a ticket in jira for the bug": "an issue tracker",
        "write it up in notion under semester 5": "a document tool",
        # Reads are requests too: VEGA cannot see a calendar either.
        "what is on my calendar tomorrow": "your calendar",
    }
    for phrase, destination in cases.items():
        r = unsupported_destination(phrase)
        assert r is not None, phrase
        assert r["destination"] == destination, phrase
        assert "Nothing was created or changed." in r["message"], phrase


def test_supported_requests_are_never_blocked():
    """The exemption that keeps 'add a task …' working, plus the phrasings a
    daily assistant actually says. 'to-do list in todoist' is allowed on
    purpose: naming a to-do list is an explicit choice of VEGA's own surface,
    and the trailing app name is not a destination the user needs served."""
    phrases = [
        "add a task to book a dentist appointment",
        "add a task: book a dentist appointment",
        "add a task: email the professor the lab record",
        "add a task: text the landlord about the rent",
        "add a task: buy a wall calendar",
        "add this to my to-do list in todoist",
        "remind me tomorrow at 7 pm to email the professor",
        "remind me to send the message to the class group",
        "remind me to book a dentist appointment on friday",
        "what is the text of task 3",
        "add assignment linear algebra problem set due friday",
        "add coursework DBMS lab due monday",
        "start a focus session on the whatsapp project",
        "set a timer for 25 minutes",
        "start a pomodoro of 50 minutes",
        "what are my pending tasks?",
        "what's due this week",
        "list my coursework",
        "list my projects",
        "register a project called Thesis (academic) with next action write the intro",
        "update next action for Thesis: write the intro",
        "add a session note: finished the normalization chapter",
        "link task 2 to project Thesis",
        "what's on my plate today",
        "i'm done for the day",
        "refresh the ai radar",
        "any new free models?",
        "cancel the timer",
        "mark task 3 as done",
        "tell me a joke about calendars",
        # No destination named at all: VEGA's task list is its own schedule, so
        # this stays a capture rather than a refusal (documented limit).
        "schedule a dentist appointment for friday 2 pm",
        "book a dentist appointment",
    ]
    for phrase in phrases:
        assert unsupported_destination(phrase) is None, phrase


def test_empty_and_non_text_input_are_not_destinations():
    assert unsupported_destination("") is None
    assert unsupported_destination("   ") is None
    assert unsupported_destination(None) is None
    assert unsupported_destination("x" * 5000) is None


def test_open_requests_are_distinguished_from_write_requests():
    """The launch lane is refused for a destination the user asked to WRITE to
    but allowed for one they asked to reach, so the verb test is load-bearing."""
    for phrase in ("open my google calendar", "show me my calendar", "launch spotify",
                   "pull up gmail", "go to my outlook calendar", "could you open mail",
                   "hey vega, open my calendar"):
        assert is_open_request(phrase), phrase
    for phrase in ("add the interview to my google calendar", "email the HOD my report",
                   "put this on my calendar for friday", "schedule a dentist appointment",
                   "my calendar is full", ""):
        assert not is_open_request(phrase), phrase
    assert not is_open_request(None)


def test_live_eval_phrases_are_classified_as_expected():
    """The live 39-case eval corpus, pinned so its two regressions can never
    come back silently. Both were found by running the eval, not by reading the
    guard: 'add to my list: email the professor' names VEGA's own list (so
    'list' is a native object), and 'what is on my agenda?' is the ordinary way
    to ask for today's plan (so 'agenda' is not in the destination vocabulary
    at all). A phrase the guard blocks cannot be scored as a normal action, so
    every non-destination eval phrase must stay open.
    """
    blocked = {
        "schedule a dentist appointment on my calendar": "your calendar",
        "add the interview to my google calendar on saturday 10 am": "your calendar",
        "what is on my calendar tomorrow": "your calendar",
        "email the HOD my backlog report": "email",
        "message the class group on whatsapp about the venue change": "a messaging app",
    }
    open_for_model = [
        "Could you jot down that I need to submit the lab record by Friday at 6 pm",
        "make a note to buy groceries this weekend",
        "add to my list: email the professor about the extension",
        "What is currently on my agenda?",
        "Give me a rundown of what I still need to get done",
        "Ping me tomorrow morning at 9 to call the bank",
        "Let us dive into deep work for 45 minutes on the thesis",
        "put on a one hour study block for linear algebra",
        "I have taken care of the data check",
        "delete every single task I have",
        "read all my notes and change them",
        "schedule a dentist appointment for friday 2 pm",
        "put this in my task list: book a dentist appointment for next week",
        "remind me to email the professor about the extension tomorrow at 10",
        "wake me up at 7 tomorrow for the flight",
    ]
    for phrase, destination in blocked.items():
        r = unsupported_destination(phrase)
        assert r and r["destination"] == destination, phrase
    for phrase in open_for_model:
        assert unsupported_destination(phrase) is None, phrase


def test_open_alternative_message_tells_the_user_how_to_get_the_tab():
    """The launch-lane refusal must not read as 'VEGA can never show you a
    calendar' — opening it is a request the launch lane does honor."""
    plain = unsupported_destination("add it to my calendar")
    launched = unsupported_destination("add it to my calendar", open_alternative=True)
    assert "Nothing was opened" not in plain["message"]
    assert "Nothing was opened either" in launched["message"]
    assert "open your calendar" in launched["message"]
    assert launched["destination"] == plain["destination"]
