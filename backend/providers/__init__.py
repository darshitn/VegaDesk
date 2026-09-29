"""Provider gateway package: one normalized interface over model backends."""

try:
    from providers.base import (
        ProviderError, ProviderUnavailable, ProviderTimeout, ProviderAuth,
        ProviderQuota, ProviderContextLimit, ProviderMalformed,
        ProviderResponse, ToolProposal, Capabilities, BaseProvider,
        redact_secrets, DEFAULT_DEADLINE_S, QUEUE_TIMEOUT_S)
    from providers.ollama import OllamaProvider
    from providers.gemini import GeminiProvider
except ImportError:
    from .base import (
        ProviderError, ProviderUnavailable, ProviderTimeout, ProviderAuth,
        ProviderQuota, ProviderContextLimit, ProviderMalformed,
        ProviderResponse, ToolProposal, Capabilities, BaseProvider,
        redact_secrets, DEFAULT_DEADLINE_S, QUEUE_TIMEOUT_S)
    from .ollama import OllamaProvider
    from .gemini import GeminiProvider

__all__ = [
    "ProviderError", "ProviderUnavailable", "ProviderTimeout", "ProviderAuth",
    "ProviderQuota", "ProviderContextLimit", "ProviderMalformed",
    "ProviderResponse", "ToolProposal", "Capabilities", "BaseProvider",
    "OllamaProvider", "GeminiProvider",
    "redact_secrets", "DEFAULT_DEADLINE_S", "QUEUE_TIMEOUT_S",
]
