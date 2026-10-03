"""Model lane orchestrator for VEGA P1.

Coordinates: provider gateway -> context builder -> tool registry
validation -> the EXISTING executor. One action per request. The final
user message always derives from the executor's receipt, never from the
model's own claim. Provider failures never touch stored tasks/history and
never block the next deterministic command (that path runs before this
module is reached).

Data policy: only the system prompt, bounded chat history, tool schemas,
and the user's own message go to a provider. Local database content is
never packed into a cloud request — task answers come back through the
locally executed read-only tool.
"""

import re
import time

try:
    import command_parser
    import context_builder
    import tool_registry
    from providers import (
        OllamaProvider, GeminiProvider, ProviderError, ProviderQuota,
        DEFAULT_DEADLINE_S, redact_secrets)
    from providers.base import ToolProposal
except ImportError:
    from . import command_parser
    from . import context_builder
    from . import tool_registry
    from .providers import (
        OllamaProvider, GeminiProvider, ProviderError, ProviderQuota,
        DEFAULT_DEADLINE_S, redact_secrets)
    from .providers.base import ToolProposal

VALID_PROVIDERS = ("ollama", "gemini", "none")

# "create_task": { … } / "start_timer": [ … ] as it appears inside a leaked
# proposal — see _sanitize_text.
_INTENT_KEY_RE = re.compile(r'"(?:%s)"\s*:' % "|".join(
    map(re.escape, tool_registry.model_tool_ids())))

_PROVIDERS = {}


def get_provider(name):
    """Cached provider instances. Model ids/budgets are configuration (env),
    so switching models never edits tool handlers."""
    if name not in _PROVIDERS:
        if name == "ollama":
            _PROVIDERS[name] = OllamaProvider()
        elif name == "gemini":
            _PROVIDERS[name] = GeminiProvider()
        else:
            raise ValueError(name)
    return _PROVIDERS[name]


def reset_providers():
    """Test hook: drop cached instances so env/config changes take effect."""
    _PROVIDERS.clear()


_LOCAL_COMMAND_HINT = ("Your tasks, timers, and reminders still work offline — for example, "
                       "say 'set a timer for 25 minutes' or 'add a task: submit the lab record by friday 6 pm'.")


def _friendly_provider_error(exc):
    kind = getattr(exc, "kind", "unavailable")
    detail = redact_secrets(str(exc))
    if kind == "quota":
        head = "Advanced reasoning is temporarily unavailable (provider quota or rate limit; remaining quota unknown)."
        if getattr(exc, "retry_after", None):
            head += f" The server suggested retrying after {exc.retry_after}s."
    elif kind == "timeout":
        head = "The model took too long to answer."
    elif kind == "auth":
        head = "The model provider rejected its credentials."
    elif kind == "context_limit":
        head = "That request is too large for the configured model."
    elif kind == "malformed":
        head = "The model returned an unusable answer."
    else:
        head = "The model is unavailable right now."
    return f"{head} Nothing was executed or lost. {detail} {_LOCAL_COMMAND_HINT}"


def _sanitize_text(text):
    """Never show (or let TTS read) raw JSON, fenced tool-call blocks, or leaked
    chat-template tokens. Weak local models sometimes emit the tool call as text
    instead of a structured proposal; that is a non-answer, not a reply, and it
    is never executed."""
    t = (text or "").strip()
    if not t:
        return ("I couldn't complete that request. Please try rephrasing it, "
                "or use an exact command like 'set a timer for 25 minutes'.")
    leaked_template = "<|tool_call|>" in t or "<|im_start|>" in t or "<|assistant|>" in t
    fenced_tool = "```" in t and ('"function"' in t or '"name"' in t or '"arguments"' in t
                                  or '"parameters"' in t or '"type"' in t)
    json_blob = t.startswith("{") and (t.endswith("}") or '"function"' in t[:200])
    # A weak model may print the call as prose, e.g. `create_task({"text": ...})`
    # or `[{...}]`. That is a leaked proposal, not an answer, and is never run.
    python_call = re.match(r"^\[?\s*[A-Za-z_]\w*\s*\(\s*\{", t) is not None
    # …and it may wrap the call in a friendly sentence first (live: phi4-mini
    # "Sure, let's take a look…\n\n[{"name":"list_tasks",…}]"). Only an entirely
    # JSON-looking reply would fail the checks above, so scan anywhere in the text.
    embedded_call = bool(re.search(r"[\[{]\s*\"(?:name|function|tool_calls)\"\s*:", t)) and (
        '"arguments"' in t or '"parameters"' in t)
    # …or it keys the call by the INTENT name instead of the envelope, which has
    # none of the quoted keys above (live: phi4-mini 'Sure, I will set a
    # reminder…\n\n```json\n{"create_reminder": {"text": …}}\n```'). Keying this
    # on the registry's own ids keeps it correct as tools are added, and keeps a
    # reply that merely mentions create_task in prose passing through.
    intent_keyed = _INTENT_KEY_RE.search(t) is not None
    if leaked_template or fenced_tool or json_blob or python_call or embedded_call or intent_keyed:
        return ("I couldn't complete that request. Please try rephrasing it, "
                "or use an exact command like 'set a timer for 25 minutes'.")
    return t


