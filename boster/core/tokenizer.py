"""Tokenizer abstraction — provider-specific token counting.

Implements a pluggable tokenizer interface with these adapters:
  - TiktokenAdapter     (OpenAI cl100k_base, gpt2, p50k_base, etc.)
  - HuggingFaceAdapter  (huggingface/tokenizers — DeepSeek, Llama, etc.)
  - CharEstimateAdapter (fallback: chars/4 for ASCII, chars/3 for non-ASCII)

All methods are deterministic and require no network I/O after initial load.
"""
from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional

logger = logging.getLogger("boster.tokenizer")

# ── Adapter base class ────────────────────────────────────────────────


class TokenizerAdapter(ABC):
    """Abstract tokenizer adapter. Subclasses implement encode(text) -> list[int]."""

    @abstractmethod
    def encode(self, text: str) -> list[int]:
        ...

    def count(self, text: str) -> int:
        """Return token count for text."""
        if not text:
            return 0
        return len(self.encode(text))

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ── Tiktoken adapter ──────────────────────────────────────────────────


class TiktokenAdapter(TokenizerAdapter):
    """Uses tiktoken (OpenAI tokenizers: cl100k_base, gpt2, p50k_base, etc.)."""

    _cache: Dict[str, "TiktokenAdapter"] = {}
    _lock = threading.Lock()

    def __init__(self, encoding_name: str = "cl100k_base"):
        self.encoding_name = encoding_name
        self._enc = None

    @property
    def enc(self):
        if self._enc is None:
            try:
                import tiktoken
                self._enc = tiktoken.get_encoding(self.encoding_name)
            except ImportError:
                raise ImportError(
                    "tiktoken not installed. Run: uv pip install tiktoken"
                )
        return self._enc

    def encode(self, text: str) -> list[int]:
        return self.enc.encode(text)

    def __repr__(self) -> str:
        return f"TiktokenAdapter({self.encoding_name})"


# ── HuggingFace tokenizer adapter ─────────────────────────────────────


class HuggingFaceTokenizerAdapter(TokenizerAdapter):
    """Uses huggingface/tokenizers for models like DeepSeek, Llama, etc."""

    _cache: Dict[str, "HuggingFaceTokenizerAdapter"] = {}
    _lock = threading.Lock()

    def __init__(self, tokenizer_name: str):
        self.tokenizer_name = tokenizer_name
        self._tokenizer = None

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            try:
                from tokenizers import Tokenizer as _Tok
                import requests
                # Try loading from HuggingFace hub URL
                url = f"https://huggingface.co/{self.tokenizer_name}/raw/main/tokenizer.json"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    self._tokenizer = _Tok.from_str(r.text)
                else:
                    # Try local path
                    import pathlib
                    p = pathlib.Path(self.tokenizer_name)
                    if p.exists() and (p / "tokenizer.json").exists():
                        self._tokenizer = _Tok.from_file(str(p / "tokenizer.json"))
            except Exception as e:
                logger.debug("HuggingFace tokenizer load failed: %s", e)
        return self._tokenizer

    def encode(self, text: str) -> list[int]:
        t = self.tokenizer
        if t is None:
            raise RuntimeError(
                f"Could not load tokenizer for {self.tokenizer_name}"
            )
        return t.encode(text).ids

    def __repr__(self) -> str:
        return f"HuggingFaceTokenizerAdapter({self.tokenizer_name})"


# ── Character estimate adapter (fallback) ─────────────────────────────


