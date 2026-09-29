"""Deterministic command parsing for VEGA M1.

One parser serves typed commands and wake-word transcripts. Returns either an
intent dict, a clarification request (ambiguous dates never get guessed), or
None (caller falls through to the existing open_app/open_website fast path and
then to the selected model). Fully offline — no model calls here.
"""

import re

try:
    import timeutil
except ImportError:
    from . import timeutil

# Optional polite/wake prefixes users add in front of a command.
_PREFIX = r"^(?:hey\s+(?:jarvis|vega)|vega|jarvis|ok\s+vega|please|can\s+you|could\s+you|would\s+you|i\s+want\s+to|i\s+need\s+to|let's|lets)[,!\s]*"

_TASK_REF_NUM = r"(?:#?\s*(\d+))"


def _strip_prefixes(text: str) -> str:
    prev = None
    t = text.strip()
    while prev != t:
        prev = t
        t = re.sub(_PREFIX, "", t, count=1, flags=re.IGNORECASE).strip()
    return t


def _clean_body(text: str) -> str:
    """Strip leftover prepositions/fillers around a removed when-phrase."""
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"[,;]?\s*\b(?:by|before|due|until|at|on|in|for|to|that|should|remind me|reminder)\b\s*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*(?:to|that)\s+", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+", " ", t).strip(" ,;.")
    return t


def _strip_when(body: str, when) -> str:
    """Remove the matched when-phrase (and its preposition) from body text."""
    if not when.matched_text:
        return body
    span = when.matched_text
    out = body.replace(span, " ", 1)
    # also remove a dangling preposition that introduced the span
    out = re.sub(r"\b(?:by|before|due|until|at|on|in|for)\s+(?=[,;.]|\s*$)", " ", out, flags=re.IGNORECASE)
    return _clean_body(out)


