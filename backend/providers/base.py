"""Normalized provider gateway contract for VEGA P1.

Every model backend (Ollama, Gemini, future ones) adapts to this one
interface: normalized text + tool proposals + usage + typed errors. The
chat endpoint and tool registry never import a provider SDK directly, so
swapping models/providers never touches tool handlers or the executor.

Policy rules encoded here:
- At most ONE bounded inference retry, inside a total deadline.
- Retry-After (or provider retry hint) is surfaced, never slept beyond
  the deadline.
- Executed mutations are never retried here — this layer only covers
  inference, and the caller executes actions after a successful return.
- One active generation at a time with a small bounded queue.
- Secrets are redacted from every error message that reaches users/logs.
"""

import os
import re
import threading
import time
from dataclasses import dataclass, field

# Total wall-clock budget for one chat turn's inference (incl. the single
# bounded retry). Kept below the frontend's 90s abort so the user always
# gets our honest message instead of a client-side timeout.
DEFAULT_DEADLINE_S = 75.0

# How long a second request may wait for the single generation slot
# before being told the assistant is busy (bounded queue).
QUEUE_TIMEOUT_S = 10.0

_SECRET_ENV_NAMES = ("GEMINI_API_KEY", "OPENROUTER_KEY", "VEGA_RADAR_OPENROUTER_KEY")


def redact_secrets(text: str) -> str:
    """Remove credential values from any user-visible/logged error text."""
    out = text or ""
    for env_name in _SECRET_ENV_NAMES:
        value = (os.getenv(env_name) or "").strip()
        if value and len(value) >= 6:
            out = out.replace(value, f"<{env_name} redacted>")
    # Generic patterns: Authorization headers, key=value query params.
    out = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", out)
    out = re.sub(r"(?i)\b((?:api[_-]?key|key|token)=)[A-Za-z0-9._\-]+", r"\1<redacted>", out)
    return out


# ─────────────────────────────────────────────
# Typed errors
# ─────────────────────────────────────────────

class ProviderError(Exception):
    """Base class. `kind` is the normalized error category."""
    kind = "unavailable"

    def __init__(self, message, retry_after=None):
        super().__init__(redact_secrets(str(message)))
        self.retry_after = retry_after  # seconds (float) if the provider hinted one

    @property
    def user_message(self):
        return str(self)


class ProviderUnavailable(ProviderError):
    kind = "unavailable"


class ProviderTimeout(ProviderError):
    kind = "timeout"


class ProviderAuth(ProviderError):
    kind = "auth"


class ProviderQuota(ProviderError):
    """429 / quota exhausted. retry_after may carry the server's hint.
    Unknown remaining quota stays unknown — never reported as 0 or unlimited."""
    kind = "quota"


class ProviderContextLimit(ProviderError):
    kind = "context_limit"


class ProviderMalformed(ProviderError):
    kind = "malformed"


# Transient kinds eligible for the single bounded retry. Auth/quota/
# malformed/context-limit are terminal within one turn.
TRANSIENT_KINDS = ("timeout", "unavailable")


# ─────────────────────────────────────────────
# Normalized result
# ─────────────────────────────────────────────

@dataclass
class ToolProposal:
    """One proposed tool call, already shaped as (name, raw_args dict).
    Validation happens in tool_registry — a proposal is never executed as-is."""
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class ProviderResponse:
    text: str = ""
    proposals: list = field(default_factory=list)   # list[ToolProposal]
    # Token usage; None means the runtime did not report it (unknown —
    # never displayed as 0 or unlimited).
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = ""


# ─────────────────────────────────────────────
# Capabilities (explicit flags — never inferred from a model's name)
# ─────────────────────────────────────────────

@dataclass
class Capabilities:
    local: bool = False             # runs on this machine (no data leaves it)
    tools: bool = False             # runtime supports tool/function calling
    structured_output: bool = False
    vision: bool = False
    context_window: int = 8192      # configured input context budget (tokens)
    reserved_output: int = 1024     # output space kept out of the input budget
    data_policy: str = "local_only"  # "local_only" | "user_configured_cloud"


class BaseProvider:
    """Adapter contract. Subclasses implement `_call` only; retry policy,
    the single-generation gate, and secret redaction live here."""

    name = "base"

    def __init__(self, capabilities: Capabilities):
        self.capabilities = capabilities

    def _call(self, messages, tools, timeout_s) -> ProviderResponse:
        raise NotImplementedError

    # Single active generation with a bounded wait (module-wide: one model
    # turn at a time regardless of provider, matching the resource policy).
    _slot = threading.Semaphore(1)

    def generate(self, messages, tools=None, deadline=None) -> ProviderResponse:
        deadline = deadline if deadline is not None else (time.monotonic() + DEFAULT_DEADLINE_S)
        # Module-level lookup so the bounded-wait policy is test-tunable.
        queue_timeout = globals().get("QUEUE_TIMEOUT_S", QUEUE_TIMEOUT_S)
        if not self._slot.acquire(timeout=queue_timeout):
            raise ProviderUnavailable(
                "VEGA is still working on another request. Please wait a moment and try again.")
        try:
            return self._generate_bounded(messages, tools or [], deadline)
        finally:
            self._slot.release()

    def _generate_bounded(self, messages, tools, deadline):
        """One attempt, plus at most one retry for transient failures while
        time remains. Never waits past the deadline for a retry hint."""
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderTimeout("The model took too long to answer. Your tasks and history are unchanged.")
            try:
                return self._call(messages, tools, remaining)
            except ProviderError as e:
                terminal = e.kind not in TRANSIENT_KINDS or attempt >= 1
                if terminal:
                    raise
                wait = 0.0
                if e.retry_after is not None:
                    if e.retry_after > max(0.0, deadline - time.monotonic()):
                        # Honor the hint by surfacing it — not by waiting indefinitely.
                        e.retry_after = round(e.retry_after, 1)
                        raise
                    wait = e.retry_after
                if time.monotonic() + wait >= deadline:
                    raise ProviderTimeout(
                        "The model took too long to answer. Your tasks and history are unchanged.")
                attempt += 1
                if wait > 0:
                    time.sleep(wait)
