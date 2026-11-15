"""Provider adapters — stub interfaces for LLM providers.

These adapters provide a unified interface for different LLM providers.
They are intentionally lightweight: Boster core never requires network I/O.
Adapters handle format translation between Boster's internal representation
and provider-specific API formats.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ProviderAdapter:
    """Base adapter for LLM providers."""

    def __init__(self, model: str = "", **kwargs):
        self.model = model
        self.kwargs = kwargs

    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Format messages for provider API call."""
        raise NotImplementedError

    def parse_response(self, response: Any) -> Dict[str, Any]:
        """Parse provider response into standard format."""
        raise NotImplementedError


class OpenAIAdapter(ProviderAdapter):
    """OpenAI-compatible API adapter."""

    def format_messages(self, messages, system_prompt=""):
        formatted = []
        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})
        for msg in messages:
            formatted.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })
        return {"messages": formatted}

    def parse_response(self, response):
        choice = response.get("choices", [{}])[0]
        return {
            "content": choice.get("message", {}).get("content", ""),
            "model": response.get("model", ""),
            "usage": response.get("usage", {}),
        }


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Claude adapter."""

    def format_messages(self, messages, system_prompt=""):
        # Extract system prompt
        system = system_prompt
        user_msgs = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                user_msgs.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })
        return {"messages": user_msgs, "system": system}

    def parse_response(self, response):
        block = response.get("content", [{}])[0]
        return {
            "content": block.get("text", ""),
            "model": response.get("model", ""),
            "usage": response.get("usage", {}),
        }


class OllamaAdapter(ProviderAdapter):
    """Ollama local adapter."""

    def format_messages(self, messages, system_prompt=""):
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[{
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                } for msg in messages],
            ],
        }

    def parse_response(self, response):
        msg = response.get("message", {})
        return {
            "content": msg.get("content", ""),
            "model": response.get("model", ""),
        }


# Adapter registry
_ADAPTERS = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
}


def get_adapter(provider: str, model: str = "", **kwargs) -> ProviderAdapter:
    """Get a provider adapter by name."""
    adapter_cls = _ADAPTERS.get(provider.lower())
    if adapter_cls is None:
        return OpenAIAdapter(model=model, **kwargs)  # fallback
    return adapter_cls(model=model, **kwargs)


__all__ = [
    "ProviderAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "OllamaAdapter",
    "get_adapter",
]
