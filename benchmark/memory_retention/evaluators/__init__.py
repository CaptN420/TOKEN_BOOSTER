"""Evaluators for memory retention benchmark tests."""
from __future__ import annotations

from typing import Any, Dict, List


class FactEvaluator:
    """Evaluate fact retention in compressed context."""

    def __init__(self, ground_truth: Dict[str, Any]):
        self.facts = ground_truth.get("facts", [])
        self.relationships = ground_truth.get("relationships", [])
        self.decisions = ground_truth.get("decisions", [])
        self.constraints = ground_truth.get("constraints", [])
        self.temporal = ground_truth.get("temporal", [])
        self.contradictions = ground_truth.get("contradictions", [])

    def evaluate(self, selected_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        from benchmark.memory_retention.metrics import (
            compute_fact_retention,
            compute_relationship_retention,
            compute_decision_retention,
            compute_constraint_retention,
            compute_temporal_accuracy,
            compute_contradiction_preservation,
            compute_long_range_recall,
        )

        fact_result = compute_fact_retention(selected_messages, self.facts)
        rel_result = compute_relationship_retention(selected_messages, self.relationships)
        dec_result = compute_decision_retention(selected_messages, self.decisions)
        con_result = compute_constraint_retention(selected_messages, self.constraints)
        temp_result = compute_temporal_accuracy(selected_messages, self.temporal)
        contra_result = compute_contradiction_preservation(selected_messages, self.contradictions)
        long_range = compute_long_range_recall(selected_messages, self.facts, max_message_idx=10)

        return {
            "fact_retention": fact_result,
            "relationship_retention": rel_result,
            "decision_retention": dec_result,
            "constraint_retention": con_result,
            "temporal_accuracy": temp_result,
            "contradiction_preservation": contra_result,
            "long_range_recall": long_range,
        }


class TokenEfficiencyEvaluator:
    """Evaluate token efficiency of compression."""

    def __init__(self):
        self.results: List[Dict[str, Any]] = []

    def record(
        self,
        scenario: str,
        strategy: str,
        original_tokens: int,
        selected_tokens: int,
        processing_time_ms: float,
        **extra: Any,
    ) -> None:
        self.results.append({
            "scenario": scenario,
            "strategy": strategy,
            "original_tokens": original_tokens,
            "selected_tokens": selected_tokens,
            "reduction_pct": (1 - selected_tokens / max(original_tokens, 1)) * 100,
            "processing_time_ms": processing_time_ms,
            **extra,
        })

    def report(self) -> List[Dict[str, Any]]:
        return self.results


class PerformanceEvaluator:
    """Evaluate performance characteristics."""

    def __init__(self):
        self.timings: Dict[str, List[float]] = {}

    def record(self, label: str, time_ms: float) -> None:
        self.timings.setdefault(label, []).append(time_ms)

    def stats(self, label: str) -> Dict[str, float]:
        times = self.timings.get(label, [])
        if not times:
            return {}
        sorted_times = sorted(times)
        n = len(sorted_times)
        return {
            "count": n,
            "avg_ms": sum(times) / n,
            "min_ms": min(times),
            "max_ms": max(times),
            "median_ms": sorted_times[n // 2],
            "p95_ms": sorted_times[int(n * 0.95)] if n > 1 else sorted_times[0],
        }

    def report(self) -> Dict[str, Any]:
        return {label: self.stats(label) for label in self.timings}
