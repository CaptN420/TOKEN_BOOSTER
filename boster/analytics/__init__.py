"""Analytics: savings tracking, cost estimation, reports, benchmarks."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SavingsRecord:
    """Record of a compression/savings event."""
    timestamp: float = field(default_factory=time.time)
    operation: str = ""
    input_chars: int = 0
    output_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    savings_pct: float = 0.0
    model: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "savings_pct": self.savings_pct,
            "model": self.model,
        }


class SavingsTracker:
    """Track token savings across operations."""

    def __init__(self):
        self._records: List[SavingsRecord] = []

    def record(
        self,
        operation: str,
        input_chars: int,
        output_chars: int,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
    ) -> SavingsRecord:
        """Record a savings event."""
        record = SavingsRecord(
            operation=operation,
            input_chars=input_chars,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            savings_pct=((input_tokens - output_tokens) / max(input_tokens, 1)) * 100,
            model=model,
        )
        self._records.append(record)
        return record

    def get_total_saved(self) -> int:
        """Get total tokens saved across all operations."""
        return sum(r.input_tokens - r.output_tokens for r in self._records)

    def get_total_input(self) -> int:
        """Get total input tokens across all operations."""
        return sum(r.input_tokens for r in self._records)

    def get_average_savings_pct(self) -> float:
        """Get average savings percentage."""
        if not self._records:
            return 0.0
        return sum(r.savings_pct for r in self._records) / len(self._records)

    def get_report(self) -> Dict[str, Any]:
        """Get summary report."""
        return {
            "total_operations": len(self._records),
            "total_input_tokens": self.get_total_input(),
            "total_saved_tokens": self.get_total_saved(),
            "average_savings_pct": round(self.get_average_savings_pct(), 1),
            "recent_records": [r.to_dict() for r in self._records[-10:]],
        }


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""
    name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    savings_pct: float = 0.0
    latency_ms: float = 0.0
    memory_delta_mb: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "savings_pct": self.savings_pct,
            "latency_ms": self.latency_ms,
            "memory_delta_mb": self.memory_delta_mb,
            "metadata": self.metadata,
        }


class BenchmarkSuite:
    """Run and compare compression benchmarks."""

    def __init__(self):
        self._results: List[BenchmarkResult] = []

    def add_result(self, result: BenchmarkResult) -> None:
        """Add a benchmark result."""
        self._results.append(result)

    def get_comparison(self) -> Dict[str, Any]:
        """Get comparison of all benchmark results."""
        return {
            "total_benchmarks": len(self._results),
            "results": [r.to_dict() for r in self._results],
            "avg_savings_pct": (
                sum(r.savings_pct for r in self._results) / max(len(self._results), 1)
            ),
            "avg_latency_ms": (
                sum(r.latency_ms for r in self._results) / max(len(self._results), 1)
            ),
        }


# Module-level instances
savings_tracker = SavingsTracker()
benchmark_suite = BenchmarkSuite()


__all__ = [
    "SavingsRecord",
    "SavingsTracker",
    "BenchmarkResult",
    "BenchmarkSuite",
    "savings_tracker",
    "benchmark_suite",
]
