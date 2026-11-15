"""Memory Retention Benchmark — Performance Test.

Measures processing time, memory usage, and scalability.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from boster.context_budget import ContextBudget, BudgetConfig
from benchmark.memory_retention.generators import ConversationGenerator


def bench_performance(size: str, trials: int = 20) -> Dict[str, Any]:
    """Measure performance characteristics."""
    generator = ConversationGenerator()
    all_convs = generator.generate_all()
    conv = all_convs[size]
    exchanges = conv["exchanges"]

    from boster.core.tokenizer import count_tokens
    original_text = json.dumps(exchanges, ensure_ascii=False)
    original_tokens = count_tokens(original_text)

    budget = ContextBudget(
        model="gpt-4",
        config=BudgetConfig(total=2000, history=1000, system=500, memory=500, tools=500, retrieved=500),
    )

    # Run compression (outside timing loop for result capture)
    result = budget.select(
        system_prompt=exchanges[0]["content"] if exchanges[0].get("role") == "system" else "",
        messages=[m for m in exchanges if m.get("role") != "system"],
        memory_text="",
        user_query="What do you remember?",
    )

    # Warm-up
    for _ in range(3):
        budget.select(
            system_prompt="",
            messages=[m for m in exchanges if m.get("role") != "system"][:5],
            user_query="Test",
        )

    # Measure
    times = []
    for _ in range(trials):
        start = time.perf_counter()
        result = budget.select(
            system_prompt=exchanges[0]["content"] if exchanges[0].get("role") == "system" else "",
            messages=[m for m in exchanges if m.get("role") != "system"],
            memory_text="",
            user_query="What do you remember?",
        )
        times.append(time.perf_counter() - start)

    sorted_times = sorted(times)
    n = len(sorted_times)

    return {
        "size": size,
        "exchange_count": len(exchanges),
        "original_tokens": original_tokens,
        "selected_tokens": result.selected_total_tokens,
        "trials": trials,
        "avg_ms": sum(times) / n * 1000,
        "min_ms": min(times) * 1000,
        "max_ms": max(times) * 1000,
        "median_ms": sorted_times[n // 2] * 1000,
        "p95_ms": sorted_times[int(n * 0.95)] * 1000 if n > 1 else sorted_times[0] * 1000,
    }


def main():
    print("=" * 70)
    print("PERFORMANCE BENCHMARK")
    print("=" * 70)

    all_results: Dict[str, Any] = {}
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        try:
            res = bench_performance(size, trials=20)
            all_results[size] = res
            print(f"  Original tokens : {res['original_tokens']:,}")
            print(f"  Selected tokens : {res['selected_tokens']:,}")
            print(f"  Avg time        : {res['avg_ms']:.2f} ms")
            print(f"  Min time        : {res['min_ms']:.2f} ms")
            print(f"  Max time        : {res['max_ms']:.2f} ms")
            print(f"  Median time     : {res['median_ms']:.2f} ms")
            print(f"  P95 time        : {res['p95_ms']:.2f} ms")
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback; traceback.print_exc()
            all_results[size] = {"error": str(e)}

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  {'Size':>10} | {'Tokens':>8} | {'Avg(ms)':>8} | {'Min(ms)':>8} | {'Max(ms)':>8} | {'P95(ms)':>8}")
    print(f"  {'-'*10} | {'-'*8} | {'-'*8} | {'-'*8} | {'-'*8} | {'-'*8}")
    for size in ["short", "medium", "long", "very_long"]:
        res = all_results.get(size, {})
        if "error" in res:
            print(f"  {size:>10} | FAILED")
            continue
        print(f"  {size:>10} | {res['original_tokens']:>8,} | {res['avg_ms']:>8.2f} | {res['min_ms']:>8.2f} | {res['max_ms']:>8.2f} | {res['p95_ms']:>8.2f}")

    output_path = Path(__file__).parent.parent / "reports" / "performance_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