def _result(response, mode, receipt=None, clarification=False, error=None):
    """One consistent chat-result shape so callers/tests never KeyError on a
    rejection path. `error` is reserved for contract violations."""
    out = {"response": response, "receipt": receipt,
           "clarification": clarification, "executionMode": mode}
    if error is not None:
        out = {"error": error, "executionMode": mode}
    return out


def run_model_turn(db_factory, clock, message, history, user_name="Sir",
                   provider_name=None, source="chat", idempotency_key=None,
                   deadline=None):
    """Execute one model-assisted chat turn. Always returns a dict with
    'response' (user-facing) and 'executionMode' in
    {'local', 'cloud', 'none'}; 'error' only for contract violations such as
    an unknown provider. Mutations happen exclusively via the registry +
    existing executor after a successful, validated proposal."""
    name = (provider_name or "").strip().lower() or None
    if name is not None and name not in VALID_PROVIDERS:
        return _result(None, "none", error=f"Invalid LLM provider '{name}'. Supported providers are: {VALID_PROVIDERS}")
    if name == "none":
        return _result(
            "VEGA is running in deterministic-only mode (no AI model selected). "
            "Supported offline commands (tasks, timers, reminders, notes, workspaces) still work. "
            "To enable conversational AI, select Ollama (local) or Gemini (cloud) in Settings.",
            "none",
            clarification=True,
        )
    try:
        provider = get_provider(name or "ollama")
    except ValueError:
        return _result(None, "none", error=f"Invalid LLM provider '{name}'. Supported providers are: {VALID_PROVIDERS}")
    mode = "local" if provider.capabilities.local else "cloud"

    # ── Capability boundary, before the provider is consulted ───────────────
    # VEGA has no calendar, email, messaging, spreadsheet, document or issue
    # tracker tool, so a request naming one cannot be fulfilled by ANY proposal.
    # tools.execute_intent already refuses such a write and the launch lane
    # refuses such a tab, but both run after the model has answered — so when
    # the model declined in prose instead of proposing, the user heard the
    # model's wording and when it proposed, they heard VEGA's. Asking the
    # question here makes the explanation the same either way, and the provider
    # is never reached: no inference, no cloud round-trip, nothing to time out.
    # 'open my calendar' is exempt because reaching the destination is the
    # fulfillment the launch lane can actually deliver.
    blocked = command_parser.unsupported_destination(message, True)
    if blocked and not command_parser.is_open_request(message):
        return _result(blocked["message"], "none", clarification=True)

    schema_kind = "gemini" if provider.name == "gemini" else "openai"
    try:
        messages, _meta = context_builder.build_messages(
            clock, history, message, user_name, provider.capabilities, schema_kind)
    except ValueError:
        budget = provider.capabilities.context_window - provider.capabilities.reserved_output
        return _result(
            "That request is too large for the model's context budget. "
            "Please send a shorter request — your history is unchanged. "
            f"(Estimated budget: {budget} tokens, estimated.)",
            "none", clarification=True)

    deadline = deadline if deadline is not None else (time.monotonic() + DEFAULT_DEADLINE_S)
    try:
        result = provider.generate(messages,
                                   tools=tool_registry.schemas_for_provider(schema_kind),
                                   deadline=deadline)
    except ProviderError as e:
        return _result(_friendly_provider_error(e), "none")

    if len(result.proposals) > 1:
        names = ", ".join(p.name for p in result.proposals[:4])
        return _result(
            f"You asked for several actions at once ({names}). "
            "I execute one action per request so nothing half-happens — "
            "which one should I do first?",
            mode, clarification=True)

    if result.proposals:
        if time.monotonic() >= deadline:
            return _result(
                "The request timed out before the proposed action could be executed. Nothing was executed.",
                "none",
            )
        proposal = result.proposals[0]
        try:
            validated = tool_registry.validate_proposal(proposal, clock)
        except tool_registry.ProposalRejected as e:
            return _result(f"{e} Nothing was executed.", mode, clarification=True)

        if time.monotonic() >= deadline:
            return _result(
                "The request timed out before the proposed action could be executed. Nothing was executed.",
                "none",
            )

        entry = validated["entry"]
        if entry.get("produces_receipt"):
            base = (idempotency_key or "").strip()[:80] or tool_registry.request_fingerprint(message, source)
            action_key = f"{base}:{validated['tool']}"[:120]
        else:
            action_key = None  # read-only tools write no receipt by design

        outcome = tool_registry.execute_proposal(
            validated, db_factory, clock, source="model",
            idempotency_key=action_key, command_text=message)
        outcome["executionMode"] = mode
        return outcome

    return _result(_sanitize_text(result.text), mode)
