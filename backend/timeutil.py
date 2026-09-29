"""Time handling for VEGA M1: injectable clock, local time-zone context, and
deterministic natural-language date/duration parsing.

Storage rule: every scheduled instant is stored as naive UTC in SQLite.
Resolution rule: relative phrases ("tomorrow at 7 PM", "in 10 minutes") are
resolved in the machine's configured local time zone via ``astimezone()``
(which applies the OS zone rules, including DST). The time-zone context string
kept on each record is ``VEGA_TIMEZONE`` (IANA name) if set, else a system
description like "system local (India Standard Time, UTC+05:30)".

Ambiguity rule: if a phrase resolves to a time that has already passed, or a
required time-of-day is missing, parsing returns a clarification question
instead of guessing.
"""

import os
import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
    "fri": 4, "sat": 5, "sun": 6,
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
}

# Default time-of-day when only a date is given, per context.
# deadline: "by Friday" means end of that day; reminder/timer: a concrete
# time-of-day is required (missing -> clarification, never a silent guess).
DEFAULT_TIME_BY_CONTEXT = {
    "deadline": (23, 59, 0),
    "reminder": None,
    "timer": None,
}


class Clock:
    """Injectable clock. Production code uses SystemClock; tests use FakeClock."""

    def now_utc(self) -> datetime:
        raise NotImplementedError