def parse_command(text: str, clock):
    """Parse a command. Returns dict {type: intent|clarification, ...} or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    t = _strip_prefixes(text)[:600]
    low = t.lower()

    # ── Stage 3: coursework completion — checked FIRST because the generic
    # task-completion branch below would otherwise grab "complete coursework 3".
    # Keyed on the literal token "coursework", which no task phrasing uses. ──
    m = re.search(r"\bcoursework\s+#?\s*(\d+)\b", low)
    if m and re.search(r"\b(done|complete|completed|finish|finished|mark|tick|cross)\b", low):
        return {"type": "intent", "intent": "complete_coursework",
                "params": {"coursework_id": int(m.group(1)), "completed": True}}

    # ── list tasks / what is due ────────────────────────────────
    m = re.match(r"^(?:what(?:'s| is)?\s+(?:due|on)\s+(this\s+week|today|tomorrow)|"
                 r"(?:list|show|read)\s+(?:me\s+)?(?:my\s+|all\s+|the\s+)?(?:tasks|to-?do list|todos)|"
                 r"what\s+(?:tasks|to-?dos?)\s+(?:do i have|are there|is left))\??$", low)
    if m:
        window = (m.group(1) or "all").replace("this ", "")
        return {"type": "intent", "intent": "list_tasks", "params": {"window": window}}

    # Natural task-query variants (audit finding #2): "what are my pending
    # tasks?", "show my open tasks", "any pending tasks?", "what's on my
    # plate". "pending"/"open" map to window="all" because list_tasks already
    # filters to incomplete tasks; an explicit due-window narrows further.
    _window_m = re.search(r"\b(?:due\s+)?(this\s+week|today|tomorrow)\b", low)
    _win = (_window_m.group(1).replace("this ", "") if _window_m else "all")
    m = re.match(
        r"^(?:(?:what|which)\s+(?:are|is)|show|list|read|display|give|tell)\s+"
        r"(?:me\s+)?(?:my\s+|all\s+|the\s+|any\s+)*"
        r"(?:pending|open|incomplete|unfinished|remaining|left|leftover)?\s*"
        r"(?:tasks?|to-?dos?|to-?do\s+list|todo\s+list)"
        r"(?:\s+(?:due|on)\s+(?:this\s+week|today|tomorrow))?"
        r"\s*(?:do\s+i\s+have|are\s+there|is\s+left|are\s+pending|are\s+open)?\??$", low)
    if m:
        return {"type": "intent", "intent": "list_tasks", "params": {"window": _win}}
    m = re.match(
        r"^(?:do\s+i\s+have|have\s+i\s+got|any)\s+"
        r"(?:my\s+|any\s+)*"
        r"(?:pending|open|incomplete|unfinished|remaining)?\s*"
        r"(?:tasks?|to-?dos?)\??$", low)
    if m:
        return {"type": "intent", "intent": "list_tasks", "params": {"window": _win}}
    m = re.match(r"^(?:what(?:'s| is)?\s+on\s+my\s+(?:plate|list)|what\s+(?:do|does)\s+i\s+(?:have|need)\s+to\s+do|what(?:'s| is)\s+left|anything\s+(?:due|pending))\??$", low)
    if m:
        return {"type": "intent", "intent": "list_tasks", "params": {"window": _win}}

    # ── create task ─────────────────────────────────────────────
    m = re.match(r"^(?:add|create|make|new)\s+(?:a\s+|new\s+)?(?:task|to-?do)[:\s]+(.+?)[.!?]*$", t, re.IGNORECASE)
    if m:
        body = m.group(1).strip()
        if not body:
            return None
        when = timeutil.extract_when(body, clock, context="deadline")
        if when.needs_clarification:
            return {"type": "clarification", "question": when.question}
        deadline_utc = when.utc_dt if when.ok else None
        if when.ok:
            body = _strip_when(body, when)
        if not body:
            return {"type": "clarification",
                    "question": "What should the task say? For example: 'Add a task: finish the DBMS assignment by Friday at 6 PM'."}
        return {"type": "intent", "intent": "create_task",
                "params": {"text": body[:500], "deadline_utc": deadline_utc}}

    # ── complete task ───────────────────────────────────────────
    m = re.match(r"^(?:complete|finish|mark|check\s+off|close)\s+(?:task\s+)?" + _TASK_REF_NUM +
                 r"(?:\s+as\s+(?:done|complete|completed|finished))?$", low)
    if not m:
        m = re.match(r"^mark\s+(?:task\s+)?" + _TASK_REF_NUM + r"\s+as\s+(?:done|complete|completed|finished)$", low)
    if not m:
        m = re.match(r"^task\s+" + _TASK_REF_NUM + r"\s+(?:is\s+)?(?:done|complete|completed|finished)$", low)
    if m:
        return {"type": "intent", "intent": "set_task_completed",
                "params": {"task_id": int(m.group(1)), "completed": True}}
    m = re.match(r"^(?:complete|finish|mark\s+done)\s+task\s+(?:called\s+|named\s+)?(.+?)[.!?]*$", t, re.IGNORECASE)
    if m and not re.match(r"^\d+$", m.group(1).strip()):
        return {"type": "intent", "intent": "set_task_completed",
                "params": {"task_text": _clean_body(m.group(1))[:500], "completed": True}}

    # ── complete task (natural language) ────────────────────────
    # Audit finding: phrasings like "complete the scholarship task", "mark the
    # lab record done", "I wrapped up the thesis thing, mark it as done", and
    # "data check is done" fell through to the model, which misrouted them to
    # read-only list_tasks. These patterns resolve them offline. Deliberately
    # conservative: each requires an explicit completion predicate ("task" at the
    # end, or done/complete/finished/wrapped up), so a creation-style
    # "finish the assignment by Friday" is NEVER turned into a write here. The
    # target is resolved by the executor (exact -> unique partial -> clarify),
    # so an ambiguous or missing task clarifies and mutates nothing.
    def _complete(target):
        target = _clean_body(target or "")
        if not target or re.match(r"^\d+$", target):
            return None
        return {"type": "intent", "intent": "set_task_completed",
                "params": {"task_text": target[:500], "completed": True}}

    m = re.match(r"^i(?:'?m|\s+(?:have\s+|had\s+)?)?(?:just\s+)?"
                 r"(?:wrapped up|finished up|finished|completed|done with|got|knocked out)\s+"
                 r"(?:the\s+|my\s+|that\s+|this\s+)?([^,.!?]+)", t, re.IGNORECASE)
    if m:
        r = _complete(m.group(1))
        if r:
            return r
    m = re.match(r"^(?:complete|finish|close|mark|check\s+off|tick\s+off|check)\s+"
                 r"(?:the\s+|my\s+|this\s+|that\s+)?(.+?)\s+task(?:\s+(?:as\s+)?(?:done|complete|completed|finished))?[.!?]*$",
                 t, re.IGNORECASE)
    if m:
        r = _complete(m.group(1))
        if r:
            return r
    m = re.match(r"^(?:mark|make|set)\s+(?:the\s+|my\s+)?([^,.!?]+?)\s+(?:task\s+)?(?:as\s+)?"
                 r"(?:done|complete|completed|finished)[.!?]*$", t, re.IGNORECASE)
    if m:
        r = _complete(m.group(1))
        if r:
            return r
    m = re.match(r"^([^,.!?]+?)\s+(?:task\s+)?(?:is|was|’?s)\s+(?:all\s+|now\s+)?"
                 r"(?:done|complete|completed|finished)"
                 r"(?:[,.]?\s*(?:and\s+)?(?:so\s+)?"
                 r"(?:tick|mark|check|cross|knock)\s+it(?:\s+off)?\s*(?:as\s+done)?)?"
                 r"[.!?]*$", t, re.IGNORECASE)
    if m:
        r = _complete(m.group(1))
        if r:
            return r
    # "done, tick it off" without a copula (e.g. "the report done, mark it")
    m = re.match(r"^(?:complete|finish|check\s+off|tick\s+off|cross\s+off|mark\s+done)\s+"
                 r"(?:the\s+|my\s+|this\s+|that\s+)?([^,.!?]+?)\s*,?\s*"
                 r"(?:tick|mark|check|cross)\s+it(?:\s+off)?[.!?]*$", t, re.IGNORECASE)
    if m:
        r = _complete(m.group(1))
        if r:
            return r


    # ── timers ──────────────────────────────────────────────────
    dur_text, label = None, ""
    m = re.match(r"^(?:set|start|create)\s+(?:an?\s+)?timer\s+(?:for\s+)?(.+?)[.!?]*$", t, re.IGNORECASE)
    if m:
        dur_text = m.group(1)
    else:
        m = re.match(r"^timer\s+(?:for\s+)?(.+?)[.!?]*$", t, re.IGNORECASE)
        if m:
            dur_text = m.group(1)
        else:
            m = re.match(r"^(?:set|start|create)\s+(?:an?\s+)?(.+?)\s+timer(?:\s+(?:for|named|called)\s+(.+?))?[.!?]*$", t, re.IGNORECASE)
            if m:
                dur_text, label = m.group(1), (m.group(2) or "").strip()
    if dur_text is not None:
        try:
            seconds = timeutil.parse_duration_seconds(dur_text)
        except ValueError as e:
            return {"type": "clarification", "question": str(e)}
        if seconds is None:
            return {"type": "clarification",
                    "question": "How long should the timer be? For example: 'Set a timer for 25 minutes'."}
        return {"type": "intent", "intent": "start_timer",
                "params": {"duration_seconds": seconds, "label": label[:300]}}

    m = re.match(r"^(?:cancel|stop|delete|kill)\s+(?:my\s+|the\s+)?(?:timer|countdown)\s*" + _TASK_REF_NUM + r"?[.!?]*$", low)
    if m:
        timer_id = int(m.group(1)) if m.group(1) else None
        return {"type": "intent", "intent": "cancel_timer", "params": {"timer_id": timer_id}}

    # ── reminders ───────────────────────────────────────────────
    m = re.match(r"^(?:remind\s+me|set\s+(?:a\s+)?reminder|create\s+(?:a\s+)?reminder|add\s+(?:a\s+)?reminder)(.+?)[.!?]*$", t, re.IGNORECASE)
    if m:
        body = m.group(1).strip()
        when = timeutil.extract_when(body, clock, context="reminder")
        if when.needs_clarification:
            return {"type": "clarification", "question": when.question}
        if not when.ok:
            return {"type": "clarification",
                    "question": "When should I remind you? For example: 'Remind me tomorrow at 7 PM to revise trees'."}
        body = _strip_when(body, when)
        body = re.sub(r"^(?:to|that|i\s+should|should|about)\s+", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"^(?:remind\s+me)\s+", "", body, flags=re.IGNORECASE).strip()
        if not body:
            return {"type": "clarification", "question": "What should the reminder say?"}
        return {"type": "intent", "intent": "create_reminder",
                "params": {"text": body[:500], "due_utc": when.utc_dt}}

    m = re.match(r"^snooze\s+(?:that|the|my|this)?\s*(?:reminder)?\s*" + _TASK_REF_NUM + r"?\s*(?:for\s+(.+?))?[.!?]*$", low)
    if m and ("snooze" in low):
        try:
            seconds = timeutil.parse_duration_seconds(m.group(2) or "") if m.group(2) else None
        except ValueError as e:
            return {"type": "clarification", "question": str(e)}
        if m.group(2) and seconds is None:
            return {"type": "clarification",
                    "question": "How long should I snooze it? For example: 'Snooze that reminder for 10 minutes'."}
        return {"type": "intent", "intent": "snooze_reminder",
                "params": {"reminder_id": int(m.group(1)) if m.group(1) else None,
                           "snooze_seconds": seconds or 600}}

    # ── focus sessions ──────────────────────────────────────────
    focus_dur, focus_obj, is_focus = "", "", False
    m = re.match(r"^(?:start|begin)\s+(?:an?\s+)?(?:(.+?)\s+)?focus\s+session(?:\s+(?:for|on|to|named)\s+(.+?))?[.!?]*$", t, re.IGNORECASE)
    if m:
        is_focus = True
        focus_dur, focus_obj = (m.group(1) or "").strip(), (m.group(2) or "").strip()
    else:
        m = re.match(r"^(?:i\s+want\s+to\s+)?focus\s+(?:for\s+(.+?))?(?:\s+on\s+(.+?))?[.!?]*$", t, re.IGNORECASE)
        if m and not re.match(r"^(?:cancel|stop|end|pause)\b", low):
            is_focus = True
            focus_dur, focus_obj = (m.group(1) or "").strip(), (m.group(2) or "").strip()
        else:
            # "start a pomodoro of twenty five minutes" / "do a pomodoro on chapter 3"
            m = re.match(r"^(?:start|begin|do|run|set|set\s+up)\s+(?:an?\s+)?pomodoro\b"
                         r"(?:\s+(?:of|for)\s+(.+?))?(?:\s+on\s+(.+?))?[.!?]*$", t, re.IGNORECASE)
            if m:
                is_focus = True
                focus_dur, focus_obj = (m.group(1) or "").strip(), (m.group(2) or "").strip()
    if is_focus:
        seconds = None
        if focus_dur:
            try:
                seconds = timeutil.parse_duration_seconds(focus_dur)
            except ValueError as e:
                return {"type": "clarification", "question": str(e)}
        if seconds is None:
            seconds = 25 * 60  # documented default: 25-minute pomodoro
            if focus_dur and not focus_obj:
                focus_obj = focus_dur
        return {"type": "intent", "intent": "start_focus_session",
                "params": {"duration_seconds": seconds, "objective": focus_obj[:300]}}

    m = re.match(r"^(?:end|stop|finish|complete)\s+(?:my\s+|the\s+)?focus\s+session(?:\s*[;,.]\s*(.+?)|\s+(?:i\s+finished|i\s+completed|note[:\s])\s*(.+?))?[.!?]*$", t, re.IGNORECASE)
    if m:
        note = (m.group(1) or m.group(2) or "").strip()
        return {"type": "intent", "intent": "end_focus_session", "params": {"outcome_note": note[:1000]}}

    # ── P2 workspaces / projects / session closure (deterministic, offline) ──
    # These work with Ollama and Gemini fully offline; the UI also calls the
    # same intents directly. Registration/resume are reversible and inspectable.
    m = re.match(r"^(?:register|add|create|track|start tracking)\s+(?:a\s+|new\s+|the\s+)?"
                 r"(?:(academic|personal)\s+)?(?:project|workspace|repo)(?:\s+(?:called|named|as|:)\s+(.+?))?[.!?]*$",
                 t, re.IGNORECASE)
    if m:
        rest = (m.group(2) or "").strip()
        wtype = (m.group(1) or "").lower()

        def _grab(s, kw):
            mm = re.search(rf"\b{kw}\b[:\s]+(.+?)(?=\s+(?:with|goal|next action|and|that|in|at|path)\b|$)",
                           s, re.IGNORECASE)
            if mm:
                return mm.group(1).strip(), (s[:mm.start()] + " " + s[mm.end():])
            return None, s

        tm = re.search(r"\((academic|personal)\)", rest, re.IGNORECASE)
        if tm:
            wtype = tm.group(1).lower()
            rest = rest[:tm.start()] + " " + rest[tm.end():]
        wtype = wtype or "personal"
        next_action, rest = _grab(rest, "next action")
        goal, rest = _grab(rest, "goal")
        pm = re.search(r"\b(?:in|at|path)\b[:\s]+([A-Za-z]:\\[^\s,]+|/[^\s,]+)", rest)
        path = pm.group(1).strip() if pm else None
        if pm:
            rest = rest[:pm.start()] + " " + rest[pm.end():]
        name = re.sub(r"\s+(?:with|and|that)\s*$", "", rest).strip(" ,:.\t")
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            return {"type": "clarification",
                    "question": "What should the project be called? For example: 'Register a project called Thesis (academic) with next action write the intro'."}
        p = {"name": name[:200], "type": wtype}
        if goal:
            p["goal"] = goal[:500]
        if next_action:
            p["next_action"] = next_action[:500]
        if path:
            p["path"] = path[:600]
        return {"type": "intent", "intent": "register_workspace", "params": p}

    m = re.match(r"^(?:resume|continue|re-?open|get back to)\s+"
                 r"(?:(?:my\s+)?(?:work|project|progress)|where\s+i\s+left\s+off)$", low)
    if m:
        return {"type": "intent", "intent": "resume_workspace", "params": {}}
    m = re.match(r"^(?:resume|continue|get back to)\s+(?:(?:the\s+|my\s+)?(?:project|workspace)?\s*(.+?))$", t, re.IGNORECASE)
    if m and (m.group(1) or "").strip():
        return {"type": "intent", "intent": "resume_workspace",
                "params": {"name": _clean_body(m.group(1))[:200]}}

    m = re.match(r"^(?:set|update|change)\s+next action\s+(?:for\s+(?:(?:the\s+|my\s+)?project\s*)?([^:]*?)\s*[:\-]\s*(.+)|[:\-]\s*(.+))$", t, re.IGNORECASE)
    if m:
        proj = (m.group(1) or "").strip()
        action = (m.group(2) or m.group(3) or "").strip()
        if not action:
            return None
        p = {"next_action": action[:500]}
        if proj:
            p["name"] = proj[:200]
        return {"type": "intent", "intent": "update_workspace", "params": p}

    m = re.match(r"^(?:add|create|save|log|write)\s+(?:a\s+)?session note[:\s]+(.+?)[.!?]*$", t, re.IGNORECASE)
    if m:
        return {"type": "intent", "intent": "add_session_note",
                "params": {"outcome": m.group(1).strip()[:2000]}}

    if re.match(r"^(?:i'?m\s+done for (?:the\s+)?(?:day|today)|done for (?:the\s+)?day|"
                r"wrap(?:ping|s)? up (?:the\s+)?(?:day|today)|that'?s (?:all )?(?:for )?(?:today|the day))$", low):
        return {"type": "intent", "intent": "build_session_draft", "params": {}}

    if re.match(r"^(?:what'?s on (?:my )?(?:plate for )?today|show (?:my )?today|"
                r"today(?:'s)? (?:briefing|view|summary)|what does my day look like)$", low):
        return {"type": "intent", "intent": "get_today", "params": {}}

    if re.match(r"^(?:list|show)\s+(?:my\s+|all\s+)?(?:the\s+)?"
                r"(?:projects|workspaces|repos)\??$", low):
        return {"type": "intent", "intent": "list_workspaces", "params": {}}

    m = re.match(r"^link\s+task\s+#?\s*(\d+)\s+to\s+(?:(?:the\s+|my\s+)?(?:project|workspace)?\s*(.+?))$", t, re.IGNORECASE)
    if m and (m.group(2) or "").strip():
        return {"type": "intent", "intent": "link_task_to_workspace",
                "params": {"task_id": int(m.group(1)), "name": _clean_body(m.group(2))[:200]}}

    # ── Stage 3: academic coursework (deterministic, offline) ────────────
    # "add <kind> <title> [due <when>] [for <subject>] [about <est>]"
    m = re.match(r"^(?:add|create|log|note|new)\s+(?:(?:an?|the|my)\s+)?"
                 r"(assignment|exam|lab|test|quiz|reading|project|homework|essay|tutorial|problem\s*set|pset)\b[:\s\"']+"
                 r"(.+)$", t, re.IGNORECASE)
    if m:
        kind = re.sub(r"\s+", " ", m.group(1).lower().strip())
        kind = {"test": "exam", "quiz": "exam", "homework": "assignment",
                "essay": "assignment", "tutorial": "assignment",
                "problem set": "assignment", "pset": "assignment"}.get(kind, kind)
        if kind not in ("assignment", "exam", "lab", "reading", "project"):
            kind = "assignment"

        def _cut(s, pattern):
            mm = re.search(pattern, s, re.IGNORECASE)
            if not mm:
                return None, s
            return mm.group(1).strip(), (s[:mm.start()] + " " + s[mm.end():])

        body = m.group(2).strip().strip('"\'')
        due, body = _cut(body, r"\bdue\s+(?:date\s+)?:?\s*(.+?)(?=\s+\b(?:for|about|approx|takes|~)\b|$)")
        subj, body = _cut(body, r"\bfor\s+(?:the\s+|my\s+)?(?:subject|class|course)?\s*(.+?)(?=\s+\b(?:due|about|takes|~)\b|$)")
        est, body = _cut(body, r"\b(?:about|approx(?:imately)?|roughly|takes?(?:\s+me)?|~|est(?:imated)?(?:\s+to\s+be)?|should\s+take)\s+(.+?)(?=\s+\b(?:due|for)\b|$)")
        title = re.sub(r"[,;:\s]+$", "", body).strip(" ,:-\t")
        title = re.sub(r"\s+", " ", title).strip().strip('"\'').strip()
        # A bare when-phrase left in the title ("DMS mid-term next wednesday")
        # is the due date, not part of the item's name.
        if not due:
            when = timeutil.extract_when(title, clock, context="deadline")
            if when.ok:
                due = when.matched_text
                title = _strip_when(title, when).strip().strip('"\'').strip()
        if not title:
            return {"type": "clarification",
                    "question": "What is it called? e.g. 'add assignment calculus problem set due friday about 90 minutes'."}
        p = {"title": title[:300], "kind": kind}
        if due:
            p["due"] = due[:120]
        if subj:
            p["subject"] = _clean_body(subj)[:200]
        if est:
            p["effort_text"] = est[:120]
        return {"type": "intent", "intent": "add_coursework", "params": p}

    # "list/show my coursework|assignments|exams" and "what's due (for class)?"
    if re.match(r"^(?:list|show)\s+(?:my\s+|all\s+)?(?:open\s+|pending\s+)?"
                r"(?:coursework|assignments?|exams?|labs?|readings?)\??$", low) \
            or re.match(r"^what(?:'s| is)?\s+due\b", low):
        return {"type": "intent", "intent": "list_coursework", "params": {}}

    # "I have 25 minutes" / "give me something to do in 30 minutes" /
    # "what should I work on?" → study suggestion (default 25).
    m = re.match(r"^(?:i\s+(?:have|have\s+only|'ve\s+got|got|only\s+have)|"
                 r"(?:give|show)\s+me\s+something\s+to\s+do\s+in|what\s+can\s+i\s+do\s+in)\s+(.+)$", low)
    if m:
        secs = timeutil.parse_duration_seconds(m.group(1))
        if secs and secs >= 300:
            return {"type": "intent", "intent": "suggest_study",
                    "params": {"minutes": max(5, int(round(secs / 60)))}}
    if re.match(r"^(?:what\s+should\s+i\s+(?:work\s+on|do|study)(?:\s+first)?|"
                r"(?:which\s+assignment|what)\s+should\s+i\s+tackle)\??$", low):
        return {"type": "intent", "intent": "suggest_study", "params": {"minutes": 25}}

    # ── AI Radar (deterministic, offline reads from stored research) ──────
    # Refresh/run an AI Radar pass.
    if (re.search(r"\b(?:refresh|run|update|re-?run|check)\b", low)
            and re.search(r"\b(?:ai\s+radar|radar|ai\s+news|ai\s+digest)\b", low)):
        return {"type": "intent", "intent": "refresh_ai_radar", "params": {}}
    # "Any new free models or API credits?" -> free/credits digest.
    if (re.search(r"\bfree\b", low)
            and re.search(r"\b(?:models?|api|credits?|tokens?)\b", low)
            and re.search(r"\b(?:ai|llm|api|model|credit|token)\b", low)):
        return {"type": "intent", "intent": "get_ai_radar_digest", "params": {"mode": "free"}}
    # "What's new in AI today?" / general AI Radar digest.
    if re.search(r"\b(?:ai\s+radar|ai\s+news|ai\s+digest|whats\s+new\s+in\s+ai|what's\s+new\s+in\s+ai|"
                 r"new\s+in\s+ai|ai\s+releases?|new\s+(?:ai\s+)?models?|model\s+releases?)\b", low):
        return {"type": "intent", "intent": "get_ai_radar_digest", "params": {"mode": "general"}}

    return None


# ─────────────────────────────────────────────
# Capability boundary — checked by the executor before it runs anything
# ─────────────────────────────────────────────
# VEGA writes to its own tasks, reminders, timers, focus sessions and
# workspace notes, and to nothing else. A model asked for a calendar entry
# proposes `create_task` because that is the nearest available write (live:
# 'schedule a dentist appointment on my calendar' created Task #1), so the
# user's own words are checked against the tool's real capability first.
#
# Precision rule: an unambiguous external system name ('calendar', 'gmail',
# 'slack', 'jira', …) matches anywhere in the request, while words that are
# also ordinary nouns or verbs ('cal', 'doc', 'sheet', 'ticket', 'chat') match
# only inside a destination frame — a preposition plus up to three descriptor
# words. A request that also names a VEGA-native object is never blocked, so
# 'add a task to book a dentist appointment' and 'remind me to email the
# professor' still work. Favouring false negatives over false positives here is
# deliberate: the cost of a miss is the previous behaviour, the cost of an
# over-block is a working command.
_DESTINATION_FRAME = r"\b(?:on|in|into|onto|to|via|through|at)\b(?:\s+\w+){0,3}\s+"

_EXTERNAL_DESTINATIONS = (
    # 'agenda' is deliberately absent: "what's on my agenda?" is the ordinary
    # way to ask for today's plan, which VEGA can answer.
    ("your calendar", r"\b(?:calendar|planner)\b",
     r"\bcal\b"),
    ("email", r"\b(?:e-?mails?|gmail|inbox|outlook|mail|mailer)\b",
     r""),
    ("a messaging app",
     r"\b(?:whatsapp|discord|telegram|slack|wechat|sms|dms?|group\s+chats?"
     r"|message(?:s|d)?|text(?:s|ing)?|(?:ms|microsoft)\s+teams)\b",
     r"\bchats?\b"),
    ("a spreadsheet", r"\b(?:spreadsheets?|excel|google\s+sheets)\b",
     r"\bsheets?\b"),
    ("a document tool", r"\b(?:notion|confluence|sharepoint|overleaf|obsidian)\b",
     r"\b(?:docs?|documents?)\b"),
    ("an issue tracker",
     r"\b(?:jira|trello|asana|clickup|monday\.com|todoist|github\s+issues?)\b",
     r"\btickets?\b"),
)

# Objects VEGA really has. 'lab' is absent on purpose — it is as often a
# science lab to be scheduled as it is a VEGA coursework kind. 'list' is here
# because "add to my list" names VEGA's own task list as the destination.
_NATIVE_OBJECT = re.compile(
    r"\b(?:tasks?|to-?dos?|lists?|reminders?|remind|timers?|focus|pomodoros?"
    r"|sessions?|notes?|coursework|assignments?|exams?|readings?"
    r"|projects?|workspaces?)\b", re.IGNORECASE)


_OPEN_REQUEST = re.compile(
    r"^(?:open|launch|start|show(?:\s+me)?|display|pull\s+up|get\s+me|"
    r"go\s+to|visit|navigate\s+to|bring\s+up)\b", re.IGNORECASE)


def is_open_request(text):
    """True when the request's own verb is 'bring this up' rather than 'write
    into it'. Opening a site is the right answer to that — and the only one —
    so a calendar *write* request must not be served by a surprise tab."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _OPEN_REQUEST.match(_strip_prefixes(text)[:600]) is not None


