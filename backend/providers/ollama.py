"""Ollama adapter for the P1 provider gateway.

Local runtime: no data leaves this machine. Tool-call support is a runtime
capability that is probed from the actual response shape — never assumed
from the model's name. Model id, context budget, and reserved output are
configuration (env), not code edits.

Security & Local Isolation Invariant:
Only loopback addresses (localhost, 127.0.0.1, ::1) are permitted for OLLAMA_BASE_URL.
Remote, LAN, or cloud metadata endpoints are strictly rejected to prevent SSRF and data exfiltration.
"""

import ipaddress
import json
import os
import urllib.parse

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


def is_loopback_host(hostname: str) -> bool:
    """Return True if hostname is a valid local loopback identifier."""
    if not hostname:
        return False
    h = hostname.lower().strip()
    if h in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return True
    clean_ip = h.strip("[]")
    try:
        ip = ipaddress.ip_address(clean_ip)
        return ip.is_loopback
    except ValueError:
        return False


def validate_loopback_url(url: str) -> str:
    """Validate that url uses http/https and targets a local loopback interface.

    Raises ValueError if scheme is invalid or host is not loopback.
    """
    if not url or not isinstance(url, str):
        url = "http://localhost:11434"
    cleaned = url.strip()
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Ollama base URL must use http or https scheme, got '{parsed.scheme or 'none'}'")
    hostname = parsed.hostname
    if not hostname or not is_loopback_host(hostname):
        raise ValueError(
            f"Ollama base URL must be a local loopback address (e.g. http://localhost:11434). "
            f"Non-loopback host '{hostname}' is rejected for local isolation and security."
        )
    return cleaned.rstrip("/")


def check_ollama_readiness(base_url: str = None, model: str = None, timeout_s: float = 1.5) -> dict:
    """Probes local Ollama /api/tags without downloading any models or generating tokens.

    Returns structured status:
      - ready: service online and model is installed
      - missing_model: service online but model tag not found
      - unavailable: service offline, unreachable, or endpoint invalid
    """
    url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    target_model = (model or os.getenv("OLLAMA_MODEL", "llama3")).strip()
    try:
        valid_url = validate_loopback_url(url)
    except ValueError as e:
        return {
            "ready": False,
            "status": "unavailable",
            "model": target_model,
            "error": str(e),
            "models_available": [],
        }

    try:
        resp = requests.get(f"{valid_url}/api/tags", timeout=max(0.2, timeout_s))
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            # Match exact name or prefix (e.g. 'llama3:latest' matches 'llama3')
            model_found = any(
                target_model == m or m.startswith(f"{target_model}:")
                for m in models
            )
            if model_found:
                return {
                    "ready": True,
                    "status": "ready",
                    "model": target_model,
                    "error": None,
                    "models_available": models,
                }
            else:
                return {
                    "ready": False,
                    "status": "missing_model",
                    "model": target_model,
                    "error": (
                        f"Model '{target_model}' not found in local Ollama tags. "
                        f"Install it with 'ollama pull {target_model}' or configure an installed model."
                    ),
                    "models_available": models,
                }
        else:
            return {
                "ready": False,
                "status": "unavailable",
                "model": target_model,
                "error": f"Ollama HTTP {resp.status_code}",
                "models_available": [],
            }
    except requests.exceptions.Timeout:
        return {
            "ready": False,
            "status": "unavailable",
            "model": target_model,
            "error": f"Ollama check timed out after {timeout_s}s on {valid_url}.",
            "models_available": [],
        }
    except requests.exceptions.ConnectionError:
        return {
            "ready": False,
            "status": "unavailable",
            "model": target_model,
            "error": f"Ollama service is not reachable on {valid_url}. Ensure Ollama is running.",
            "models_available": [],
        }
    except Exception as e:
        return {
            "ready": False,
            "status": "unavailable",
            "model": target_model,
            "error": f"Ollama probe error: {str(e)}",
            "models_available": [],
        }


def _env_int(name, default):
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, model=None, base_url=None, context_window=None, reserved_output=None):
        raw_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        try:
            self.base_url = validate_loopback_url(raw_url)
        except ValueError as e:
            raise ProviderUnavailable(str(e))

        self.model = (model or os.getenv("OLLAMA_MODEL", "llama3")).strip()
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

        bounded_timeout = min(max(timeout_s, 1.0), 30.0)
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload,
                                 timeout=bounded_timeout)
        except requests.exceptions.Timeout:
            raise ProviderTimeout(f"Local model '{self.model}' timed out after {bounded_timeout:.1f}s. Try a shorter request.")
        except requests.exceptions.ConnectionError:
            raise ProviderUnavailable(
                f"Ollama is not reachable on {self.base_url}. Ensure the local Ollama service is running.")

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
                f"Install it yourself with 'ollama pull {self.model}' or set OLLAMA_MODEL in backend/.env to an installed model.")
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