class SystemClock(Clock):
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeClock(Clock):
    def __init__(self, fixed: datetime):
        self._now = fixed.replace(tzinfo=None) if fixed.tzinfo else fixed

    def now_utc(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt.replace(tzinfo=None) if dt.tzinfo else dt


def naive_utc(dt: datetime) -> datetime:
    """Aware datetime -> naive UTC (the DB storage format)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_utc(naive_local: datetime) -> datetime:
    """Naive local datetime -> naive UTC using the machine's time zone."""
    return naive_utc(naive_local.astimezone(timezone.utc))


def to_local(dt_naive_utc: datetime) -> datetime:
    """Naive UTC from the DB -> aware local datetime."""
    return dt_naive_utc.replace(tzinfo=timezone.utc).astimezone()


def local_now(clock: Clock) -> datetime:
    return to_local(clock.now_utc())


def tz_context_name() -> str:
    """Time-zone context string persisted with scheduled records."""
    env_tz = (os.getenv("VEGA_TIMEZONE") or "").strip()
    if env_tz:
        return env_tz
    now_local = datetime.now(timezone.utc).astimezone()
    offset = now_local.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"system local ({now_local.tzname()}, {offset})"


def render_local(dt_naive_utc: datetime) -> str:
    """Readable local rendering of a stored UTC instant."""
    return to_local(dt_naive_utc).strftime("%A, %d %B %Y at %I:%M %p")


def week_bounds_utc(clock: Clock):
    """(start, end) naive-UTC bounds of the current local calendar week (Mon-Sun)."""
    now_local = local_now(clock)
    monday = (now_local - timedelta(days=now_local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday_end = (monday + timedelta(days=7)) - timedelta(microseconds=1)
    return naive_utc(monday.astimezone(timezone.utc)), naive_utc(sunday_end.astimezone(timezone.utc))


# ─────────────────────────────────────────────
# Duration parsing
# ─────────────────────────────────────────────

_DURATION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*-?\s*"
    r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)\b",
    re.IGNORECASE,
)


# Bounded English word-numbers so "twenty minutes" / "an hour" work on BOTH the
# deterministic parser and the model 'duration_text' lane (one shared function).
# Deliberately limited to a day-scale, spelled-unit subset — no free-form math.
_WORD_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_WORD_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

def _alt(mapping):
    return "|".join(sorted(mapping, key=len, reverse=True))

# num-word: 'a'/'an' (=1), a tens optionally followed by an one-word, or a bare one-word.
_NUMWORD_RE = (
    r"(?:a|an"
    r"|(?:(?:" + _alt(_WORD_TENS) + r")(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?)"
    r"|" + _alt(_WORD_ONES) + r")"
)
# Word durations require a spelled-out unit (no bare s/m/h/d) to stay unambiguous.
_UNIT_ALT_SPELLED = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?"
_WORD_DUR_RE = re.compile(
    r"\b(?P<num>" + _NUMWORD_RE + r")\s*-?\s*(?P<unit>" + _UNIT_ALT_SPELLED + r")\b",
    re.IGNORECASE,
)


def _word_num_value(s: str):
    s = s.strip().lower()
    if s in ("a", "an"):
        return 1
    parts = [p for p in re.split(r"[\s-]+", s) if p]
    if not parts:
        return None
    total = 0
    for p in parts:
        v = _WORD_ONES.get(p, _WORD_TENS.get(p))
        if v is None:
            return None
        total += v
    return total


def _normalize_word_durations(text: str) -> str:
    """Rewrite spelled durations ('twenty five minutes', 'an hour') into the
    canonical '<N> <unit>' form the digit regex already understands, so both
    parsing lanes share one implementation. Leaves digit phrases untouched."""
    def repl(m):
        n = _word_num_value(m.group("num"))
        if n is None:
            return m.group(0)
        return f" {n} {m.group('unit')} "
    return _WORD_DUR_RE.sub(repl, text)


def parse_duration_seconds(text: str):
    """Total seconds from phrases like '25 minutes', '45-minute', '1.5 hours',
    '90 seconds', and bounded English word-numbers ('twenty minutes', 'an hour',
    'half an hour', 'two hours'). Returns None if no duration is present. Raises
    ValueError for zero/negative totals (deliberate handling per spec)."""
    if not text:
        return None
    # Bounded 'X hour(s) and a half' / 'half (an|a) hour' idioms, handled on the
    # original text BEFORE word normalization (else the inner 'an hour' would be
    # turned into '1 hour' and the trailing 'and a half' silently dropped).
    text = re.sub(r"\bhours?\s+and\s+a\s+half\b", "hour 30 minutes",
                  text, count=1, flags=re.IGNORECASE)
    text = re.sub(
        r"\bhalf\b[\s-]*(?:an?[\s-]+)?hour", " 30 minutes ",
        text, count=1, flags=re.IGNORECASE)
    normalized = _normalize_word_durations(text)
    # A negative length is a mistake, not a countdown. The sign is not part of
    # _DURATION_RE, so without this check 'minus five minutes' (normalized to
    # 'minus 5 minutes') and '-5 minutes' silently became a POSITIVE 300s timer.
    if (re.search(r"\b(?:minus|negative)\b[\w.]*\s*[\w.]*\s*\d", normalized, re.IGNORECASE)
            or re.search(r"(?:^|[\s(,])-[\d.]", normalized)):
        raise ValueError("A duration can't be negative. Please give a positive length.")
    total = 0.0
    found = False
    for m in _DURATION_RE.finditer(normalized):
        value = float(m.group(1))
        unit = m.group(2).lower().rstrip(".")
        total += value * _UNIT_SECONDS.get(unit, 0)
        found = True
    if not found:
        return None
    total = int(round(total))
    if total <= 0:
        raise ValueError("Duration must be greater than zero.")
    return total


# ─────────────────────────────────────────────
# When-phrase parsing
# ─────────────────────────────────────────────

_DUR_TOKEN = (
    r"\d+(?:\.\d+)?\s*-?\s*(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d)"
    r"(?:\s+and\s+\d+(?:\.\d+)?\s*-?\s*(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h))?"
)
_TIME_TOKEN = r"(?:\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|noon|midnight)"
_WEEKDAY_TOKEN = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH_TOKEN = "|".join(sorted(MONTHS, key=len, reverse=True))
_AT_TIME = r"(?:at|@)\s+" + _TIME_TOKEN
# A time written without 'at' after a date: 'friday 6 pm', '25 oct 18:30'.
# Stricter than _TIME_TOKEN on purpose — it must carry a meridiem or a colon, so
# the day number of '25 march' can never be read as an hour.
_BARE_TIME = (r"(?:\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?"
              r"|\d{1,2}\s*[ap]\.?\s*m\.?"
              r"|noon|midnight)")
_TIME_TAIL = r"(?:" + _AT_TIME + r"|" + _BARE_TIME + r")"

_WHEN_PATTERN = re.compile(
    r"(?:\b(?:by|before|due|until)\b[\s,]+)?"
    r"(?:"
    r"(?P<in_dur>in\s+" + _DUR_TOKEN + r")"
    r"|(?P<tomorrow>tomorrow(?:\s+" + _TIME_TAIL + r")?)"
    r"|(?P<today>today\s+" + _TIME_TAIL + r")"
    r"|(?P<tonight>tonight(?:\s+" + _TIME_TAIL + r")?)"
    r"|(?P<weekday>(?:next\s+|this\s+)?(?:" + _WEEKDAY_TOKEN + r")\b(?:\s+" + _TIME_TAIL + r")?)"
    r"|(?P<iso_date>\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?\b)"
    r"|(?P<month_day>\b(?:on\s+)?(?:" + _MONTH_TOKEN + r")\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b(?:\s+" + _TIME_TAIL + r")?)"
    r"|(?P<day_month>\b(?:on\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTH_TOKEN + r")(?:,?\s*\d{4})?\b(?:\s+" + _TIME_TAIL + r")?)"
    r"|(?P<at_time>\b" + _AT_TIME + r")"
    r")",
    re.IGNORECASE,
)


