"""Memory Retention Benchmark — Relationship Retention Test (Structured).

Measures whether Boster preserves relationships when compressing conversation history.
Reports exact structured retention, not just keyword presence.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from boster.memory.models import MemoryItem, Entity, Relation, Decision, Contradiction, RecentContext, SemanticMemory
from boster.memory.consolidation import consolidate_exchanges
from benchmark.memory_retention.generators import ConversationGenerator


def run_relationship_retention_test(size: str) -> Dict[str, Any]:
    """Run relationship retention test for a given conversation size."""
    generator = ConversationGenerator()
    all_convs = generator.generate_all()
    conv = all_convs[size]
    exchanges = conv["exchanges"]
    gt = conv["ground_truth"]

    # Run consolidation
    recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=5)

    # Build selected messages
    selected_messages = []
    for item in semantic_mem.items:
        selected_messages.append({"role": "system", "content": item.content})
    for ex in recent_ctx.messages:
        selected_messages.append(ex)

    original_text = json.dumps(exchanges, ensure_ascii=False)
    original_tokens = len(original_text) // 4
    selected_text = json.dumps(selected_messages, ensure_ascii=False)
    selected_tokens = len(selected_text) // 4

    start_time = time.perf_counter()
    elapsed = time.perf_counter() - start_time

    # Build full text for mention retention
    full_text = " ".join(m.get("content", "") for m in selected_messages)

    # Evaluate fact mention retention
    facts = gt.get("facts", [])
    fact_mentions = 0
    for fact in facts:
        keywords = [w for w in fact["text"].split() if len(w) > 3]
        if any(kw.lower() in full_text.lower() for kw in keywords):
            fact_mentions += 1

    # Evaluate structured entity retention
    entities_found = 0
    for fact in facts:
        keywords = [w for w in fact["text"].split() if len(w) > 3]
        entity_match = any(
            kw.lower() in [e.name.lower() for e in semantic_mem.entities.values()]
            for kw in keywords
        )
        if entity_match:
            entities_found += 1

    # Evaluate structured relation retention (exact match)
    rels = gt.get("relationships", [])
    rel_retained = 0
    for rel in rels:
        subject = rel.get("subject", "")
        predicate = rel.get("type", "")
        obj = rel.get("object", "")
        found = any(
            r.subject.lower() == subject.lower() and
            r.predicate.lower() == predicate.lower() and
            r.object.lower() == obj.lower()
            for r in semantic_mem.relations
        )
        if found:
            rel_retained += 1

    # Evaluate decision retention
    decisions = gt.get("decisions", [])
    dec_retained = 0
    for dec in decisions:
        keywords = [w for w in dec["text"].split() if len(w) > 4]
        found = any(kw.lower() in d.value.lower() for kw in keywords for d in semantic_mem.decisions)
        if found:
            dec_retained += 1

    # Evaluate contradiction preservation
    contradictions = gt.get("contradictions", [])
    contra_preserved = 0
    for contra in contradictions:
        resolution = contra.get("resolution", "")
        if resolution and resolution.lower() in full_text.lower():
            contra_preserved += 1
        elif contra.get("resolved"):
            claim_a = contra["claim_a"]["text"]
            claim_b = contra["claim_b"]["text"]
            if claim_a.lower() in full_text.lower() or claim_b.lower() in full_text.lower():
                contra_preserved += 0.5

    return {
        "size": size,
        "exchange_count": len(exchanges),
        "original_tokens": original_tokens,
        "selected_tokens": selected_tokens,
        "reduction_pct": (1 - selected_tokens / max(original_tokens, 1)) * 100,
        "processing_time_ms": elapsed * 1000,
        "mention_retention": {
            "rate": fact_mentions / len(facts) if facts else 1.0,
            "retained": fact_mentions,
            "total": len(facts),
        },
        "structured_entity_retention": {
            "rate": entities_found / len(facts) if facts else 1.0,
            "retained": entities_found,
            "total": len(facts),
        },
        "relationship_retention": {
            "rate": rel_retained / len(rels) if rels else 1.0,
            "retained": rel_retained,
            "total": len(rels),
        },
        "decision_retention": {
            "rate": dec_retained / len(decisions) if decisions else 1.0,
            "retained": dec_retained,
            "total": len(decisions),
        },
        "contradiction_preservation": {
            "rate": contra_preserved / len(contradictions) if contradictions else 1.0,
            "preserved": contra_preserved,
            "total": len(contradictions),
        },
        "semantic_items": len(semantic_mem.items),
        "entities": len(semantic_mem.entities),
        "relations": len(semantic_mem.relations),
        "decisions": len(semantic_mem.decisions),
        "contradictions": len(semantic_mem.contradictions),
    }


def main():
    print("=" * 70)
    print("RELATIONSHIP RETENTION BENCHMARK (Three-Tier Memory - Structured)")
    print("=" * 70)

    generator = ConversationGenerator()
    all_convs = generator.generate_all()
    all_results = {}

    for size in ["short", "medium", "long", "very_long"]:
        print(f"\n--- {size.upper()} ({all_convs[size]['exchange_count']} exchanges) ---")
        try:
            res = run_relationship_retention_test(size)
            all_results[size] = res
            print(f"  Mention retention   : {res['mention_retention']['rate']*100:.1f}% ({res['mention_retention']['retained']}/{res['mention_retention']['total']})")
            print(f"  Struct. entity ret. : {res['structured_entity_retention']['rate']*100:.1f}% ({res['structured_entity_retention']['retained']}/{res['structured_entity_retention']['total']})")
            print(f"  Struct. relation ret.: {res['relationship_retention']['rate']*100:.1f}% ({res['relationship_retention']['retained']}/{res['relationship_retention']['total']})")
            print(f"  Decision ret.       : {res['decision_retention']['rate']*100:.1f}% ({res['decision_retention']['retained']}/{res['decision_retention']['total']})")
            print(f"  Contradiction       : {res['contradiction_preservation']['rate']*100:.1f}% ({res['contradiction_preservation']['preserved']}/{res['contradiction_preservation']['total']})")
            print(f"  Semantic items      : {res['semantic_items']}")
            print(f"  Entities            : {res['entities']}")
            print(f"  Relations           : {res['relations']}")
            print(f"  Decisions           : {res['decisions']}")
            print(f"  Contradictions      : {res['contradictions']}")
        except Exception as e:
            print(f"  FAILED: {e}")
            import traceback; traceback.print_exc()
            all_results[size] = {"error": str(e)}

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for size, res in all_results.items():
        if "error" in res:
            print(f"  {size}: FAILED — {res['error']}")
            continue
        print(f"  {size:>10} | mention={res['mention_retention']['rate']*100:.0f}% | entity={res['structured_entity_retention']['rate']*100:.0f}% | rel={res['relationship_retention']['rate']*100:.0f}% | dec={res['decision_retention']['rate']*100:.0f}% | contra={res['contradiction_preservation']['rate']*100:.0f}%")

    # Save results
    output_path = Path(__file__).parent.parent / "reports" / "relationship_retention_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")
    return all_results


if __name__ == "__main__":
    main()
