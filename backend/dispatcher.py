"""Command dispatcher: the single entry point for typed commands and wake-word
transcripts. Parses deterministically, executes via tools.execute_intent, and
returns a structured result. Anything unmatched falls through to the existing
open_app/open_website fast path and then to the selected model in main.py.
"""

try:
    from command_parser import parse_command
    import tools
except ImportError:
    from .command_parser import parse_command
    from . import tools


def dispatch_command(db, clock, text, source="typed", idempotency_key=None):
    """Returns:
      {'handled': False}                            -> not a productivity command
      {'handled': True, 'response': str, 'clarification': bool, 'receipt': dict|None}
    """
    parsed = parse_command(text, clock)
    if parsed is None:
        return {"handled": False}
    if parsed["type"] == "clarification":
        return {"handled": True, "response": parsed["question"],
                "clarification": True, "receipt": None}

    try:
        receipt = tools.execute_intent(
            db, clock, parsed["intent"], parsed["params"],
            source=source, idempotency_key=idempotency_key, command_text=text)
    except tools.ToolError as exc:
        return {"handled": True, "response": str(exc),
                "clarification": True, "receipt": None}

    return {"handled": True,
            "response": receipt["message"],
            "clarification": bool(receipt.get("clarification")),
            "receipt": receipt}
