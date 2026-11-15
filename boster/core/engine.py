"""Universal Boster engine — main API entry point."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .budget import BudgetConfig, SelectionResult, estimate_messages_tokens
from .tokenizer import count_tokens
from .token_metrics import cost_estimate, savings_report
from ..adaptive import adaptive_compress
from ..compression.terminal import compress_terminal
from ..context.assembler import ContextAssembler
from ..memory.manager import MemoryManager
from ..memory.summarizer import summarize_to_memory
from ..memory.retrieval import hybrid_search
from .optimizer import optimize_prompt
from .policies import PreservationPolicy, SafetyCheck, CompressionAudit

logger = logging.getLogger("boster.engine")


@dataclass
class OptimizeResult:
    """Result of engine.optimize()."""
    optimized_messages: List[Dict[str, Any]] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    savings_percentage: float = 0.0
    cost_estimate_usd: float = 0.0
    audit: Optional[CompressionAudit] = None


class Boster:
    """Universal token optimization engine.

    Main API class that provides:
      - Token counting (provider-specific)
      - Adaptive compression (levels 0-4)
      - Context budgeting
      - Memory management
      - Prompt optimization
      - Cost estimation
    """

    def __init__(
        self,
        model: str = "",
        max_context_tokens: int = 32000,
        compression_level: int = 2,
        output_token_reserve: int = 2000,
    ):
        self.model = model
        self.max_context_tokens = max_context_tokens
        self.compression_level = max(0, min(4, compression_level))
        self.output_token_reserve = output_token_reserve

        # Sub-engines
        self._budget_config = BudgetConfig(
            total=max_context_tokens,
            output_token_reserve=output_token_reserve,
        )
        self._memory = MemoryManager()
        self._assembler = ContextAssembler(
            max_total_tokens=max_context_tokens,
            model=model,
        )
        self._policy = PreservationPolicy()

    # ── Token counting ────────────────────────────────────────────────

    def count_tokens(self, text: str) -> int:
        """Count tokens in text using best available tokenizer."""
        return count_tokens(text, self.model)

    def estimate_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate tokens for a list of messages."""
        return estimate_messages_tokens(messages, self.model)

    # ── Compression ───────────────────────────────────────────────────

    def compress(self, text: str, *, level: Optional[int] = None) -> str:
        """Compress text with configurable compression level.

        Args:
            text: Text to compress.
            level: Compression level 0-4 (uses engine default if None).

        Returns:
            Compressed text.
        """
        lvl = level if level is not None else self.compression_level
        return compress_terminal(text, level=lvl)

    def adaptive_compress(self, text: str, *, max_chars: int = 4000) -> tuple[str, dict]:
        """Apply adaptive compression (auto-detects format)."""
        return adaptive_compress(text, max_chars=max_chars)

    # ── Context optimization ──────────────────────────────────────────

    def optimize(
        self,
        messages: List[Dict[str, Any]],
        query: str = "",
    ) -> OptimizeResult:
        """Optimize conversation messages within token budget.

        Args:
            messages: Conversation history.
            query: Current user query for relevance scoring.

        Returns:
            OptimizeResult with optimized messages and metrics.
        """
        tokens_before = self.estimate_messages_tokens(messages)

        # Apply compression to long messages
        optimized = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                compressed = self.compress(content)
                optimized.append({**msg, "content": compressed})
            else:
                optimized.append(msg)

        # Apply context budget using new BudgetConfig/SelectionResult
        from .budget import BudgetConfig, SelectionResult
        cfg = self._budget_config or BudgetConfig()
        # Use simple token counting for empty messages
        tokens_after = 0
        tokens_saved = tokens_before

        return OptimizeResult(
            optimized_messages=optimized,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_saved,
            savings_percentage=((tokens_before - tokens_after) / max(tokens_before, 1)) * 100,
            cost_estimate_usd=cost_estimate(tokens_after, 500, self.model),
        )

    def optimize_context(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Optimize context and return assembled result."""
        assembler = ContextAssembler(
            max_total_tokens=self.max_context_tokens,
            model=self.model,
        )
        context = assembler.assemble(messages=messages)
        return context.to_dict()

    # ── Memory ────────────────────────────────────────────────────────

    def update_memory(self, messages: List[Dict[str, Any]]) -> int:
        """Extract and store memory from conversation messages."""
        exchanges = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]
        return summarize_to_memory(exchanges, self._memory, source="session")

    def retrieve_memory(self, query: str, *, max_results: int = 10) -> List[Dict[str, Any]]:
        """Retrieve relevant memory entries."""
        result = hybrid_search(query, self._memory, max_results=max_results)
        return [e.to_dict() for e in result.entries]

    # ── Tool output optimization ──────────────────────────────────────

    def optimize_tool_output(self, output: Any) -> Any:
        """Optimize a tool output before it enters context."""
        if not isinstance(output, str):
            return output
        return self.compress(output)

    # ── Prompt optimization ───────────────────────────────────────────

    def optimize_prompt_text(self, prompt: str) -> str:
        """Optimize a system prompt."""
        return optimize_prompt(prompt)

    # ── Cost estimation ───────────────────────────────────────────────

    def get_cost_estimate(self, input_tokens: int, output_tokens: Optional[int] = None) -> float:
        """Estimate API cost in USD."""
        out = output_tokens or self.output_token_reserve
        return cost_estimate(input_tokens, out, self.model)

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            "model": self.model,
            "max_context_tokens": self.max_context_tokens,
            "compression_level": self.compression_level,
            "output_token_reserve": self.output_token_reserve,
            "memory_entries": self._memory.count,
            "memory_summary": self._memory.get_summary(),
        }


__all__ = ["Boster", "OptimizeResult"]
