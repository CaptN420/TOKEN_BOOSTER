"""Enhanced context budget with dynamic allocation and emergency reduction.

Extends the original context_budget.py with:
  - Dynamic budget allocation (reserves output tokens)
  - Emergency context reduction
  - Token-aware truncation (not just char-based)
  - Recency + relevance weighting improvements
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .tokenizer import count_tokens
from .token_metrics import cost_estimate

logger = logging.getLogger("boster.budget")

# ── Re-export existing types for backwards compat ────────────────────
# (Imported from the original context_budget module)
try:
    from ..context_budget import BudgetConfig as _OldBudgetConfig
    from ..context_budget import SelectionResult as _OldSelectionResult
    HAS_OLD = True
except ImportError:
    HAS_OLD = False


# ── Enhanced budget config ───────────────────────────────────────────

@dataclass
class BudgetConfig:
    """Context Budget configuration with output token reservation.

    All values are in provider-equivalent tokens.
    """
    total: int = 30_000
    system: int = 5_000
    history: int = 8_000
    memory: int = 7_000
    tools: int = 5_000
    retrieved: int = 5_000

    # Output token reservation (tokens reserved for model response)
    output_token_reserve: int = 2_000

    # Emergency reduction: when context exceeds budget by this %,
    # trigger aggressive truncation
    emergency_threshold_pct: float = 120.0

    @classmethod
    def from_env(cls) -> "BudgetConfig":
        """Load from environment variables."""
        return cls(
            total=int(os.environ.get("CAPTN_CONTEXT_BUDGET_TOTAL", "30000")),
            system=int(os.environ.get("CAPTN_CONTEXT_BUDGET_SYSTEM", "5000")),
            history=int(os.environ.get("CAPTN_CONTEXT_BUDGET_HISTORY", "8000")),
            memory=int(os.environ.get("CAPTN_CONTEXT_BUDGET_MEMORY", "7000")),
            tools=int(os.environ.get("CAPTN_CONTEXT_BUDGET_TOOLS", "5000")),
            retrieved=int(os.environ.get("CAPTN_CONTEXT_BUDGET_RETRIEVED", "5000")),
            output_token_reserve=int(os.environ.get(
                "CAPTN_OUTPUT_TOKEN_RESERVE", "2000"
            )),
        )

    @property
    def effective_total(self) -> int:
        """Total budget minus output token reserve."""
        return max(0, self.total - self.output_token_reserve)


DEFAULT_BUDGET_CONFIG = BudgetConfig()


# ── Selection result ─────────────────────────────────────────────────

@dataclass
class SelectionDecision:
    """Record of one selection/pruning decision."""
    category: str
    item_id: str = ""
    estimated_tokens: int = 0
    relevance_score: float = 0.0
    selected: bool = False
    selection_reason: str = ""


@dataclass
class SelectionResult:
    """Result of applying the Context Budget to a request."""
    # Original estimates
    original_system_tokens: int = 0
    original_history_tokens: int = 0
    original_memory_tokens: int = 0
    original_tool_tokens: int = 0
    original_retrieved_tokens: int = 0
    original_total_tokens: int = 0

    # Selected counts
    selected_system_tokens: int = 0
    selected_history_tokens: int = 0
    selected_memory_tokens: int = 0
    selected_tool_tokens: int = 0
    selected_retrieved_tokens: int = 0
    selected_total_tokens: int = 0

    # Budget limits
    budget_total: int = 30_000
    budget_system: int = 5_000
    budget_history: int = 8_000
    budget_memory: int = 7_000
    budget_tools: int = 5_000
    budget_retrieved: int = 5_000

    # Output token reserve
    output_token_reserve: int = 2_000

    # Overflow flags
    system_budget_exceeded: bool = False
    tools_budget_exceeded: bool = False
    total_budget_exceeded: bool = False

    # Items dropped
    history_items_dropped: int = 0
    memory_items_dropped: int = 0
    retrieved_items_dropped: int = 0
    tools_dropped: int = 0

    # Decisions
    decisions: List[SelectionDecision] = field(default_factory=list)

    # Selected content (for actual use)
    selected_messages: List[Dict[str, Any]] = field(default_factory=list)
    selected_tools: List[Dict[str, Any]] = field(default_factory=list)
    selected_memory_text: str = ""
    selected_retrieved_text: str = ""
    selected_system_text: str = ""

    # History compression
    history_compressed: bool = False
    history_compressed_savings: int = 0

    # Emergency reduction
    emergency_reduction_triggered: bool = False
    emergency_reduction_pct: float = 0.0

    # Dry-run mode
    dry_run: bool = False

    @property
    def unused_budget(self) -> int:
        return max(0, self.budget_total - self.selected_total_tokens)

    @property
    def overflow_tokens(self) -> int:
        if self.total_budget_exceeded:
            return self.selected_total_tokens - self.budget_total
        return 0

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate API cost for the selected context."""
        return cost_estimate(
            self.selected_total_tokens,
            self.output_token_reserve,
        )

    def report(self) -> str:
        sep = "─" * 52
        lines = [sep, "  HERMES CONTEXT BUDGET", sep]
        lines.append(f"  {'Budget':30s} {self.budget_total:>8,d}")
        lines.append(f"  {'Output reserve':30s} {self.output_token_reserve:>8,d}")
        lines.append("")
        lines.append(f"  {'System':30s} {self.selected_system_tokens:>6,d} / {self.budget_system:<6,d}"
                      f"  {'(!) EXCEEDED' if self.system_budget_exceeded else ''}")
        lines.append(f"  {'History':30s} {self.selected_history_tokens:>6,d} / {self.budget_history:<6,d}"
                      f"  {'compressed' if self.history_compressed else ''}")
        lines.append(f"  {'Memory':30s} {self.selected_memory_tokens:>6,d} / {self.budget_memory:<6,d}")
        lines.append(f"  {'Tools':30s} {self.selected_tool_tokens:>6,d} / {self.budget_tools:<6,d}"
                      f"  {'(!) EXCEEDED' if self.tools_budget_exceeded else ''}")
        lines.append(f"  {'Retrieved':30s} {self.selected_retrieved_tokens:>6,d} / {self.budget_retrieved:<6,d}")
        lines.append(sep)
        lines.append(f"  {'Selected Total':30s} {self.selected_total_tokens:>8,d}")
        lines.append(f"  {'Original Total':30s} {self.original_total_tokens:>8,d}")
        if self.selected_total_tokens < self.original_total_tokens:
            saved = self.original_total_tokens - self.selected_total_tokens
            pct = (saved / max(self.original_total_tokens, 1)) * 100
            lines.append(f"  {'Token Reduction':30s} {saved:>8,d}  ({pct:.0f}%)")
        lines.append(f"  {'Unused budget':30s} {self.unused_budget:>8,d}")
        if self.overflow_tokens > 0:
            lines.append(f"  {'⚠ OVERFLOW':30s} {self.overflow_tokens:>+8,d}")
        if self.emergency_reduction_triggered:
            lines.append(f"  {'Emergency reduce':30s} {self.emergency_reduction_pct:.0f}%")
        lines.append("")
        lines.append(f"  {'History items dropped':30s} {self.history_items_dropped:>8,d}")
        lines.append(f"  {'Memory items dropped':30s} {self.memory_items_dropped:>8,d}")
        lines.append(f"  {'Retrieved items dropped':30s} {self.retrieved_items_dropped:>8,d}")
        lines.append(f"  {'Tools dropped':30s} {self.tools_dropped:>8,d}")
        if self.dry_run:
            lines.append(sep)
            lines.append("  DRY RUN — no changes applied to real request")
        lines.append(sep)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget": {
                "total": self.budget_total,
                "system": self.budget_system, "history": self.budget_history,
                "memory": self.budget_memory, "tools": self.budget_tools,
                "retrieved": self.budget_retrieved,
                "output_reserve": self.output_token_reserve,
            },
            "original": {
                "system": self.original_system_tokens, "history": self.original_history_tokens,
                "memory": self.original_memory_tokens, "tools": self.original_tool_tokens,
                "retrieved": self.original_retrieved_tokens, "total": self.original_total_tokens,
            },
            "selected": {
                "system": self.selected_system_tokens, "history": self.selected_history_tokens,
                "memory": self.selected_memory_tokens, "tools": self.selected_tool_tokens,
                "retrieved": self.selected_retrieved_tokens, "total": self.selected_total_tokens,
            },
            "overflow": {
                "system_budget_exceeded": self.system_budget_exceeded,
                "tools_budget_exceeded": self.tools_budget_exceeded,
                "total_budget_exceeded": self.total_budget_exceeded,
                "overflow_tokens": self.overflow_tokens,
            },
            "dropped": {
                "history_items": self.history_items_dropped,
                "memory_items": self.memory_items_dropped,
                "retrieved_items": self.retrieved_items_dropped,
                "tools": self.tools_dropped,
            },
            "history_compressed": self.history_compressed,
            "history_compressed_savings": self.history_compressed_savings,
            "emergency_reduction_triggered": self.emergency_reduction_triggered,
            "emergency_reduction_pct": self.emergency_reduction_pct,
            "dry_run": self.dry_run,
            "unused_budget": self.unused_budget,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