def unsupported_destination(text, open_alternative=False):
    """Return {'destination': str, 'message': str} when the request asks VEGA
    to reach a system it has no tool for, else None.

    Deliberately narrow: a request that names no external destination is not
    blocked, so 'schedule a dentist appointment for friday 2 pm' is still
    allowed to be captured as a task with a deadline. That is VEGA's own
    scheduling surface, and the receipt keeps the user's words verbatim.

    ``open_alternative`` is for the launch lane, where declining means a
    desktop action did NOT happen — the refusal says so, and names the request
    that would legitimately get that tab.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip()[:600]
    if _NATIVE_OBJECT.search(t):
        return None
    for label, bare, framed in _EXTERNAL_DESTINATIONS:
        if re.search(bare, t, re.IGNORECASE) or (
                framed and re.search(_DESTINATION_FRAME + framed, t, re.IGNORECASE)):
            message = (
                f"I can't reach {label} — VEGA has no tool for it, and I don't "
                "guess at a nearby action instead. Nothing was created or changed. "
                "What I can do is keep this in your own list: tasks, reminders, "
                'timers, focus sessions and workspace notes. If you want it as a '
                'task after all, say "add a task" followed by what you want done.')
            if open_alternative:
                message += (f" Nothing was opened either — say 'open {label}' and I "
                            "will launch it in your browser instead.")
            return {"destination": label, "message": message}
    return None
