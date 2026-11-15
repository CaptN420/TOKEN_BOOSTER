"""Prefix-stability measurement for the KV-cache audit (Phase KV CACHE 1).

What is measured, in characters (exact) and in tokens (estimated, `chars/4`,
no tokenizer installed):

* the longest common prefix (LCP) between the prompt of turn *t-1* and turn *t* —
  this is the region a prefix-caching engine can reuse, and nothing more;
* **where** that prefix breaks: inside the stable instructions, inside the
  volatile context block, or in the message list;
* the tokens added / removed by the break;
* for each turn, the block's own LCP against the previous block.

Three arrangements are compared for the *same* pipeline output, so the
comparison isolates the ordering and nothing else:

| Arrangement | Layout |
|---|---|
| `actual` | `instructions + block + messages` — the pipeline's own layout |
| `suffix` | `instructions + messages + block` — volatile content at the end |
| `incremental` | `actual`, but the block is reused byte-identically while the memory is unchanged |

`incremental` separates the two causes of churn: memory that really changed,
versus a block that is merely regenerated (notably because it is conditioned on
the question).

A character-level LCP is a NECESSARY condition for KV-cache reuse, never a proof
of it: the real token stream depends on the model's chat template, and only the
engine can report what it actually reused. No number here is a cache hit.

Run:  PYTHONPATH="." uv run python -m benchmark.kv_cache.prefix_stability
      PYTHONPATH="." uv run python -m benchmark.kv_cache.prefix_stability --json out.json
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from boster.core.tokenizer import count_tokens
from boster.deterministic_pipeline import (
    CONTEXT_BLOCK_HEADER,
    PipelineConfig,
    run_deterministic_pipeline,
)

from .conversation import (
    SYSTEM_PROMPT,
    as_messages,
    build_conversation,
    serialize,
    split_system,
)

__all__ = ["StepMetric", "simulate", "summarise", "format_table", "run_matrix", "SIZES", "SCENARIOS"]

SIZES = (10, 25, 50, 100)
SCENARIOS = ("A", "B", "C", "D", "E", "F", "G", "H")

SCENARIO_LABELS = {
    "A": "sans memoire",
    "B": "memoire persistante",
    "C": "consolidation tardive",
    "D": "changement de decision",
    "E": "correction d'un fait ancien",
    "F": "question historique",
    "G": "reconstruction complete",
    "H": "budget contraignant",
}
SCENARIO_CONFIG: Dict[str, Dict[str, Any]] = {
    "A": {"use_memory": False},
    "B": {"use_memory": True},
    "C": {"use_memory": True, "memory_from_turn": 12},
    "D": {"use_memory": True, "late": "decision_change"},
    "E": {"use_memory": True, "late": "fact_correction"},
    "F": {"use_memory": True},
    "G": {"use_memory": True, "reconstruct": True},
    "H": {"use_memory": True, "max_tokens": 800},
}


def lcp(left: str, right: str) -> int:
    """Length of the longest common prefix, in characters."""
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def common_prefix_all(texts: Sequence[str]) -> int:
    """LCP shared by every text in the list."""
    if not texts:
        return 0
    length = min(len(t) for t in texts)
    for position in range(length):
        char = texts[0][position]
        if any(t[position] != char for t in texts[1:]):
            return position
    return length


def _region(offset: int, instructions: int, block_end: int) -> str:
    """Where a prefix break lands, given the layout markers."""
    if offset <= instructions:
        return "instructions"
    if offset < block_end:
        return "block"
    return "messages"


@dataclass
class StepMetric:
    turn: int
    scenario: str
    exchanges: int
    # sizes
    prompt_chars: int
    prompt_tokens_est: int
    instructions_chars: int
    block_chars: int
    messages_chars: int
    # prefix stability, actual layout
    lcp_chars: int
    lcp_tokens_est: int
    break_region: str
    break_offset: int
    added_chars: int
    removed_chars: int
    preserved_pct: float
    # block's own stability
    block_lcp_chars: int
    block_preserved_pct: float
    # counterfactuals
    suffix_lcp_chars: int
    suffix_preserved_pct: float
    incremental_lcp_chars: int
    incremental_preserved_pct: float
    append_lcp_chars: int
    append_preserved_pct: float
    memory_changed: bool
    variant: str
    count_kind: str = "estimated"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Simulation:
    scenario: str
    exchanges: int
    steps: List[StepMetric] = field(default_factory=list)
    stable_prefix_actual: int = 0
    stable_prefix_suffix: int = 0
    turns_with_block: int = 0
    block_rebuilds: int = 0
    memory_changes: int = 0


def simulate(exchanges: int, scenario: str = "B", *, max_tokens: int = 2000,
             level: str = "balanced", conversation_id: str = "") -> Simulation:
    """Run the pipeline once per turn and measure the prefix it produces."""
    config = SCENARIO_CONFIG.get(scenario, {})
    use_memory: bool = config.get("use_memory", True)
    reconstruct: bool = config.get("reconstruct", True)
    budget: int = config.get("max_tokens", max_tokens)
    memory_from_turn: int = config.get("memory_from_turn", 1)

    turns, question = build_conversation(exchanges, scenario=scenario)
    simulation = Simulation(scenario=scenario, exchanges=exchanges)

    previous: Optional[Dict[str, str]] = None
    previous_signature: Optional[Tuple[Any, ...]] = None
    previous_block: str = ""
    incremental_block: str = ""
    prompts: List[str] = []
    suffix_prompts: List[str] = []

    for turn in turns:
        messages = as_messages(turns[: turn.index])
        memory_on = use_memory and turn.index >= memory_from_turn
        output = run_deterministic_pipeline(
            messages,
            system_prompt=SYSTEM_PROMPT,
            query=turn.question,
            config=PipelineConfig(
                max_tokens=budget, level=level, model="",
                conversation_id=conversation_id or f"kv-{scenario}-{exchanges}",
                use_memory=memory_on, reconstruct=reconstruct,
            ),
        )
        instructions, block = split_system(output.system_prompt, CONTEXT_BLOCK_HEADER)
        produced = [{"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
                    for m in output.messages]
        prompt = serialize(output.system_prompt, produced)
        suffix_prompt = serialize(instructions, produced) + block
        # append-only baseline: the conversation is never rewritten, the volatile
        # block goes last — the layout an append-only client would produce
        append_prompt = serialize(instructions, as_messages(turns[: turn.index])) + block

        signature = _memory_signature(output)
        memory_changed = previous_signature is not None and signature != previous_signature
        if block and block != previous_block:
            simulation.block_rebuilds += 1
        if memory_changed:
            simulation.memory_changes += 1
        if block:
            simulation.turns_with_block += 1
        # incremental: keep the previous block while the memory is unchanged
        incremental_block = (previous_block
                             if (block and previous_block and not memory_changed) else block)
        incremental_prompt = serialize(
            instructions + ("\n\n" + incremental_block if incremental_block else ""), produced)
        if not block:
            incremental_prompt = prompt

        step = StepMetric(
            turn=turn.index, scenario=scenario, exchanges=exchanges,
            prompt_chars=len(prompt), prompt_tokens_est=count_tokens(prompt, ""),
            instructions_chars=len(instructions), block_chars=len(block),
            messages_chars=len(prompt) - len(instructions) - len(block),
            lcp_chars=0, lcp_tokens_est=0, break_region="none", break_offset=0,
            added_chars=0, removed_chars=0, preserved_pct=0.0,
            block_lcp_chars=0, block_preserved_pct=0.0,
            suffix_lcp_chars=0, suffix_preserved_pct=0.0,
            incremental_lcp_chars=0, incremental_preserved_pct=0.0,
            append_lcp_chars=0, append_preserved_pct=0.0,
            memory_changed=memory_changed,
            variant=str(_stage_detail(output, "7_context_reconstructor").get("variant", "n/a")),
        )
        if previous is not None:
            common = lcp(previous["prompt"], prompt)
            block_end = len(instructions) + len(block) + 2
            step.lcp_chars = common
            step.lcp_tokens_est = count_tokens(prompt[:common], "")
            step.break_offset = common
            step.break_region = _region(common, len(instructions), block_end)
            step.added_chars = len(prompt) - common
            step.removed_chars = len(previous["prompt"]) - common
            step.preserved_pct = round(100.0 * common / max(len(previous["prompt"]), 1), 2)
            if previous["block"] or block:
                step.block_lcp_chars = lcp(previous["block"], block)
                step.block_preserved_pct = round(
                    100.0 * step.block_lcp_chars / max(len(previous["block"]), 1), 2)
            suffix_common = lcp(previous["suffix"], suffix_prompt)
            step.suffix_lcp_chars = suffix_common
            step.suffix_preserved_pct = round(
                100.0 * suffix_common / max(len(previous["suffix"]), 1), 2)
            incremental_common = lcp(previous["incremental"], incremental_prompt)
            step.incremental_lcp_chars = incremental_common
            step.incremental_preserved_pct = round(
                100.0 * incremental_common / max(len(previous["incremental"]), 1), 2)
            append_common = lcp(previous["append"], append_prompt)
            step.append_lcp_chars = append_common
            step.append_preserved_pct = round(
                100.0 * append_common / max(len(previous["append"]), 1), 2)
        simulation.steps.append(step)
        prompts.append(prompt)
        suffix_prompts.append(suffix_prompt)
        previous = {"prompt": prompt, "suffix": suffix_prompt,
                    "incremental": incremental_prompt, "append": append_prompt,
                    "block": block}
        previous_signature = signature
        previous_block = block

    # the region every turn shares: the truly reusable prefix
    simulation.stable_prefix_actual = common_prefix_all(prompts)
    simulation.stable_prefix_suffix = common_prefix_all(suffix_prompts)
    return simulation


def _memory_signature(output: Any) -> Tuple[Any, ...]:
    """What the structured memory holds, independent of how the block renders it."""
    memory = getattr(output, "memory", None)
    if memory is None:
        return ()
    return (
        tuple(memory.facts),
        tuple(memory.decisions),
        tuple(memory.relations),
        tuple(getattr(memory, "constraints", ())),
    )


def summarise(simulation: Simulation) -> Dict[str, Any]:
    steps = simulation.steps
    with_prefix = [s for s in steps if s.lcp_chars or s.break_region != "none"]
    return {
        "scenario": simulation.scenario,
        "exchanges": simulation.exchanges,
        "turns": len(steps),
        "turns_with_block": simulation.turns_with_block,
        "block_rebuilds": simulation.block_rebuilds,
        "memory_changes": simulation.memory_changes,
        "stable_prefix_actual_chars": simulation.stable_prefix_actual,
        "stable_prefix_suffix_chars": simulation.stable_prefix_suffix,
        "mean_preserved_pct": round(
            sum(s.preserved_pct for s in with_prefix) / max(len(with_prefix), 1), 2),
        "mean_suffix_preserved_pct": round(
            sum(s.suffix_preserved_pct for s in with_prefix) / max(len(with_prefix), 1), 2),
        "mean_incremental_preserved_pct": round(
            sum(s.incremental_preserved_pct for s in with_prefix) / max(len(with_prefix), 1), 2),
        "mean_append_preserved_pct": round(
            sum(s.append_preserved_pct for s in with_prefix) / max(len(with_prefix), 1), 2),
        "mean_block_preserved_pct": round(
            sum(s.block_preserved_pct for s in with_prefix if s.block_chars) /
            max(sum(1 for s in with_prefix if s.block_chars), 1), 2),
        # the engaging regime: turns where the pipeline actually emitted a block
        "block_turns_mean_preserved_pct": round(
            sum(s.preserved_pct for s in with_prefix if s.block_chars) /
            max(sum(1 for s in with_prefix if s.block_chars), 1), 2),
        "breaks_in_instructions": sum(1 for s in with_prefix if s.break_region == "instructions"),
        "breaks_in_block": sum(1 for s in with_prefix if s.break_region == "block"),
        "breaks_in_messages": sum(1 for s in with_prefix if s.break_region == "messages"),
        "last_prompt_tokens_est": steps[-1].prompt_tokens_est if steps else 0,
        "last_block_chars": steps[-1].block_chars if steps else 0,
        "count_kind": "estimated",
    }


def _stage_detail(output: Any, name: str) -> Dict[str, Any]:
    for stage in getattr(output, "stages", ()) or ():
        if getattr(stage, "name", "") == name:
            return getattr(stage, "detail", {}) or {}
    return {}


# The audit's system prompt is fixed: taken from `conversation.py` so the two
# modules cannot drift apart.
SYSTEM_PROMPTS = [SYSTEM_PROMPT]


def run_matrix(sizes: Sequence[int] = SIZES, scenarios: Sequence[str] = SCENARIOS
               ) -> List[Simulation]:
    return [simulate(size, scenario) for size in sizes for scenario in scenarios]


def format_table(results: Sequence[Simulation]) -> str:
    head = (f"{'scen':5} {'taille':>6} {'tours':>5} {'bloc':>5} {'reconstr':>8} "
            f"{'LCP moy%':>8} {'bloc%':>6} {'LCP(bloc)%':>10} {'suffixe%':>8} "
            f"{'append%':>7} {'prefixe stable':>14} {'coup. instr':>11} {'bloc':>5} "
            f"{'msg':>4} {'tok finaux':>10}")
    lines = [head, "-" * len(head)]
    for simulation in results:
        summary = summarise(simulation)
        lines.append(
            f"{summary['scenario']:5} {summary['exchanges']:>6} {summary['turns']:>5} "
            f"{summary['turns_with_block']:>5} {summary['block_rebuilds']:>8} "
            f"{summary['mean_preserved_pct']:>8} {summary['mean_block_preserved_pct']:>6} "
            f"{summary['block_turns_mean_preserved_pct']:>10} "
            f"{summary['mean_suffix_preserved_pct']:>8} "
            f"{summary['mean_append_preserved_pct']:>7} "
            f"{summary['stable_prefix_actual_chars']:>14} "
            f"{summary['breaks_in_instructions']:>11} {summary['breaks_in_block']:>5} "
            f"{summary['breaks_in_messages']:>4} {summary['last_prompt_tokens_est']:>10}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    sizes = SIZES
    scenarios = SCENARIOS
    print("Prefix stability per turn — characters exact, tokens ESTIMATED (chars/4, no tokenizer)")
    print()
    print(format_table(run_matrix(sizes, scenarios)))
    print()
    print("detail (regime engageant, taille 100, scenarios B/D/F/G/H) :")
    for scenario in ("B", "D", "F", "G", "H"):
        simulation = simulate(100, scenario)
        engaging = [s for s in simulation.steps if s.block_chars][-3:]
        if not engaging:
            print(f"  {scenario}: aucun tour avec bloc (le pipeline n'agit pas)")
        for step in engaging:
            print(f"  {scenario} t{step.turn:>3} total={step.prompt_chars:>6}c "
                  f"bloc={step.block_chars:>5}c LCP={step.lcp_chars:>6}c "
                  f"({step.preserved_pct:>6}%) coupe={step.break_region:<12} "
                  f"bloc_reuse={step.block_preserved_pct:>6}% suffixe={step.suffix_preserved_pct:>6}% "
                  f"append={step.append_preserved_pct:>6}% mem_change={step.memory_changed}")
    if "--json" in argv:
        path = argv[argv.index("--json") + 1]
        payload = {"summaries": [summarise(s) for s in run_matrix(sizes, scenarios)],
                   "steps": [s.to_dict() for sim in run_matrix(sizes, scenarios)
                             for s in sim.steps]}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