class WhenResult:
    """Outcome of parsing a when-phrase: exactly one field is meaningful."""

    def __init__(self, utc_dt=None, matched_text=None, question=None, found=False):
        self.utc_dt = utc_dt            # naive UTC instant when resolved
        self.matched_text = matched_text  # the span consumed from the input
        self.question = question        # clarification text when ambiguous
        self.found = found              # whether a when-phrase was located at all

    @property
    def ok(self):
        return self.found and self.utc_dt is not None and self.question is None

    @property
    def needs_clarification(self):
        return self.found and self.question is not None


_TIME_ONLY_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?|noon|midnight", re.IGNORECASE)
# Same three groups as _TIME_ONLY_RE, but a meridiem or a colon is REQUIRED (see
# _BARE_TIME) so finditer can skip a leading day number: in '25 oct 6 pm' the
# first candidate is the bare '25', which carries no marker and must not win.
_BARE_TIME_RE = re.compile(
    r"\b(?:(\d{1,2}):(\d{2})|(\d{1,2})\s*([ap])\.?\s*m\.?)"
    r"(?:\s*([ap])\.?\s*m\.?)?(?![a-z0-9])", re.IGNORECASE)


def _hm(hour_s, minute_s, mer):
    hour = int(hour_s)
    minute = int(minute_s or 0)
    mer = (mer or "").replace(".", "").lower()
    if mer.startswith("p") and hour < 12:
        hour += 12
    elif mer.startswith("a") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return (hour, minute)


def _time_from_phrase(phrase: str):
    """Extract (hour, minute) from a phrase's trailing time, or None. When the
    phrase says 'at <time>' only the part after it is considered; otherwise a
    bare time is accepted only if it carries a meridiem or a colon, so date
    numbers ('25 march') are never mistaken for hours."""
    if not phrase:
        return None
    low = phrase.lower()
    parts = re.split(r"(?:^|\s+)(?:at|@)\s+", low)
    segment = parts[-1] if len(parts) > 1 else None
    if segment is None:
        if re.search(r"\bnoon\b", low):
            return (12, 0)
        if re.search(r"\bmidnight\b", low):
            return (0, 0)
        for bm in _BARE_TIME_RE.finditer(low):
            if bm.group(3) is not None:
                return _hm(bm.group(3), "0", bm.group(4) or bm.group(5))
            return _hm(bm.group(1), bm.group(2), bm.group(5))
        return None
    m = _TIME_ONLY_RE.search(segment)
    if not m:
        return None
    if m.group(0) == "noon":
        return (12, 0)
    if m.group(0) == "midnight":
        return (0, 0)
    if m.group(1) is None:
        return None
    return _hm(m.group(1), m.group(2), m.group(3))


def _date_part(phrase: str) -> str:
    """Strip a leading 'on' and a trailing ' at <time>' so date token
    extraction can't pick up time digits or the preposition."""
    out = re.split(r"\s+at\s+", phrase, maxsplit=1, flags=re.IGNORECASE)[0]
    out = re.sub(r"^\s*on\s+", "", out, flags=re.IGNORECASE)
    return out.strip()


