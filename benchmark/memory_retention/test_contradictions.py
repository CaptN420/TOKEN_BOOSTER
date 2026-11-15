"""Memory Retention Benchmark — Contradiction Handling Test.

Measures whether Boster preserves contradictions and their resolutions.
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


def run_contradiction_test(size: str) -> Dict[str, Any]:
    """Run contradiction handling test."""
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
        user_query="What decisions were made? Were there any changes or contradictions?",
    )
    elapsed = time.perf_counter() - start

    full_text = " ".join(m.get("content", "") for m in result.selected_messages)
    contradictions = gt.get("contradictions", [])

    preserved = 0
    details = []
    for contra in contradictions:
        cid = contra["id"]
        resolution = contra.get("resolution", "")
        claim_a = contra["claim_a"]["text"]
        claim_b = contra["claim_b"]["text"]

        a_in_text = claim_a.lower() in full_text.lower()
        b_in_text = claim_b.lower() in full_text.lower()
        res_in_text = resolution.lower() in full_text.lower() if resolution else False

        if res_in_text:
            preserved += 1
            details.append(f"{cid}: FULL — resolution preserved")
        elif a_in_text or b_in_text:
            preserved += 0.5
            details.append(f"{cid}: PARTIAL — only one claim present")
        else:
            details.append(f"{cid}: LOST — neither claim preserved")

    return {
        "size": size,
        "original_tokens": result.original_total_tokens,
        "selected_tokens": result.selected_total_tokens,
        "reduction_pct": (1 - result.selected_total_tokens / max(result.original_total_tokens, 1)) * 100,
        "processing_time_ms": elapsed * 1000,
        "contradictions_preserved": preserved,
        "contradictions_total": len(contradictions),
        "contradiction_rate": preserved / len(contradictions) if contradictions else 0.0,
        "details": details,
    }


def main():
    print("=" * 70)
    print("CONTRADICTION HANDLING BENCHMARK")
    print("=" * 70)

    all_results: Dict[str, Any] = {}
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        try:
            res = run_contradiction_test(size)
            all_results[size] = res
            print(f"  Original tokens : {res['original_tokens']:,}")
            print(f"  Selected tokens : {res['selected_tokens']:,}")
            print(f"  Reduction       : {res['reduction_pct']:.1f}%")
            print(f"  Contradictions  : {res['contradictions_preserved']}/{res['contradictions_total']} ({res['contradiction_rate']*100:.0f}%)")
            for d in res["details"]:
                print(f"    {d}")
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
        print(f"  {size:>10} | contra={res['contradictions_preserved']}/{res['contradictions_total']} ({res['contradiction_rate']*100:.0f}%) | time={res['processing_time_ms']:.1f}ms")

    output_path = Path(__file__).parent.parent / "reports" / "contradiction_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
