"""Memory Retention Benchmark — Token Efficiency Test.

Compares multiple selection strategies and measures token efficiency.
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


def bench_strategy(size: str, strategy: str, config: BudgetConfig) -> Dict[str, Any]:
    """Benchmark a single compression strategy."""
    generator = ConversationGenerator()
    all_convs = generator.generate_all()
    conv = all_convs[size]
    exchanges = conv["exchanges"]

    from boster.core.tokenizer import count_tokens
    original_text = json.dumps(exchanges, ensure_ascii=False)
    original_tokens = count_tokens(original_text)

    budget = ContextBudget(model="gpt-4", config=config)

    start = time.perf_counter()
    result = budget.select(
        system_prompt=exchanges[0]["content"] if exchanges[0].get("role") == "system" else "",
        messages=[m for m in exchanges if m.get("role") != "system"],
        memory_text="",
        user_query="What do you remember?",
    )
    elapsed = time.perf_counter() - start

    return {
        "size": size,
        "strategy": strategy,
        "original_tokens": original_tokens,
        "selected_tokens": result.selected_total_tokens,
        "reduction_pct": (1 - result.selected_total_tokens / max(original_tokens, 1)) * 100,
        "processing_time_ms": elapsed * 1000,
        "budget_total": config.total,
        "history_items_dropped": result.history_items_dropped,
    }


def main():
    print("=" * 70)
    print("TOKEN EFFICIENCY BENCHMARK")
    print("=" * 70)

    strategies = [
        ("no_compression", BudgetConfig(total=999999, history=999999, system=999999, memory=0, tools=0, retrieved=0)),
        ("default", BudgetConfig(total=2000, history=1000, system=500, memory=500, tools=500, retrieved=500)),
        ("conservative", BudgetConfig(total=4096, history=2000, system=1000, memory=500, tools=500, retrieved=500)),
        ("aggressive", BudgetConfig(total=500, history=200, system=100, memory=100, tools=100, retrieved=100)),
    ]

    all_results: Dict[str, Dict[str, Any]] = {}

    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        all_results[size] = {}
        for name, config in strategies:
            try:
                res = bench_strategy(size, name, config)
                all_results[size][name] = res
                print(f"  {name:>20} | orig={res['original_tokens']:,} | sel={res['selected_tokens']:,} | red={res['reduction_pct']:.1f}% | time={res['processing_time_ms']:.2f}ms")
            except Exception as e:
                print(f"  {name}: FAILED — {e}")
                all_results[size][name] = {"error": str(e)}

    # Comparison table
    print("\n" + "=" * 70)
    print("STRATEGY COMPARISON")
    print("=" * 70)
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n  {size.upper()}")
        for name, res in all_results.get(size, {}).items():
            if "error" in res:
                continue
            print(f"    {name:>20}: {res['selected_tokens']:,} tokens ({res['reduction_pct']:.1f}% reduction) in {res['processing_time_ms']:.2f}ms")

    output_path = Path(__file__).parent.parent / "reports" / "token_efficiency_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
