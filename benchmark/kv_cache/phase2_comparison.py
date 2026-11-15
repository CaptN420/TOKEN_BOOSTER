"""Phase KV CACHE 2 — comparaison des architectures, mêmes fixtures que la phase 1.

Ce module MESURE, il ne revendique rien. Trois natures de chiffres sont distinguées
et jamais mélangées (exigence de la consigne) :

  * ``measured``      — produit par le code de la phase 2, tour par tour ;
  * ``recorded``      — chiffres de la phase 1, relus dans
                        ``benchmark/kv_cache/phase1_prefix_steps.json`` (le pipeline
                        d'avant la phase 2 n'est plus exécutable : le code a changé) ;
  * ``counterfactual``— contrefactuel TEXTE du harnais (append-only), qui n'est pas
                        un cache hit mais une longueur de préfixe commun.

Aucun cache hit moteur n'est revendiqué : le moteur local n'expose aucun compteur
(voir ``KV_CACHE_PHASE2_REPORT.md``).

Architectures comparées :
  A  pipeline d'avant la phase 2                      (recorded)
  B  mémoïsation incrémentale                         (measured — réutilisation réelle
                                                       des sections, via le mémo)
  C  zones séparées, bloc dans le prompt système      (measured)
  D  zones séparées, dynamique en suffixe             (measured)
  E  append-only                                      (counterfactual)
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from boster.deterministic_pipeline import PipelineConfig, run_deterministic_pipeline
from boster.memory.rendering import ZONE_ORDER, memo_for, reset_memos

from benchmark.kv_cache.conversation import (
    SYSTEM_PROMPT,
    as_messages,
    build_conversation,
    serialize,
    split_system,
)
from benchmark.kv_cache.prefix_stability import SIZES, SCENARIOS, lcp

BLOCK_HEADER = "[BOSTER CONTEXT]"
PHASE1_JSON = os.path.join(os.path.dirname(__file__), "phase1_prefix_steps.json")
PHASE2_JSON = os.path.join(os.path.dirname(__file__), "phase2_architecture_comparison.json")
BUDGET = 2000
LEVEL = "balanced"


@dataclass
class TurnStep:
    turn: int
    scenario: str
    exchanges: int
    layout: str
    prompt_chars: int
    block_chars: int
    instructions_chars: int
    lcp_prompt_chars: int
    preserved_pct: float
    lcp_block_chars: int
    block_preserved_pct: float
    sections_total: int
    sections_unchanged: int
    sections_changed: int
    memo_preserved_pct: float
    zones: Dict[str, int] = field(default_factory=dict)
    omitted: Dict[str, int] = field(default_factory=dict)
    first_break_region: str = ""
    # Counterfactual (texte) : préfixe commun avec l'append-only du tour précédent.
    append_lcp_chars: int = 0
    append_preserved_pct: float = 0.0


@dataclass
class Result:
    scenario: str
    exchanges: int
    steps: List[TurnStep] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        engaging = [s for s in self.steps if s.block_chars]
        preserved = [s.preserved_pct for s in engaging]
        block_preserved = [s.block_preserved_pct for s in engaging]
        memo = [s.memo_preserved_pct for s in engaging]
        changed = sum(s.sections_changed for s in engaging)
        return {
            "scenario": self.scenario,
            "exchanges": self.exchanges,
            "turns": len(self.steps),
            "turns_with_block": len(engaging),
            "mean_preserved_pct": round(sum(preserved) / len(preserved), 1) if preserved else 0.0,
            "mean_block_preserved_pct": round(sum(block_preserved) / len(block_preserved), 1) if block_preserved else 0.0,
            "mean_memo_preserved_pct": round(sum(memo) / len(memo), 1) if memo else 0.0,
            "sections_changed_total": changed,
            "last_block_chars": engaging[-1].block_chars if engaging else 0,
            "last_prompt_chars": engaging[-1].prompt_chars if engaging else 0,
            "zones_last": engaging[-1].zones if engaging else {},
            "omitted_last": engaging[-1].omitted if engaging else {},
            "kind": "measured",
        }


def _region(offset: int, instructions: int, block_end: int) -> str:
    if offset < instructions:
        return "instructions"
    if offset < block_end:
        return "block"
    return "messages"


def measure(exchanges: int, scenario: str, *, layout: str = "system") -> Result:
    """Run the phase-2 pipeline turn by turn and measure the prefix it produces."""
    turns, _ = build_conversation(exchanges, scenario=scenario)
    conversation_id = f"p2-{scenario}-{exchanges}-{layout}"
    reset_memos()
    memo = memo_for(conversation_id)

    result = Result(scenario=scenario, exchanges=exchanges)
    previous_prompt: Optional[str] = None
    previous_block = ""
    previous_append = ""

    for turn in range(1, exchanges + 1):
        messages = as_messages(turns[:turn])
        before = memo.snapshot()
        output = run_deterministic_pipeline(
            messages, system_prompt=SYSTEM_PROMPT, query=turns[turn - 1].question,
            config=PipelineConfig(max_tokens=BUDGET, level=LEVEL,
                                  conversation_id=conversation_id,
                                  block_position=layout))
        after = memo.snapshot()
        instructions, block = split_system(output.system_prompt, BLOCK_HEADER)
        prompt = serialize(output.system_prompt, output.messages)
        append_only = serialize(SYSTEM_PROMPT, messages)

        detail = [s for s in output.stages
                  if s.name == "7_context_reconstructor"][0].detail
        block_report = detail.get("block", {}) if isinstance(detail.get("block"), dict) else {}
        sections = block_report.get("sections", [])
        diff = memo.diff(before, after)
        changed = len(diff["added"]) + len(diff["changed"])
        total = len(after) or 1

        lcp_prompt = lcp(previous_prompt, prompt) if previous_prompt is not None else 0
        lcp_block = lcp(previous_block, block) if previous_block else 0
        lcp_append = lcp(previous_append, append_only) if previous_append else 0

        step = TurnStep(
            turn=turn, scenario=scenario, exchanges=exchanges, layout=layout,
            prompt_chars=len(prompt), block_chars=len(block),
            instructions_chars=len(instructions),
            lcp_prompt_chars=lcp_prompt,
            preserved_pct=round(100.0 * lcp_prompt / max(len(previous_prompt or ""), 1), 2)
            if previous_prompt is not None else 0.0,
            lcp_block_chars=lcp_block,
            block_preserved_pct=round(100.0 * lcp_block / max(len(previous_block), 1), 2)
            if previous_block else 0.0,
            sections_total=len(sections),
            sections_unchanged=max(len(sections) - changed, 0),
            sections_changed=changed,
            memo_preserved_pct=round(100.0 * max(total - changed, 0) / total, 2),
            zones=dict(block_report.get("zones", {})),
            omitted=dict(block_report.get("omitted", {})),
            first_break_region=_region(lcp_prompt, len(instructions),
                                       len(instructions) + len(block)),
            append_lcp_chars=lcp_append,
            append_preserved_pct=round(
                100.0 * lcp_append / max(len(previous_append), 1), 2)
            if previous_append else 0.0,
        )
        result.steps.append(step)
        previous_prompt, previous_block, previous_append = prompt, block, append_only

    return result


def compare(sizes: Sequence[int] = SIZES, scenarios: Sequence[str] = SCENARIOS
            ) -> Dict[str, Any]:
    phase1 = {}
    if os.path.exists(PHASE1_JSON):
        with open(PHASE1_JSON, encoding="utf-8") as handle:
            phase1 = json.load(handle)

    phase1_by_key = {}
    for summary in phase1.get("summaries", []):
        phase1_by_key[(summary.get("scenario"), summary.get("exchanges"))] = {
            "mean_preserved_pct": summary.get("mean_preserved_pct"),
            "mean_block_preserved_pct": summary.get("mean_block_preserved_pct"),
            "mean_append_preserved_pct": summary.get("mean_append_preserved_pct"),
            "mean_suffix_preserved_pct": summary.get("mean_suffix_preserved_pct"),
            "mean_incremental_preserved_pct": summary.get("mean_incremental_preserved_pct"),
            "block_rebuilds": summary.get("block_rebuilds"),
            "kind": "recorded",
        }

    measured = []
    for size in sizes:
        for scenario in scenarios:
            measured.append(measure(size, scenario, layout="system"))
    suffix = [measure(100, scenario, layout="suffix") for scenario in scenarios]

    rows = []
    for result in measured:
        row = result.summary()
        row["arch_a_recorded"] = phase1_by_key.get((result.scenario, result.exchanges), {})
        engaging = [s for s in result.steps if s.block_chars]
        row["arch_e_append_pct"] = round(
            sum(s.append_preserved_pct for s in engaging) / len(engaging), 1
        ) if engaging else 0.0
        rows.append(row)

    payload = {
        "note": ("Phase KV CACHE 2 — mesures. Tokens estimes (chars/4). LCP en "
                 "caracteres (exact). 'recorded' = phase 1 relu depuis le JSON de la "
                 "phase 1 ; 'counterfactual' = contrefactuel texte. Aucun cache hit "
                 "moteur revendique."),
        "sizes": list(sizes),
        "scenarios": list(scenarios),
        "measured": rows,
        "steps": [asdict(step) for result in measured for step in result.steps],
        "suffix_100": [result.summary() for result in suffix],
        "suffix_steps": [asdict(step) for result in suffix for step in result.steps],
    }
    with open(PHASE2_JSON, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
    return payload


def format_comparison(payload: Dict[str, Any]) -> str:
    head = (f"{'scen':5} {'taille':>6} {'bloc':>5} {'A(ph1)%':>8} {'C(ph2)%':>8} "
            f"{'bloc%':>7} {'B(memo)%':>9} {'E(append)%':>11} {'sect.mod.':>10} "
            f"{'zones':>18}")
    lines = [head, "-" * len(head)]
    for row in payload["measured"]:
        recorded = row.get("arch_a_recorded") or {}
        zones = row.get("zones_last") or {}
        lines.append(
            f"{row['scenario']:5} {row['exchanges']:>6} {row['turns_with_block']:>5} "
            f"{recorded.get('mean_preserved_pct', '-')!s:>8} "
            f"{row['mean_preserved_pct']!s:>8} {row['mean_block_preserved_pct']!s:>7} "
            f"{row['mean_memo_preserved_pct']!s:>9} {row['arch_e_append_pct']!s:>11} "
            f"{row['sections_changed_total']!s:>10} "
            f"{str(tuple(zones.get(z, 0) for z in ZONE_ORDER)):>18}"
        )
    lines.append("")
    lines.append("Derniere ligne de chaque scenario (taille 100) : suffixe vs systeme")
    lines.append(f"{'scen':5} {'systeme%':>9} {'suffixe%':>9} {'bloc sys':>9} {'bloc suf':>9}")
    for row in payload["suffix_100"]:
        system_row = next(r for r in payload["measured"]
                          if r["scenario"] == row["scenario"] and r["exchanges"] == 100)
        lines.append(f"{row['scenario']:5} {system_row['mean_preserved_pct']!s:>9} "
                     f"{row['mean_preserved_pct']!s:>9} {system_row['last_block_chars']!s:>9} "
                     f"{row['last_block_chars']!s:>9}")
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    print("Phase KV CACHE 2 — comparaison des architectures")
    print("LCP en caracteres (exact) ; tokens estimes (chars/4). Aucun cache hit revendique.")
    print()
    payload = compare()
    print(format_comparison(payload))
    print()
    print(f"ecrit : {PHASE2_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
