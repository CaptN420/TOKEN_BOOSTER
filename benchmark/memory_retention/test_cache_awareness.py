"""Memory Retention Benchmark — Cache Awareness Test.

Measures KV cache impact of different compression strategies.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from boster.context_budget import ContextBudget, BudgetConfig
from boster.intelligence.cache_awareness import CacheAwareness
from benchmark.memory_retention.generators import ConversationGenerator


def bench_cache_impact(size: str) -> Dict[str, Any]:
    """Measure cache impact of compression."""
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

    # Run compression
    result = budget.select(
        system_prompt=exchanges[0]["content"] if exchanges[0].get("role") == "system" else "",
        messages=[m for m in exchanges if m.get("role") != "system"],
        memory_text="",
        user_query="What do you remember?",
    )

    # Analyze cache impact
    cache = CacheAwareness()
    selected_text = json.dumps(result.selected_messages, ensure_ascii=False)
    impact = cache.analyze_prefix_change(original_text, selected_text)

    return {
        "size": size,
        "original_tokens": original_tokens,
        "selected_tokens": result.selected_total_tokens,
        "prefix_changed": impact.prefix_changed,
        "cache_lines_invalidated": impact.cache_lines_invalidated,
        "estimated_recompute_tokens": impact.estimated_recompute_tokens,
        "recommendation": impact.recommendation,
        "details": impact.details,
    }


def main():
    print("=" * 70)
    print("CACHE AWARENESS BENCHMARK")
    print("=" * 70)

    all_results: Dict[str, Any] = {}
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        try:
            res = bench_cache_impact(size)
            all_results[size] = res
            print(f"  Original tokens : {res['original_tokens']:,}")
            print(f"  Selected tokens : {res['selected_tokens']:,}")
            print(f"  Prefix changed  : {res['prefix_changed']}")
            print(f"  Lines invalid.  : {res['cache_lines_invalidated']}")
            print(f"  Recompute est.  : {res['estimated_recompute_tokens']:,} tokens")
            print(f"  Recommendation  : {res['recommendation']}")
            for d in res["details"]:
                print(f"    • {d}")
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback; traceback.print_exc()
            all_results[size] = {"error": str(e)}

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for size, res in all_results.items():
        if "error" in res:
            print(f"  {size}: FAILED — {res['error']}")
            continue
        print(f"  {size:>10} | prefix_changed={res['prefix_changed']} | lines_inv={res['cache_lines_invalidated']} | rec={res['recommendation']}")

    output_path = Path(__file__).parent.parent / "reports" / "cache_awareness_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
