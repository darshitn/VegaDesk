"""The local Ollama adapter must never follow redirects off loopback."""

from unittest.mock import Mock, patch

import pytest

from providers.base import ProviderUnavailable
from providers.ollama import OllamaProvider, check_ollama_readiness


def test_readiness_refuses_redirect_without_following_it():
    redirected = Mock(status_code=302)
    redirected.headers = {"Location": "https://example.com/api/tags"}
    with patch("providers.ollama.requests.get", return_value=redirected) as get:
        result = check_ollama_readiness("http://localhost:11434", "llama3")
    assert result["status"] == "unavailable"
    assert result["ready"] is False
    assert get.call_args.kwargs["allow_redirects"] is False
    assert get.call_count == 1


def test_inference_refuses_redirect_without_following_it():
    redirected = Mock(status_code=307)
    redirected.headers = {"Location": "https://example.com/api/chat"}
    provider = OllamaProvider(model="llama3", base_url="http://localhost:11434")
    with patch("providers.ollama.requests.post", return_value=redirected) as post:
        with pytest.raises(ProviderUnavailable, match="redirected"):
            provider._call([{"role": "user", "content": "hello"}], [], 2)
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_count == 1
