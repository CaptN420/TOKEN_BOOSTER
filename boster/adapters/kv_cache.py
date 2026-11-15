"""LM Studio / llama.cpp adapter — optional KV cache integration.

This adapter provides optional integration with servers that expose
KV cache information via API. It does NOT require LM Studio or llama.cpp
to be installed — it's a best-effort integration.

Supported endpoints (when available):
  - LM Studio: GET http://localhost:1234/v1/models (context info)
  - llama.cpp: GET http://localhost:8080/stats (cache stats)
  - Ollama: GET http://localhost:11434/api/stats (cache info)

Boster core functions WITHOUT these adapters. This module is optional.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("boster.adapters.kv_cache")


@dataclass
class CacheStats:
    """KV cache statistics from a server."""
    cached_tokens: int = 0
    computed_tokens: int = 0
    total_tokens: int = 0
    cache_hit_rate: float = 0.0
    server_type: str = ""  # "lm_studio", "llama_cpp", "ollama", "unknown"

    @property
    def is_reusable(self) -> bool:
        """True if cache has useful content."""
        return self.cached_tokens > 0 and self.cache_hit_rate > 0.1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cached_tokens": self.cached_tokens,
            "computed_tokens": self.computed_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "server_type": self.server_type,
            "is_reusable": self.is_reusable,
        }


class KVCacheAdapter:
    """Optional adapter for KV cache-aware servers.

    This adapter attempts to query server endpoints for cache statistics.
    If the server doesn't expose cache info, it returns empty stats.

    IMPORTANT: This adapter NEVER modifies server state. It only reads
    cache information when available.
    """

    def __init__(self, base_url: str = ""):
        self.base_url = base_url.rstrip("/")
        self._cache_stats: Optional[CacheStats] = None
        self._server_type: str = ""

    @property
    def is_configured(self) -> bool:
        """Whether a server URL is configured."""
        return bool(self.base_url)

    def detect_server_type(self) -> str:
        """Detect the type of server at base_url.

        Returns one of: "lm_studio", "llama_cpp", "ollama", "openai_compatible", "unknown"
        """
        if not self.is_configured:
            return "unknown"

        # Try to detect by endpoint availability
        # LM Studio serves OpenAI-compatible API on port 1234
        # llama.cpp serves on port 8080 with /stats endpoint
        # Ollama serves on port 11434
        if "11434" in self.base_url:
            return "ollama"
        elif "1234" in self.base_url:
            return "lm_studio"
        elif "8080" in self.base_url:
            return "llama_cpp"
        else:
            return "openai_compatible"

    def get_cache_stats(self) -> CacheStats:
        """Get current KV cache statistics.

        Returns empty stats if server doesn't expose cache info.
        """
        if not self.is_configured:
            return CacheStats(server_type="unknown")

        server_type = self.detect_server_type()

        if server_type == "llama_cpp":
            return self._get_llama_cpp_stats()
        elif server_type == "ollama":
            return self._get_ollama_stats()
        elif server_type in ("lm_studio", "openai_compatible"):
            return self._get_openai_compatible_stats()

        return CacheStats(server_type=server_type)

    def _get_llama_cpp_stats(self) -> CacheStats:
        """Get stats from llama.cpp server."""
        try:
            import urllib.request
            import json
            url = f"{self.base_url}/stats"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return CacheStats(
                    cached_tokens=data.get("cache_filled_tokens", 0),
                    computed_tokens=data.get("prompt_processing_tokens", 0),
                    total_tokens=data.get("prompt_processing_tokens", 0) + data.get("cache_filled_tokens", 0),
                    cache_hit_rate=data.get("prompt_token_cost", 0.0),
                    server_type="llama_cpp",
                )
        except Exception as e:
            logger.debug("Failed to get llama.cpp stats: %s", e)
            return CacheStats(server_type="llama_cpp")

    def _get_ollama_stats(self) -> CacheStats:
        """Get stats from Ollama server."""
        try:
            import urllib.request
            import json
            url = f"{self.base_url}/api/stats"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                # Ollama stats structure: {\"prompt_run_count\": N, \"prompt_token_count\": N, ...}
                return CacheStats(
                    cached_tokens=data.get("prompt_token_count", 0),
                    computed_tokens=data.get("eval_count", 0),
                    total_tokens=data.get("prompt_token_count", 0) + data.get("eval_count", 0),
                    cache_hit_rate=data.get("prompt_token_cost", 0.0),
                    server_type="ollama",
                )
        except Exception as e:
            logger.debug("Failed to get Ollama stats: %s", e)
            return CacheStats(server_type="ollama")

    def _get_openai_compatible_stats(self) -> CacheStats:
        """Get stats from OpenAI-compatible server (LM Studio, etc.)."""
        # These servers typically don't expose KV cache stats via API
        # Return empty stats — the client should use text analysis instead
        return CacheStats(server_type=self.detect_server_type())

    def should_compress(self, context_tokens: int, max_tokens: int) -> bool:
        """Determine if compression is advisable given current cache state.

        Args:
            context_tokens: Current token count.
            max_tokens: Maximum context tokens.

        Returns:
            True if compression is recommended.
        """
        stats = self.get_cache_stats()

        # If we have good cache hit rate, compression may not be needed
        if stats.cache_hit_rate > 0.8 and stats.cached_tokens > context_tokens * 0.5:
            logger.debug("Cache is healthy (hit rate %.1f%%), compression may be unnecessary",
                         stats.cache_hit_rate * 100)
            return False

        # If cache is small or empty, compression might help
        utilization = context_tokens / max(max_tokens, 1)
        return utilization > 0.7


__all__ = [
    "KVCacheAdapter",
    "CacheStats",
]
