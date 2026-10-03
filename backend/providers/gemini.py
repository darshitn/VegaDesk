"""Gemini adapter for the P1 provider gateway.

Cloud runtime: only used because the user configured it (LLM_PROVIDER /
the in-app provider setting). The context builder decides what may be
sent; this adapter never adds local database content on its own. The API
key is read from configuration and redacted from every error path.
"""

import os

try:
    from providers.base import (
        BaseProvider, Capabilities, ProviderResponse, ToolProposal,
        ProviderUnavailable, ProviderTimeout, ProviderAuth, ProviderQuota,
        ProviderContextLimit, ProviderMalformed)
except ImportError:
    from .base import (
        BaseProvider, Capabilities, ProviderResponse, ToolProposal,
        ProviderUnavailable, ProviderTimeout, ProviderAuth, ProviderQuota,
        ProviderContextLimit, ProviderMalformed)


def _env_int(name, default):
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _classify(exc):
    """Map a google.genai exception onto the normalized error taxonomy using
    its status code when present, else its message. Never leaks the key."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    text = str(exc)
    low = text.lower()
    if code == 429 or "resource has been exhausted" in low or "quota" in low or "rate limit" in low:
        retry_after = None
        headers = getattr(exc, "headers", None) or {}
        try:
            retry_after = float(headers.get("retry-after") or headers.get("Retry-After") or 0) or None
        except (ValueError, AttributeError):
            retry_after = None
        return ProviderQuota("Gemini quota or rate limit reached. Remaining quota is unknown.",
                             retry_after=retry_after)
    if code in (400, 401) or "api key not valid" in low or "permission denied" in low or code == 403:
        return ProviderAuth("Gemini rejected the API key or permission was denied. Check GEMINI_API_KEY.")
    if "deadline" in low or "timed out" in low or "timeout" in low:
        return ProviderTimeout("Gemini request timed out.")
    if "exceeds" in low and "context" in low or code == 413:
        return ProviderContextLimit("Request is too large for the configured Gemini model.")
    if code is not None and code >= 500 or "internal error" in low or "unavailable" in low:
        return ProviderUnavailable(f"Gemini service error: {text[:200]}")
    return ProviderUnavailable(f"Gemini request failed: {text[:200]}")


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, model=None, api_key=None, context_window=None, reserved_output=None):
        self.model = (model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")
        caps = Capabilities(
            local=False,
            tools=True,
            structured_output=True,
            vision=False,             # vision stays out of the default command lane
            context_window=context_window or _env_int("GEMINI_CONTEXT_WINDOW", 32768),
            reserved_output=reserved_output or _env_int("GEMINI_RESERVED_OUTPUT", 2048),
            data_policy="user_configured_cloud",
        )
        super().__init__(caps)

    def _client(self, http_options=None):
        if not (self._api_key or "").strip():
            raise ProviderUnavailable(
                "GEMINI_API_KEY is not configured. Set it in backend/.env or switch the provider to 'ollama'.")
        from google import genai
        return genai.Client(api_key=self._api_key, http_options=http_options)

    def _call(self, messages, tools, timeout_s) -> ProviderResponse:
        from google.genai import types

        system_text = ""
        contents = []
        for m in messages:
            if m.get("role") == "system":
                system_text = m.get("content") or ""
                continue
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content") or ""}]})

        gemini_tools = None
        if tools:
            gemini_tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["function"]["name"],
                    description=t["function"].get("description", ""),
                    parameters=t["function"].get("parameters") or None,
                ) for t in tools
            ])]

        timeout_ms = max(100, int(timeout_s * 1000))
        http_options = types.HttpOptions(timeout=timeout_ms)
        try:
            client = self._client(http_options=http_options)
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_text or None,
                    tools=gemini_tools,
                    max_output_tokens=self.capabilities.reserved_output,
                    http_options=http_options,
                ),
            )
        except ProviderUnavailable:
            raise
        except Exception as e:
            raise _classify(e)

        proposals = []
        for fc in (getattr(response, "function_calls", None) or []):
            name = getattr(fc, "name", None)
            args = getattr(fc, "args", None)
            if not isinstance(name, str) or not name.strip():
                continue
            proposals.append(ToolProposal(name=name.strip(),
                                          args=dict(args) if isinstance(args, dict) else {}))

        text = getattr(response, "text", None)
        if not text and not proposals:
            # Empty text with no tool call: usually a safety-filter block.
            raise ProviderMalformed(
                "Gemini returned no usable response (possibly blocked by safety filters).")

        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            text=text or "",
            proposals=proposals,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            completion_tokens=getattr(usage, "candidates_token_count", None),
            model=self.model,
        )
