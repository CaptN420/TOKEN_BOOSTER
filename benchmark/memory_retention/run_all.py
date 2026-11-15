#!/usr/bin/env python3
"""Master runner for all memory retention benchmarks."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmark.memory_retention.generators import ConversationGenerator


def run_all():
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    all_reports = {}

    # 1. Fact Retention
    print("\n" + "=" * 70)
    print("1. FACT RETENTION BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_fact_retention import run_fact_retention_test
    for size in ["short", "medium", "long", "very_long"]:
        try:
            res = run_fact_retention_test(size)
            all_reports[f"fact_{size}"] = res
            print(f"  {size:>10}: facts={res['fact_retention']['rate']*100:.0f}% | rel={res['relationship_retention']['rate']*100:.0f}% | dec={res['decision_retention']['rate']*100:.0f}% | time={res['processing_time_ms']:.2f}ms")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"fact_{size}"] = {"error": str(e)}

    # 2. Relationship Retention
    print("\n" + "=" * 70)
    print("2. RELATIONSHIP RETENTION BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_relationship_retention import run_relationship_test
    for size in ["short", "medium", "long", "very_long", "multi_project"]:
        try:
            res = run_relationship_test(size)
            all_reports[f"rel_{size}"] = res
            print(f"  {size:>15}: {res['relationships_retained']}/{res['relationships_total']} ({res['relationship_retention_rate']*100:.0f}%) | {res['processing_time_ms']:.2f}ms")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"rel_{size}"] = {"error": str(e)}

    # 3. Long-Range Recall
    print("\n" + "=" * 70)
    print("3. LONG-RANGE RECALL BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_long_range_recall import run_long_range_recall_test
    queries = [
        ("What is the project name and what language is it written in?", "name_language"),
        ("Who is involved in the project?", "people"),
        ("What technologies are used?", "technologies"),
    ]
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n  {size.upper()}")
        for query, label in queries:
            try:
                res = run_long_range_recall_test(size, query)
                all_reports[f"lr_{size}_{label}"] = res
                print(f"    {label:>15}: recall={res['long_range_recall_rate']*100:.0f}% ({res['long_range_facts_retained']}/{res['long_range_facts_total']}) | {res['processing_time_ms']:.2f}ms")
            except Exception as e:
                print(f"    {label}: FAILED — {e}")
                all_reports[f"lr_{size}_{label}"] = {"error": str(e)}

    # 4. Temporal Accuracy
    print("\n" + "=" * 70)
    print("4. TEMPORAL ACCURACY BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_temporal_accuracy import run_temporal_test
    for size in ["short", "medium", "long", "very_long"]:
        try:
            res = run_temporal_test(size)
            all_reports[f"temp_{size}"] = res
            print(f"  {size:>10}: temporal={res['temporal_accuracy_rate']*100:.0f}% | contra={res['contradictions_preserved']}/{res['contradictions_total']} | {res['processing_time_ms']:.2f}ms")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"temp_{size}"] = {"error": str(e)}

    # 5. Contradiction Handling
    print("\n" + "=" * 70)
    print("5. CONTRADICTION HANDLING BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_contradictions import run_contradiction_test
    for size in ["short", "medium", "long", "very_long"]:
        try:
            res = run_contradiction_test(size)
            all_reports[f"contra_{size}"] = res
            print(f"  {size:>10}: {res['contradictions_preserved']}/{res['contradictions_total']} ({res['contradiction_rate']*100:.0f}%) | {res['processing_time_ms']:.2f}ms")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"contra_{size}"] = {"error": str(e)}

    # 6. Token Efficiency
    print("\n" + "=" * 70)
    print("6. TOKEN EFFICIENCY BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_token_efficiency import bench_strategy
    strategies = [
        ("no_compression", {"total": 999999, "history": 999999, "system": 999999, "memory": 0, "tools": 0, "retrieved": 0}),
        ("default", {"total": 2000, "history": 1000, "system": 500, "memory": 500, "tools": 500, "retrieved": 500}),
        ("conservative", {"total": 4096, "history": 2000, "system": 1000, "memory": 500, "tools": 500, "retrieved": 500}),
        ("aggressive", {"total": 500, "history": 200, "system": 100, "memory": 100, "tools": 100, "retrieved": 100}),
    ]
    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n  {size.upper()}")
        for name, cfg in strategies:
            try:
                config = BudgetConfig(**cfg)
                res = bench_strategy(size, name, config)
                all_reports[f"token_{size}_{name}"] = res
                print(f"    {name:>20}: {res['selected_tokens']:,} tokens ({res['reduction_pct']:.1f}% red) | {res['processing_time_ms']:.2f}ms")
            except Exception as e:
                print(f"    {name}: FAILED — {e}")
                all_reports[f"token_{size}_{name}"] = {"error": str(e)}

    # 7. Performance
    print("\n" + "=" * 70)
    print("7. PERFORMANCE BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_performance import bench_performance
    for size in ["short", "medium", "long", "very_long"]:
        try:
            res = bench_performance(size, trials=20)
            all_reports[f"perf_{size}"] = res
            print(f"  {size:>10}: avg={res['avg_ms']:.2f}ms | min={res['min_ms']:.2f}ms | max={res['max_ms']:.2f}ms | p95={res['p95_ms']:.2f}ms")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"perf_{size}"] = {"error": str(e)}

    # 8. Cache Awareness
    print("\n" + "=" * 70)
    print("8. CACHE AWARENESS BENCHMARK")
    print("=" * 70)
    from benchmark.memory_retention.test_cache_awareness import bench_cache_impact
    for size in ["short", "medium", "long", "very_long"]:
        try:
            res = bench_cache_impact(size)
            all_reports[f"cache_{size}"] = res
            print(f"  {size:>10}: prefix_changed={res['prefix_changed']} | lines_inv={res['cache_lines_invalidated']} | rec={res['recommendation']}")
        except Exception as e:
            print(f"  {size}: FAILED — {e}")
            all_reports[f"cache_{size}"] = {"error": str(e)}

    # Save master report
    report_path = reports_dir / "BENCHMARK_RESULTS.json"
    report_path.write_text(json.dumps(all_reports, indent=2, ensure_ascii=False))
    print(f"\n{'=' * 70}")
    print(f"ALL BENCHMARKS COMPLETE")
    print(f"Full results: {report_path}")
    print(f"{'=' * 70}")
    return all_reports


if __name__ == "__main__":
    from boster.context_budget import BudgetConfig
    run_all()