# ── Helper functions (ported from original context_budget.py) ─────────

def estimate_provider_tokens(text: str, model: str = "") -> int:
    """Estimate provider-equivalent tokens for a text string."""
    return count_tokens(text, model)


def estimate_messages_tokens(messages: List[Dict[str, Any]], model: str = "") -> int:
    """Estimate tokens for a list of OpenAI-format messages."""
    if not messages:
        return 0
    total = 0
    for msg in messages:
        total += 4  # role overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_provider_tokens(content, model)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_provider_tokens(part.get("text", ""), model)
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                total += estimate_provider_tokens(fn.get("name", ""), model)
                total += estimate_provider_tokens(fn.get("arguments", ""), model)
    return total


def estimate_tool_tokens(tools: List[Dict[str, Any]], model: str = "") -> int:
    """Estimate tokens for a list of tool schemas."""
    if not tools:
        return 0
    return estimate_provider_tokens(
        json.dumps(tools, ensure_ascii=False, default=str), model
    )


__all__ = [
    "BudgetConfig",
    "SelectionResult",
    "SelectionDecision",
    "estimate_provider_tokens",
    "estimate_messages_tokens",
    "estimate_tool_tokens",
    # Backwards compat: re-export ContextBudget from old module
    "ContextBudget",
]

# Re-export ContextBudget from legacy module for backwards compatibility
try:
    from ..context_budget import ContextBudget  # noqa: F401
except ImportError:
    pass
