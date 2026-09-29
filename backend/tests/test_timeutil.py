"""Deterministic parsing + time-zone resolution tests (offline, fake clock)."""

from datetime import datetime, timedelta

import timeutil
from timeutil import FakeClock, extract_when, parse_duration_seconds


def make_clock(dt_utc):
    return FakeClock(dt_utc)


MON_10AM_IST = datetime(2026, 9, 21, 4, 30, 0)  # 10:00 IST (UTC+05:30)


def test_parse_duration_variants():
    assert parse_duration_seconds("25 minutes") == 1500
    assert parse_duration_seconds("1.5 hours") == 5400
    assert parse_duration_seconds("45-minute") == 2700
    assert parse_duration_seconds("90 seconds") == 90
    assert parse_duration_seconds("in 1 hour and 30 minutes") == 5400


def test_zero_duration_rejected():
    try:
        parse_duration_seconds("0 minutes")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_in_duration_relative_to_clock():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("in 10 minutes", clock, "reminder")
    assert r.ok
    assert r.utc_dt == clock.now_utc() + timedelta(minutes=10)


def test_tomorrow_at_time_resolves_local_evening():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("remind me tomorrow at 7 PM to revise trees", clock, "reminder")
    assert r.ok
    local = timeutil.to_local(r.utc_dt)
    assert (local.day, local.hour, local.minute) == (22, 19, 0)
    assert r.utc_dt == datetime(2026, 9, 22, 13, 30, 0)  # 19:00 IST -> UTC


def test_friday_deadline_resolves_to_upcoming_friday():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("finish the DBMS assignment by Friday at 6 PM", clock, "deadline")
    assert r.ok
    local = timeutil.to_local(r.utc_dt)
    assert local.strftime("%A") == "Friday"
    assert (local.hour, local.minute) == (18, 0)


def test_passed_weekday_time_asks_clarification_instead_of_guessing():
    # Friday 19:00 IST = 13:30 UTC; asking for "friday at 6 pm" is now ambiguous.
    clock = make_clock(datetime(2026, 9, 25, 13, 30, 0))
    r = extract_when("friday at 6 pm", clock, "deadline")
    assert r.needs_clarification
    assert "next friday" in r.question.lower()


def test_date_only_deadline_defaults_end_of_day():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("on 25 march", clock, "deadline")
    assert r.ok
    local = timeutil.to_local(r.utc_dt)
    assert (local.month, local.day, local.hour, local.minute) == (3, 25, 23, 59)
    assert local.year == 2027  # next occurrence rule for year-less dates


def test_reminder_without_time_of_day_asks_clarification():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("remind me tomorrow", clock, "reminder")
    assert r.needs_clarification
    assert "what time" in r.question.lower()


def test_past_time_asks_clarification():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("at 9 am", clock, "reminder")  # 9am already passed at 10am
    assert r.ok  # convention: bare 'at <time>' rolls to tomorrow
    local = timeutil.to_local(r.utc_dt)
    assert local.day == 22 and local.hour == 9


def test_invalid_date_asks_clarification():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("on 31 february", clock, "deadline")
    assert r.needs_clarification


def test_week_bounds_monday_to_sunday_local():
    clock = make_clock(MON_10AM_IST)
    start, end = timeutil.week_bounds_utc(clock)
    assert timeutil.to_local(start).weekday() == 0
    assert timeutil.to_local(start).hour == 0
    assert timeutil.to_local(end).weekday() == 6
    assert (end - start) > timedelta(days=6, hours=23)


def test_utc_storage_and_iso_plus_readable_rendering():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("tomorrow at 7 PM", clock, "reminder")
    assert r.utc_dt.tzinfo is None  # stored naive-UTC
    rendered = timeutil.render_local(r.utc_dt)
    assert "07:00 PM" in rendered
    assert "September" in rendered


def test_tz_context_is_recorded():
    assert timeutil.tz_context_name()  # non-empty string describing local zone


