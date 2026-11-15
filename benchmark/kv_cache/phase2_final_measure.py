"""Mesures finales de la phase KV CACHE 2 — chiffres cités dans le rapport.

Sortie volontairement brute : chaque ligne imprimée est recopiable dans
``KV_CACHE_PHASE2_REPORT.md``.
"""
from __future__ import annotations

import re
import sys
from statistics import mean

from boster.deterministic_pipeline import (
    PipelineConfig,
    run_deterministic_pipeline,
    CONTEXT_BLOCK_HEADER,
)
from boster.memory.rendering import ZONE_ORDER, memo_for, reset_memos
from benchmark.kv_cache.conversation import (
    SYSTEM_PROMPT,
    as_messages,
    build_conversation,
    serialize,
    split_system,
    FINAL_QUESTIONS,
)
from benchmark.kv_cache.prefix_stability import lcp

BUDGET, LEVEL = 2000, "balanced"


def run(turns, count, conv, query=None, position="system", budget=BUDGET):
    out = run_deterministic_pipeline(
        as_messages(turns[:count]), system_prompt=SYSTEM_PROMPT,
        query=query or turns[count - 1].question,
        config=PipelineConfig(max_tokens=budget, level=LEVEL, conversation_id=conv,
                              block_position=position))
    detail = [s for s in out.stages if s.name == "7_context_reconstructor"][0].detail
    instructions, block = split_system(out.system_prompt, CONTEXT_BLOCK_HEADER)
    return out, detail, instructions, block


print("=" * 78)
print("(a) ETAT GELE — seule la question change (etat memoire identique)")
print("=" * 78)
turns, _ = build_conversation(100, scenario="B")
reset_memos()
blocks = {}
for key, question in FINAL_QUESTIONS.items():
    _, _, _, block = run(turns, 100, "final-freeze", query=question)
    blocks[key] = block
base = blocks["current"]
for key, block in blocks.items():
    value = lcp(base, block)
    print(f"  LCP(current,{key:<10}) = {value:>5}c / {len(base):>5}c "
          f"= {100.0 * value / len(base):>5.1f}%   (bloc question={len(block)}c)")

print()
print("=" * 78)
print("(b) TOUR PAR TOUR — scenarios B, D (decision), E (correction)")
print("=" * 78)
for scenario in ("B", "D", "E"):
    turns, _ = build_conversation(100, scenario=scenario)
    reset_memos()
    conv = f"final-{scenario}"
    memo = memo_for(conv)
    previous_prompt = previous_block = None
    print(f"\n  --- scenario {scenario} ---")
    preserved_block, preserved_prompt = [], []
    for turn in range(1, 101):
        before = memo.snapshot()
        out, detail, instructions, block = run(turns, turn, conv)
        after = memo.snapshot()
        diff = memo.diff(before, after)
        prompt = serialize(out.system_prompt, out.messages)
        if previous_prompt is not None:
            lp = lcp(previous_prompt, prompt)
            lb = lcp(previous_block, block)
            preserved_prompt.append(100.0 * lp / max(len(previous_prompt), 1))
            preserved_block.append(100.0 * lb / max(len(previous_block), 1))
            if turn >= 96:
                print(f"    t{turn:>3} prompt={len(prompt):>5}c bloc={len(block):>5}c "
                      f"LCP_prompt={lp:>5}c ({preserved_prompt[-1]:>5.1f}%) "
                      f"LCP_bloc={lb:>5}c ({preserved_block[-1]:>5.1f}%) "
                      f"reuse={len(diff['unchanged'])}/{len(after)} "
                      f"modif={len(diff['changed'])} ajout={len(diff['added'])}")
        previous_prompt, previous_block = prompt, block
    print(f"    moyenne tours 2-100 (engageants) : prompt {mean(preserved_prompt):.1f}% "
          f"/ bloc {mean(preserved_block):.1f}%   (min bloc {min(preserved_block):.1f}%)")
    zones = detail["block"]["zones"]
    print(f"    dernier tour : zones={{{', '.join(f'{z}:{zones.get(z, 0)}' for z in ZONE_ORDER)}}} "
          f"omis={detail['block']['omitted']}")
    print(f"    sections={[s['name'] for s in detail['block']['sections']]}")
    print(f"    memoire du memo : {memo.stats()}")

print()
print("=" * 78)
print("(c) TAILLE ET COMPOSITION (scenario B, tour 100)")
print("=" * 78)
turns, _ = build_conversation(100, scenario="B")
reset_memos()
out, detail, instructions, block = run(turns, 100, "final-size")
print(f"  instructions={len(instructions)}c  bloc={len(block)}c  "
      f"messages={len(serialize('', out.messages))}c")
print(f"  total prompt={len(serialize(out.system_prompt, out.messages))}c")
print(f"  zones (lignes)={detail['block']['zones']}")
print(f"  zone (caracteres)={detail['block']['zone_chars']}")

print()
print("=" * 78)
print("(d) ARRANGEMENT SUFFIXE (objectif E)")
print("=" * 78)
question = FINAL_QUESTIONS["unknown"]
reset_memos()
out_sys, _, _, block_sys = run(turns, 100, "final-sys", query=question)
reset_memos()
out_suf, detail_suf, _, block_suf = run(turns, 100, "final-suf", query=question,
                                        position="suffix")
print(f"  system : system_prompt={len(out_sys.system_prompt)}c "
      f"messages={len(out_sys.messages)} dernier_role={out_sys.messages[-1]['role']}")
print(f"  suffix : system_prompt={len(out_suf.system_prompt)}c "
      f"messages={len(out_suf.messages)} dernier_role={out_suf.messages[-1]['role']}")
print(f"  zone dynamique hors du systeme (suffixe) : "
      f"{'unknown:' not in out_suf.system_prompt}")
print(f"  zone dynamique dans le dernier message : "
      f"{'unknown:' in out_suf.messages[-1]['content']}")
