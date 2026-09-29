"""Ollama adapter for the P1 provider gateway.

Local runtime: no data leaves this machine. Tool-call support is a runtime
capability that is probed from the actual response shape — never assumed
from the model's name. Model id, context budget, and reserved output are
configuration (env), not code edits.
"""

import json
import os

import requests

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


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, model=None, base_url=None, context_window=None, reserved_output=None):
        self.model = (model or os.getenv("OLLAMA_MODEL", "llama3")).strip()
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        caps = Capabilities(
            local=True,
            tools=True,               # Ollama runtime supports tool calling; the
            structured_output=False,  # response shape is still validated strictly.
            vision=False,
            context_window=context_window or _env_int("OLLAMA_CONTEXT_WINDOW", 8192),
            reserved_output=reserved_output or _env_int("OLLAMA_RESERVED_OUTPUT", 1024),
            data_policy="local_only",
        )
        super().__init__(caps)

    def _call(self, messages, tools, timeout_s) -> ProviderResponse:
        # temperature 0 for the command lane: tool extraction must be
        # reproducible, not creative. Configurable via OLLAMA_TEMPERATURE.
        try:
            temperature = float((os.getenv("OLLAMA_TEMPERATURE") or "0").strip())
        except ValueError:
            temperature = 0.0
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": self.capabilities.reserved_output,
                        "temperature": temperature},
        }
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload,
                                 timeout=min(timeout_s, 60.0))
        except requests.exceptions.Timeout:
            raise ProviderTimeout(f"Local model '{self.model}' timed out. Try a shorter request.")
        except requests.exceptions.ConnectionError:
            raise ProviderUnavailable(
                "Ollama is not reachable. Is it running? (OLLAMA_BASE_URL in backend/.env)")

        if resp.status_code in (401, 403):
            raise ProviderAuth(f"Ollama rejected the request (HTTP {resp.status_code}).")
        if resp.status_code == 429:
            retry_after = None
            try:
                retry_after = float(resp.headers.get("Retry-After") or 0) or None
            except ValueError:
                retry_after = None
            raise ProviderQuota("Ollama is rate-limiting requests.", retry_after=retry_after)
        if resp.status_code == 404:
            # Model not installed. We never auto-download models.
            raise ProviderUnavailable(
                f"Model '{self.model}' is not installed in Ollama. "
                f"Install it yourself or set OLLAMA_MODEL in backend/.env to an installed model.")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"Ollama server error (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            body = (resp.text or "")[:300]
            if "context" in body.lower():
                raise ProviderContextLimit(f"Request is too large for '{self.model}': {body}")
            raise ProviderMalformed(f"Ollama rejected the request (HTTP {resp.status_code}): {body}")

        try:
            data = resp.json()
        except ValueError:
            raise ProviderMalformed("Ollama returned a non-JSON response.")
        if not isinstance(data, dict) or "message" not in data:
            raise ProviderMalformed("Ollama response is missing its message payload.")

        msg = data.get("message") or {}
        text = msg.get("content") or ""
        proposals = []
        for tc in (msg.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}   # registry validation will reject it honestly
            if not isinstance(args, dict):
                args = {}
            if isinstance(fn.get("name"), str) and fn["name"].strip():
                proposals.append(ToolProposal(name=fn["name"].strip(), args=args))

        return ProviderResponse(
            text=text if isinstance(text, str) else "",
            proposals=proposals,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            model=data.get("model") or self.model,
        )