def _resolve_when_match(m: re.Match, clock: Clock, context: str) -> WhenResult:
    now_local = local_now(clock)
    matched = m.group(0).strip()
    default_time = DEFAULT_TIME_BY_CONTEXT.get(context)
    groups = m.groupdict()

    def combine(day_date, time_hm, label):
        """day_date: naive local date/datetime; time_hm: (h, m) or None."""
        if time_hm is None:
            if default_time is None:
                when_word = label if label in ("tomorrow", "today", "tonight") else f"on {label}"
                return WhenResult(
                    found=True, matched_text=matched,
                    question=f"What time {when_word}? Please give me a specific time, like '{label} at 7 PM'.")
            time_hm = (default_time[0], default_time[1])
        naive_local = day_date.replace(hour=time_hm[0], minute=time_hm[1], second=0, microsecond=0)
        utc_dt = to_utc(naive_local)
        if utc_dt <= clock.now_utc():
            return WhenResult(
                found=True, matched_text=matched,
                question=(f"{render_local(utc_dt)} has already passed. "
                          f"Did you mean a later time or the next occurrence? Please be specific."))
        return WhenResult(utc_dt=utc_dt, matched_text=matched, found=True)

    if groups.get("in_dur"):
        try:
            seconds = parse_duration_seconds(groups["in_dur"])
        except ValueError as e:
            return WhenResult(found=True, matched_text=matched, question=str(e))
        if seconds is None:
            return WhenResult(found=False)
        return WhenResult(utc_dt=clock.now_utc() + timedelta(seconds=seconds),
                          matched_text=matched, found=True)

    if groups.get("tomorrow") is not None:
        day = (now_local + timedelta(days=1)).replace(tzinfo=None)
        return combine(day, _time_from_phrase(matched), "tomorrow")

    if groups.get("today") is not None:
        day = now_local.replace(tzinfo=None)
        return combine(day, _time_from_phrase(matched), "today")

    if groups.get("tonight") is not None:
        hm = _time_from_phrase(matched)
        if hm is None:
            # "tonight" without a time is genuinely ambiguous -> clarify.
            return WhenResult(found=True, matched_text=matched,
                              question="What time tonight? Please give a specific time, like 'tonight at 9 PM'.")
        day = now_local.replace(tzinfo=None)
        return combine(day, hm, "tonight")

    if groups.get("weekday") is not None:
        wd_phrase = groups["weekday"].lower()
        name = re.match(r"(?:next\s+|this\s+)?([a-z]+)", wd_phrase).group(1)
        target_wd = WEEKDAYS[name]
        days_ahead = (target_wd - now_local.weekday()) % 7
        day = (now_local + timedelta(days=days_ahead)).replace(tzinfo=None)
        hm = _time_from_phrase(matched)
        result = combine(day, hm, name.title())
        # Weekday+time already passed today: ask instead of silently rolling a week.
        if result.needs_clarification and days_ahead == 0 and hm is not None:
            next_day = day + timedelta(days=7)
            next_local = next_day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            result = WhenResult(
                found=True, matched_text=matched,
                question=(f"{name.title()} at {hm[0]:02d}:{hm[1]:02d} has already passed today. "
                          f"Did you mean next {name} ({render_local(to_utc(next_local))})?"))
        return result

    if groups.get("iso_date") is not None:
        raw = groups["iso_date"].replace(" ", "T")
        try:
            if "T" in raw:
                naive_local = datetime.fromisoformat(raw)
            elif default_time is not None:
                naive_local = datetime.fromisoformat(raw).replace(
                    hour=default_time[0], minute=default_time[1])
            else:
                naive_local = datetime.fromisoformat(raw + "T23:59:00")
        except ValueError:
            return WhenResult(found=False)
        utc_dt = to_utc(naive_local)
        if utc_dt <= clock.now_utc():
            return WhenResult(found=True, matched_text=matched,
                              question=f"{render_local(utc_dt)} has already passed. Did you mean a future date?")
        return WhenResult(utc_dt=utc_dt, matched_text=matched, found=True)

    for key in ("month_day", "day_month"):
        if groups.get(key) is not None:
            phrase = groups[key]
            hm = _time_from_phrase(phrase)
            date_phrase = _date_part(phrase)
            month_name = re.search(r"[a-zA-Z]+", date_phrase)
            month = MONTHS.get(month_name.group(0).lower()) if month_name else None
            nums = re.findall(r"\d{1,4}", date_phrase)
            if month is None or not nums:
                return WhenResult(found=False)
            if key == "month_day":
                day_num = int(nums[0])
                year = int(nums[1]) if len(nums) > 1 and len(nums[1]) == 4 else None
            else:
                day_num = int(nums[0])
                year = int(nums[-1]) if len(nums) > 1 and len(nums[-1]) == 4 else None
            effective_time = hm or default_time or (23, 59)
            if year is None:
                year = now_local.year
                try:
                    candidate = datetime(year, month, day_num, effective_time[0], effective_time[1])
                except ValueError:
                    return WhenResult(found=True, matched_text=matched,
                                      question=f"'{date_phrase}' is not a valid date. Please check the day and month.")
                if to_utc(candidate) <= clock.now_utc():
                    year += 1  # date without a year -> next occurrence (documented rule)
            try:
                day = datetime(year, month, day_num)
            except ValueError:
                return WhenResult(found=True, matched_text=matched,
                                  question=f"'{date_phrase}' is not a valid date. Please check the day and month.")
            return combine(day, hm, date_phrase)

    if groups.get("at_time") is not None:
        hm = _time_from_phrase(matched)
        if hm is None:
            return WhenResult(found=False)
        candidate = now_local.replace(tzinfo=None, hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if to_utc(candidate) <= clock.now_utc():
            candidate = candidate + timedelta(days=1)  # "at 7pm" after 7pm -> tomorrow (documented)
        return WhenResult(utc_dt=to_utc(candidate), matched_text=matched, found=True)

    return WhenResult(found=False)


def extract_when(text: str, clock: Clock, context: str = "reminder") -> WhenResult:
    """Scan text for the first resolvable when-phrase. Returns a WhenResult;
    ``matched_text`` lets callers strip the phrase from a task/reminder body."""
    if not text:
        return WhenResult(found=False)
    for m in _WHEN_PATTERN.finditer(text):
        result = _resolve_when_match(m, clock, context)
        if result.found:
            return result
    return WhenResult(found=False)
