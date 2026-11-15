"""Memory Retention Benchmark — Long-Range Recall Test.

Measures whether Boster can recall information from the beginning of long conversations.
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


def run_long_range_recall_test(size: str, query: str) -> Dict[str, Any]:
    """Run long-range recall test with a specific query."""
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
        user_query=query,
    )
    elapsed = time.perf_counter() - start

    # Evaluate: check if facts from early messages are retained
    full_text = " ".join(m.get("content", "") for m in result.selected_messages)
    facts = gt.get("facts", [])

    # Facts from first 10 messages (long-range)
    long_range_facts = [f for f in facts if "msg_" in str(f.get("source", ""))]
    retained = 0
    missing = []
    for fact in long_range_facts:
        text = fact["text"]
        keywords = [w for w in text.split() if len(w) > 3]
        if any(kw.lower() in full_text.lower() for kw in keywords):
            retained += 1
        else:
            missing.append(f"{fact['id']}: {text[:50]}")

    return {
        "size": size,
        "query": query,
        "original_tokens": result.original_total_tokens,
        "selected_tokens": result.selected_total_tokens,
        "reduction_pct": (1 - result.selected_total_tokens / max(result.original_total_tokens, 1)) * 100,
        "processing_time_ms": elapsed * 1000,
        "long_range_facts_total": len(long_range_facts),
        "long_range_facts_retained": retained,
        "long_range_recall_rate": retained / len(long_range_facts) if long_range_facts else 0.0,
        "missing_facts": missing,
    }


def main():
    print("=" * 70)
    print("LONG-RANGE RECALL BENCHMARK")
    print("=" * 70)

    queries = [
        ("What is the project name and what language is it written in?", "name_language"),
        ("Who is involved in the project?", "people"),
        ("What technologies are used?", "technologies"),
        ("What are the key decisions made?", "decisions"),
    ]

    all_results: Dict[str, Dict[str, Any]] = {}

    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ---")
        try:
            for query, label in queries:
                res = run_long_range_recall_test(size, query)
                key = f"{size}_{label}"
                all_results[key] = res
                print(f"  Q[{label}]: recall={res['long_range_recall_rate']*100:.0f}% ({res['long_range_facts_retained']}/{res['long_range_facts_total']}) | tokens={res['selected_tokens']:,} | {res['processing_time_ms']:.1f}ms")
                if res["missing_facts"]:
                    for m in res["missing_facts"][:3]:
                        print(f"    MISSING: {m}")
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback; traceback.print_exc()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for size in ["short", "medium", "long", "very_long"]:
        row = [all_results.get(f"{size}_{q}", {}).get("long_range_recall_rate", 0) for _, q in queries]
        avg = sum(row) / len(row) if row else 0
        print(f"  {size:>10} | avg_recall={avg*100:.0f}% | queries={len([r for r in row if r > 0])}/{len(queries)}")

    output_path = Path(__file__).parent.parent / "reports" / "long_range_recall_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
