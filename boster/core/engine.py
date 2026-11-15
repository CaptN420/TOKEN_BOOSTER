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

# v2.0 intelligence modules
from ..intelligence.monitor import ContextMonitor, CompressionMode, ThresholdConfig
from ..intelligence.compressor import ContextCompressor, CompressionResult
from ..intelligence.integrity import ContextIntegrityEngine
from ..intelligence.snapshot import SnapshotManager
from ..intelligence.cache_awareness import CacheAwareness, CacheImpactReport

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
    # v2.0 fields
    compression_triggered: bool = False
    compression_level: int = 0
    cache_impact: Optional[CacheImpactReport] = None
    snapshot_id: Optional[str] = None


class Boster:
    """Universal token optimization engine.

    Main API class that provides:
      - Token counting (provider-specific)
      - Adaptive compression (levels 0-4)
      - Context budgeting
      - Memory management
      - Prompt optimization
      - Cost estimation
      - v2.0: Smart compression with KV cache awareness
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

        # v2.0 intelligence engines
        self._monitor = ContextMonitor(
            max_context_tokens=max_context_tokens,
            mode=CompressionMode.THRESHOLD,
        )
        self._compressor = ContextCompressor()
        self._integrity = ContextIntegrityEngine()
        self._snapshots = SnapshotManager()
        self._cache_awareness = CacheAwareness()

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

        v2.0 behavior: compression is ONLY triggered when the monitor
        indicates it's warranted. In passive mode, no compression occurs.

        Args:
            messages: Conversation history.
            query: Current user query for relevance scoring.

        Returns:
            OptimizeResult with optimized messages and metrics.
        """
        tokens_before = self.estimate_messages_tokens(messages)

        # Update monitor with current state
        report = self._monitor.update(tokens_before)

        # Check if compression is warranted
        if not report.should_compress:
            # No compression needed — return messages as-is
            return OptimizeResult(
                optimized_messages=list(messages),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                tokens_saved=0,
                savings_percentage=0.0,
                cost_estimate_usd=cost_estimate(tokens_before, self.output_token_reserve, self.model),
            )

        # Create snapshot before compression (for reversibility)
        snapshot = self._snapshots.create(
            label=f"Pre-compression at {report.utilization:.0%} utilization",
            facts=[{"content": m.get("content", "")[:100], "source_message_index": i}
                   for i, m in enumerate(messages[:10])],
            token_count=tokens_before,
        )

        # Analyze cache impact
        full_text = "\n".join(m.get("content", "") for m in messages)
        cache_impact = self._cache_awareness.analyze_prefix_change(full_text, full_text)

        # Apply compression only to messages that need it
        optimized = []
        total_saved = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                comp_result = self._compressor.compress(content)
                optimized.append({**msg, "content": comp_result.compressed_text})
                total_saved += comp_result.tokens_saved
            else:
                optimized.append(msg)

        self._monitor.record_compression()

        tokens_after = self.estimate_messages_tokens(optimized)
        tokens_saved = max(0, tokens_before - tokens_after)

        return OptimizeResult(
            optimized_messages=optimized,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_saved,
            savings_percentage=((tokens_before - tokens_after) / max(tokens_before, 1)) * 100,
            cost_estimate_usd=cost_estimate(tokens_after, self.output_token_reserve, self.model),
            compression_triggered=True,
            compression_level=self.compression_level,
            cache_impact=cache_impact,
            snapshot_id=snapshot.id if snapshot else None,
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
            # v2.0 stats
            "monitor_mode": self._monitor.mode.value,
            "compression_count": self._monitor.compression_count,
            "snapshot_count": self._snapshots.count,
        }

    # ── v2.0 Public API ───────────────────────────────────────────────

    @property
    def monitor(self) -> ContextMonitor:
        """Access the context monitor."""
        return self._monitor

    @property
    def compressor(self) -> ContextCompressor:
        """Access the context compressor."""
        return self._compressor

    @property
    def integrity_engine(self) -> ContextIntegrityEngine:
        """Access the context integrity engine."""
        return self._integrity

    @property
    def snapshots(self) -> SnapshotManager:
        """Access the snapshot manager."""
        return self._snapshots

    @property
    def cache_awareness(self) -> CacheAwareness:
        """Access the cache awareness analyzer."""
        return self._cache_awareness

    def set_compression_mode(self, mode: str | CompressionMode) -> None:
        """Change compression mode (Passive, Threshold, Task Boundary)."""
        if isinstance(mode, str):
            mode = CompressionMode(mode)
        self._monitor.mode = mode

    def take_snapshot(self, label: str = "", summary: str = "") -> Any:
        """Take a manual snapshot of current context state."""
        return self._snapshots.create(label=label, summary=summary)


__all__ = ["Boster", "OptimizeResult"]
