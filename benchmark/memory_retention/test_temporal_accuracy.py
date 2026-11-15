"""Memory Retention Benchmark — Temporal Accuracy Test.

Measures whether Boster distinguishes current requirements from historical ones.
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


def run_temporal_test(size: str) -> Dict[str, Any]:
    """Run temporal accuracy test."""
    generator = ConversationGenerator()
    all_convs = generator.generate_all()
    conv = all_convs[size]
    exchanges = conv["exchanges"]
    gt = conv["ground_truth"]

    from boster.core.tokenizer import count_tokens
    original_text = json.dumps(exchanges, ensure_ascii=False)
    original_tokens = count_tokens(original_text)

    budget = ContextBudget(
        model="gpt-4",
        config=BudgetConfig(total=2000, history=1000, system=500, memory=500, tools=500, retrieved=500),
    )

    start = time.perf_counter()
    result = budget.select(
        system_prompt=exchanges[0]["content"] if exchanges[0].get("role") == "system" else "",
        messages=[m for m in exchanges if m.get("role") != "system"],
        memory_text="",
        user_query="What are the current requirements? What changed over time?",
    )
    elapsed = time.perf_counter() - start

    full_text = " ".join(m.get("content", "") for m in result.selected_messages)
    temporal = gt.get("temporal", [])
    contradictions = gt.get("contradictions", [])

    # Check current events are retained
    current_events = [t for t in temporal if t.get("state") == "current"]
    superseded_events = [t for t in temporal if t.get("state") == "superseded"]

    current_retained = sum(
        1 for e in current_events
        if e.get("event", "").lower() in full_text.lower()
    )

    # Check contradictions are resolved correctly
    contra_preserved = 0
    for contra in contradictions:
        resolution = contra.get("resolution", "")
        if resolution and resolution.lower() in full_text.lower():
            contra_preserved += 1

    return {
        "size": size,
        "original_tokens": result.original_total_tokens,
        "selected_tokens": result.selected_total_tokens,
        "reduction_pct": (1 - result.selected_total_tokens / max(result.original_total_tokens, 1)) * 100,
        "processing_time_ms": elapsed * 1000,
        "temporal_current_retained": current_retained,
        "temporal_current_total": len(current_events),
        "temporal_accuracy_rate": current_retained / len(current_events) if current_events else 0.0,
        "superseded_events_count": len(superseded_events),
        "contradictions_preserved": contra_preserved,
        "contradictions_total": len(contradictions),
    }


def main():
    print("=" * 70)
    print("TEMPORAL ACCURACY BENCHMARK")
    print("=" * 70)

    all_results: Dict[str, Any] = {}
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        try:
            res = run_temporal_test(size)
            all_results[size] = res
            print(f"  Original tokens : {res['original_tokens']:,}")
            print(f"  Selected tokens : {res['selected_tokens']:,}")
            print(f"  Reduction       : {res['reduction_pct']:.1f}%")
            print(f"  Temporal accuracy: {res['temporal_accuracy_rate']*100:.0f}% ({res['temporal_current_retained']}/{res['temporal_current_total']})")
            print(f"  Contradictions  : {res['contradictions_preserved']}/{res['contradictions_total']} preserved")
            print(f"  Time            : {res['processing_time_ms']:.2f} ms")
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
        print(f"  {size:>10} | temporal={res['temporal_accuracy_rate']*100:.0f}% | contra={res['contradictions_preserved']}/{res['contradictions_total']} | time={res['processing_time_ms']:.1f}ms")

    output_path = Path(__file__).parent.parent / "reports" / "temporal_accuracy_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