class CharEstimateAdapter(TokenizerAdapter):
    """Fallback: estimate tokens from character count.

    Rule of thumb: ~4 chars/token for English ASCII, ~3 for mixed.
    """

    def encode(self, text: str) -> list[int]:
        if not text:
            return []
        # Rough estimate: 4 chars per token for ASCII, 3 for non-ASCII
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return list(range((ascii_chars // 4) + (non_ascii // 3) + 1))

    def __repr__(self) -> str:
        return "CharEstimateAdapter()"


# ── Model-to-tokenizer mapping ────────────────────────────────────────

_MODEL_TOKENIZER_MAP: Dict[str, str] = {
    # OpenAI models → tiktoken encodings
    "gpt-4": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4o": "cl100k_base",
    "gpt-4o-mini": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5-turbo-16k": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
    "davinci-002": "g400k_base",
    "code-davinci-002": "p50k_base",
    # DeepSeek models → huggingface tokenizer
    "deepseek/deepseek-v4": "deepseek-ai/deepseek-v4",
    "deepseek/deepseek-v4-flash": "deepseek-ai/deepseek-v4-flash",
    "deepseek/deepseek-r1": "deepseek-ai/deepseek-r1",
    "deepseek/deepseek-chat": "deepseek-ai/deepseek-chat",
    "deepseek/deepseek-coder": "deepseek-ai/deepseek-coder",
    # Anthropic (use cl100k_base as approximation — not exact but close)
    "claude-3-opus": "cl100k_base",
    "claude-3-sonnet": "cl100k_base",
    "claude-3-haiku": "cl100k_base",
    "claude-3-5-sonnet": "cl100k_base",
    "claude-3-5-haiku": "cl100k_base",
    # Google Gemini (use cl100k_base as approximation)
    "gemini-pro": "cl100k_base",
    "gemini-1.5-pro": "cl100k_base",
    "gemini-1.5-flash": "cl100k_base",
    "gemini-2.0-flash": "cl100k_base",
    # Llama models (huggingface)
    "llama-3": "meta-llama/Meta-Llama-3-8B-Instruct",
    "llama-3.1": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "llama-3.2": "meta-llama/Llama-3.2-3B-Instruct",
}


def _detect_model_family(model: str) -> Optional[str]:
    """Detect which tokenizer to use for a model name."""
    if not model:
        return None
    ml = model.lower()
    # Direct match
    if model in _MODEL_TOKENIZER_MAP:
        return _MODEL_TOKENIZER_MAP[model]
    # Partial match
    for pattern, adapter in _MODEL_TOKENIZER_MAP.items():
        if pattern in ml:
            return adapter
    return None


# ── Public API ────────────────────────────────────────────────────────

_active_adapters: Dict[str, TokenizerAdapter] = {}


def get_tokenizer(model: str = "") -> TokenizerAdapter:
    """Get or create a tokenizer adapter for the given model.

    Falls back to CharEstimateAdapter if no specific adapter is available.
    """
    if not model:
        return CharEstimateAdapter()

    key = model
    if key not in _active_adapters:
        family = _detect_model_family(model)
        if family is None:
            _active_adapters[key] = CharEstimateAdapter()
        elif "deepseek" in family or "llama" in family or "meta" in family:
            _active_adapters[key] = HuggingFaceTokenizerAdapter(family)
        else:
            _active_adapters[key] = TiktokenAdapter(family)

    return _active_adapters[key]


def count_tokens(text: str, model: str = "") -> int:
    """Count tokens in text using the best available tokenizer.

    Args:
        text: Text to count tokens for.
        model: Model name for tokenizer selection (optional).

    Returns:
        Integer token count. Never returns 0 for non-empty text.
    """
    if not text:
        return 0
    adapter = get_tokenizer(model)
    try:
        count = adapter.count(text)
        return max(count, 1)
    except Exception as e:
        logger.debug("Tokenizer failed for model=%s: %s", model, e)
        # Fallback to char estimate
        return max(1, (len(text) + 3) // 4)


def estimate_provider_tokens(text: str, model: str = "") -> int:
    """Backwards-compatible alias for count_tokens."""
    return count_tokens(text, model)


__all__ = [
    "TokenizerAdapter",
    "TiktokenAdapter",
    "HuggingFaceTokenizerAdapter",
    "CharEstimateAdapter",
    "get_tokenizer",
    "count_tokens",
    "estimate_provider_tokens",
]
