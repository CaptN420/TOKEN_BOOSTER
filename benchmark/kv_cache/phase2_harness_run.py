"""Rejoue le harnais de la phase 1 sur le code de la phase 2.

C'est la seule comparaison strictement comparable : mêmes fixtures, mêmes
``SCENARIO_CONFIG``, mêmes fonctions de mesure (``simulate`` / ``summarise``). Le
pipeline a changé, le harnais non — exactement comme l'exigeait la consigne
(« établir une référence reproductible des résultats actuels »).

Sortie : ``phase2_prefix_steps.json``, de même forme que ``phase1_prefix_steps.json``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from benchmark.kv_cache import prefix_stability as harness

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE1 = os.path.join(HERE, "phase1_prefix_steps.json")
PHASE2 = os.path.join(HERE, "phase2_prefix_steps.json")


def _extras(simulation) -> Dict[str, Any]:
    """The keys the phase-1 dump carried beyond ``summarise``."""
    steps = simulation.steps
    engaging = [s for s in steps if s.block_chars]
    return {
        "block_turns_mean_preserved_pct": (
            round(sum(s.block_preserved_pct for s in engaging) / len(engaging), 2)
            if engaging else 0.0),
        "last_block_chars": engaging[-1].block_chars if engaging else 0,
        "last_prompt_tokens_est": engaging[-1].prompt_tokens_est if engaging else 0,
    }


def main() -> int:
    results = harness.run_matrix(harness.SIZES, harness.SCENARIOS)
    summaries: List[Dict[str, Any]] = []
    for simulation in results:
        summary = dict(harness.summarise(simulation))
        summary.update(_extras(simulation))
        summary["kind"] = "measured"
        summaries.append(summary)

    payload = {
        "note": ("Phase KV CACHE 2 — le harnais de la phase 1 (identique) rejoue sur le "
                 "pipeline de la phase 2. Tokens estimes (chars/4). LCP en caracteres "
                 "(exact). Aucun cache hit moteur revendique."),
        "sizes": list(harness.SIZES),
        "scenarios": list(harness.SCENARIOS),
        "steps": [step.to_dict() for simulation in results for step in simulation.steps],
        "summaries": summaries,
    }
    with open(PHASE2, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
    print(f"ecrit : {PHASE2}")
    print(f"etapes : {len(payload['steps'])}  resumes : {len(summaries)}")

    with open(PHASE1, encoding="utf-8") as handle:
        phase1 = json.load(handle)
    before = {(s["scenario"], s["exchanges"]): s for s in phase1["summaries"]}
    after = {(s["scenario"], s["exchanges"]): s for s in summaries}

    head = (f"{'scen':5} {'taille':>6} | {'A LCP%':>7} {'C LCP%':>7} {'ecart':>7} | "
            f"{'A bloc%':>8} {'C bloc%':>8} | {'A reconst':>9} {'C reconst':>9} | "
            f"{'A append%':>9} {'C append%':>9}")
    print()
    print("A = phase 1 (enregistre)   C = phase 2 (mesure)   -- harnais identique")
    print(head)
    print("-" * len(head))
    for key in sorted(after, key=lambda k: (int(k[1]), k[0])):
        a = before.get(key, {})
        c = after[key]
        a_lcp, c_lcp = a.get("mean_preserved_pct", 0.0), c.get("mean_preserved_pct", 0.0)
        a_blk, c_blk = (a.get("mean_block_preserved_pct", 0.0),
                        c.get("mean_block_preserved_pct", 0.0))
        print(f"{c['scenario']:5} {c['exchanges']:>6} | {a_lcp:>7} {c_lcp:>7} "
              f"{round(c_lcp - a_lcp, 1):>+7} | {a_blk:>8} {c_blk:>8} | "
              f"{a.get('block_rebuilds', '-'):>9} {c.get('block_rebuilds', '-'):>9} | "
              f"{a.get('mean_append_preserved_pct', 0.0):>9} "
              f"{c.get('mean_append_preserved_pct', 0.0):>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
