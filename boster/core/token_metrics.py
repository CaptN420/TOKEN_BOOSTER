"""Token metrics: density analysis, cost estimation, savings reports."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tokenizer import count_tokens

logger = logging.getLogger("boster.token_metrics")

# ── Cost tables (per 1M tokens) — last updated 2025-06 ──────────────

_COST_TABLE: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    # Anthropic
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    # DeepSeek
    "deepseek/deepseek-v4": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-v4-flash": {"input": 0.07, "output": 0.14},
    "deepseek/deepseek-r1": {"input": 0.55, "output": 2.19},
    # Google Gemini
    "gemini-pro": {"input": 0.70, "output": 2.10},
    "gemini-1.5-pro": {"input": 1.25, "output": 3.75},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


def _match_model(model: str) -> Optional[Dict[str, float]]:
    """Find cost table entry matching model name."""
    if not model:
        return None
    ml = model.lower()
    for key, costs in _COST_TABLE.items():
        if key in ml or ml in key:
            return costs
    return None


@dataclass
class TokenMetrics:
    """Structured token usage metrics."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    cost_per_million: Dict[str, float] = field(default_factory=dict)
    estimated_cost: float = 0.0
    token_density: float = 0.0  # tokens per 100 chars

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "model": self.model,
            "cost_per_million": self.cost_per_million,
            "estimated_cost_usd": round(self.estimated_cost, 4),
            "token_density": round(self.token_density, 2),
        }


def token_density(text: str) -> float:
    """Tokens per 100 characters. Higher = more dense (code), lower = sparse (prose)."""
    if not text:
        return 0.0
    tokens = count_tokens(text)
    return (tokens / max(len(text), 1)) * 100


def cost_estimate(
    input_tokens: int,
    output_tokens: int,
    model: str = "",
) -> float:
    """Estimate API cost in USD for given token counts."""
    costs = _match_model(model)
    if not costs:
        # Default to GPT-4o rates as baseline
        costs = {"input": 2.50, "output": 10.00}
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    return round(input_cost + output_cost, 6)


def savings_report(
    original_text: str,
    compressed_text: str,
    model: str = "",
) -> Dict[str, Any]:
    """Structured savings report comparing original vs compressed text."""
    orig_tokens = count_tokens(original_text, model)
    comp_tokens = count_tokens(compressed_text, model)
    saved_tokens = orig_tokens - comp_tokens
    saved_pct = (saved_tokens / max(orig_tokens, 1)) * 100

    return {
        "original_chars": len(original_text),
        "compressed_chars": len(compressed_text),
        "original_tokens": orig_tokens,
        "compressed_tokens": comp_tokens,
        "tokens_saved": saved_tokens,
        "savings_pct": round(saved_pct, 1),
        "compression_ratio": round(len(compressed_text) / max(len(original_text), 1), 3),
        "model": model,
    }


def metrics_savings_report(
    original_text: str,
    compressed_text: str,
    model: str = "",
) -> Dict[str, Any]:
    """Alias for savings_report (backwards compat)."""
    return savings_report(original_text, compressed_text, model)


__all__ = [
    "TokenMetrics",
    "token_density",
    "cost_estimate",
    "savings_report",
    "metrics_savings_report",
]
