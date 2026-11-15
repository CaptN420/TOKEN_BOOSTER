#!/usr/bin/env python3
"""
pipeline.py — Multi-stage compression pipeline.

Chains compress → history → budget into a single operation that can
be applied to a full conversation JSON, producing a compact context
ready for LLM injection.

Usage:
    from boster.pipeline import run_pipeline

    result = run_pipeline(
        messages=messages,
        system_prompt=system,
        max_total_tokens=20000,
        compress_output=True,
        summarize_history=True,
    )
    print(result.report())
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .context_budget import BudgetConfig, ContextBudget, SelectionResult
from .history_compressor import compress_history, format_compressed_history, HistorySummary
from .output_compressor import compress as compress_output, savings_report

logger = logging.getLogger("pipeline")


@dataclass
class PipelineResult:
    """Result of running the full compression pipeline."""
    selected_messages: List[Dict[str, Any]] = field(default_factory=list)
    compressed_system: str = ""
    budget_result: Optional[SelectionResult] = None
    history_summary: Optional[HistorySummary] = None
    output_savings: dict = field(default_factory=dict)
    total_original_tokens: int = 0
    total_selected_tokens: int = 0

    def report(self) -> str:
        lines = ["═" * 52, "  PIPELINE RESULT", "═" * 52]
        if self.budget_result:
            lines.append(self.budget_result.report())
        if self.history_summary:
            lines.append("")
            lines.append(f"  {self.history_summary.savings_report()}")
        if self.output_savings:
            lines.append("")
            lines.append("  Output compression:")
            for k, v in self.output_savings.items():
                lines.append(f"    {k}: {v}")
        lines.append("═" * 52)
        return "\n".join(lines)


def run_pipeline(
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    system_prompt: str = "",
    max_total_tokens: int = 20_000,
    compress_output_msgs: bool = True,
    summarize_history: bool = True,
    model: str = "",
) -> PipelineResult:
    """Run the full compression pipeline.

    Args:
        messages: Full conversation history.
        system_prompt: System prompt text.
        max_total_tokens: Total token budget.
        compress_output: Whether to compress tool outputs in messages.
        summarize_history: Whether to summarize old history.
        model: Model name for token estimation.

    Returns:
        PipelineResult with selected content and reports.
    """
    msgs = list(messages) if messages else []
    result = PipelineResult()

    # Stage 1: Compress any tool outputs in messages
    if compress_output_msgs:
        compressed_msgs = []
        for msg in msgs:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                comp = compress_output(content)
                savings = savings_report(content, comp)
                result.output_savings[f"msg_{len(compressed_msgs)}"] = savings
                new_msg = dict(msg)
                new_msg["content"] = comp
                compressed_msgs.append(new_msg)
            else:
                compressed_msgs.append(msg)
        msgs = compressed_msgs

    # Stage 2: Summarize old history if needed
    if summarize_history and len(msgs) > 20:
        keep_recent = min(10, len(msgs))
        to_compress = msgs[:-keep_recent]
        recent = msgs[-keep_recent:]

        summary = compress_history(to_compress, max_exchanges=keep_recent * 2)
        result.history_summary = summary

        # Replace old history with compressed summary + recent
        summary_msg = {"role": "user", "content": f"[Compressed History]\n{summary.render(max_lines=20)}"}
        msgs = [summary_msg] + recent

    # Stage 3: Apply context budget
    config = BudgetConfig(total=max_total_tokens)
    budget = ContextBudget(model=model, config=config)
    budget_result = budget.select(
        system_prompt=system_prompt,
        messages=msgs,
        user_query="",
        dry_run=True,
    )

    result.budget_result = budget_result
    result.selected_messages = list(budget_result.selected_messages)
    result.compressed_system = budget_result.selected_system_text
    result.total_original_tokens = budget_result.original_total_tokens
    result.total_selected_tokens = budget_result.selected_total_tokens

    logger.info(
        "Pipeline: %d → %d tokens (%.0f%% reduction)",
        result.total_original_tokens,
        result.total_selected_tokens,
        (1 - result.total_selected_tokens / max(result.total_original_tokens, 1)) * 100,
    )

    return result


__all__ = ["PipelineResult", "run_pipeline"]