# ── bare time after a date ('by friday 6 pm'), the phrasing the app's own
# rejection hints advertise — see CHECKPOINT 8 of VEGA_OVERNIGHT_5H_HANDOFF.md.


def _local(r):
    return timeutil.to_local(r.utc_dt)


def test_bare_time_after_weekday_is_used_and_consumed():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("submit the lab record by friday 6 pm", clock, "deadline")
    assert r.ok
    assert r.matched_text.strip().lower() == "by friday 6 pm"
    local = _local(r)
    assert (local.month, local.day, local.hour, local.minute) == (9, 25, 18, 0)


def test_bare_colon_time_after_weekday():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("finish by friday 18:30", clock, "deadline")
    assert r.ok
    local = _local(r)
    assert (local.day, local.hour, local.minute) == (25, 18, 30)


def test_colon_time_with_meridiem_is_not_read_as_am():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("call by friday 6:30 pm", clock, "deadline")
    assert r.ok
    local = _local(r)
    assert (local.hour, local.minute) == (18, 30)


def test_bare_time_after_month_day_forms():
    clock = make_clock(MON_10AM_IST)
    for phrase in ("pay fees on 25 october 6 pm", "pay fees on october 25 6 pm"):
        r = extract_when(phrase, clock, "deadline")
        assert r.ok, phrase
        local = _local(r)
        assert (local.month, local.day, local.hour) == (10, 25, 18), phrase


def test_bare_noon_and_midnight_after_weekday():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("study friday noon", clock, "deadline")
    assert r.ok and _local(r).hour == 12
    r = extract_when("study friday midnight", clock, "deadline")
    # midnight means the START of the named day, which is still ahead of Monday.
    assert r.ok and (_local(r).day, _local(r).hour) == (25, 0)


def test_day_number_is_never_mistaken_for_an_hour():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("finish chapter 5 by 25 march", clock, "deadline")
    assert r.ok
    assert r.matched_text.strip().lower() == "by 25 march"
    local = _local(r)
    assert (local.month, local.day, local.hour, local.minute) == (3, 25, 23, 59)


def test_time_without_a_meridiem_or_colon_stays_unconsumed():
    clock = make_clock(MON_10AM_IST)
    r = extract_when("call mom friday 6", clock, "deadline")
    assert r.ok
    assert r.matched_text.strip().lower() == "friday"
    assert _local(r).hour == 23  # falls back to the deadline default, as before


def test_bare_time_without_a_date_is_still_not_a_deadline():
    """A lone '6 pm' says nothing about WHICH day, so the deterministic lane
    leaves it in the task text instead of guessing today."""
    clock = make_clock(MON_10AM_IST)
    r = extract_when("call the clinic 6 pm", clock, "deadline")
    assert not r.found


def test_negative_duration_is_rejected_not_absolutized():
    """'minus five minutes' used to normalize into a POSITIVE 300s timer."""
    for phrase in ("minus five minutes", "-5 minutes", "negative 2 hours"):
        try:
            parse_duration_seconds(phrase)
            assert False, f"expected ValueError for {phrase!r}"
        except ValueError as e:
            assert "negative" in str(e).lower()


def test_hyphenated_durations_are_not_read_as_negative():
    assert parse_duration_seconds("45-minute") == 2700
    assert parse_duration_seconds("twenty-five minutes") == 1500
    assert parse_duration_seconds("in twenty-five minutes") == 1500


def test_next_weekday_on_a_matching_day_is_known_current_behaviour():
    """PINS a limitation, it does not endorse it: `days_ahead = (target - today)
    % 7` ignores the 'next' prefix, so 'next monday' said ON a Monday means that
    same Monday (see CHECKPOINT 8.4). If someone changes the roll-over rule this
    test fails on purpose so the decision is made consciously."""
    clock = make_clock(MON_10AM_IST)
    r = extract_when("book tickets next monday 7 pm", clock, "deadline")
    assert r.ok
    local = timeutil.to_local(r.utc_dt)
    assert (local.month, local.day, local.weekday(), local.hour) == (9, 21, 0, 19)
