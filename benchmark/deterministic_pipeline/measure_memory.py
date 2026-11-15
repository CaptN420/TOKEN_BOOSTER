"""Phase 2.5 — memory measurement harness.

Measures, for each scenario × question × variant:

* the per-stage token ledger (raw → duplicates → selection → compression →
  memory block → final), the absolute/relative saving, and the latency cost;
* memory quality with STRICT, operational definitions — a value is never counted
  as retained merely because its name appears somewhere in the output;
* antipatterns: invented information, cross-project contamination, obsolete
  values presented as current, impossible questions not identified.

Every number comes from executing the pipeline on the frozen scenarios.

Run:  PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.measure_memory
      PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.measure_memory --json out.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from boster.core.budget import estimate_messages_tokens
from boster.memory.consolidation import message_id_for
from boster.deterministic_pipeline import (
    CONTEXT_BLOCK_FOOTER,
    CONTEXT_BLOCK_HEADER,
    PipelineConfig,
    run_deterministic_pipeline,
)
from boster.pipeline import run_pipeline

from .compare_variants import unsupported_claim_check
from .scenarios import SIZES, MemoryFact, Question, Scenario, all_scenarios

__all__ = ["MemoryMeasurement", "measure", "run_matrix", "format_matrix", "reconstruction_table"]

VARIANTS = ("B_classic", "C_no_memory", "D_memory", "E_memory_reconstructed")
SECTION_HEADERS = ("unknown:", "current decisions:", "constraints:", "history:",
                   "relations:", "facts:", "episodes:", "reasons:")

# Which information classes a question actually asks for. A reason is not
# "missing" from a context that was built for a question about the current
# database: demanding it everywhere would score the pipeline against a
# requirement the question never made. Only the applicable kinds are counted.
APPLICABLE_KINDS: Dict[str, Tuple[str, ...]] = {
    "current_value_present": ("current", "history", "reason", "unknown", "multi_episode"),
    "current_vs_obsolete": ("current", "history", "reason", "multi_episode"),
    "constraint_present": ("current", "history", "reason", "unknown", "multi_episode"),
    "reason_present": ("reason",),
    "relation_present": ("current", "multi_episode"),
    "historical_value_available": ("history", "reason", "multi_episode"),
    "decision_in_current_section": ("current", "history", "reason", "unknown"),
    "impossible_identified": ("unknown",),
}


# ── block parsing ──────────────────────────────────────────────────────────

def block_sections(block: str) -> Dict[str, List[str]]:
    """Split a context block into ``{header: [lines]}``."""
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in (block or "").splitlines():
        line = raw.strip()
        if line in SECTION_HEADERS:
            current = line
            sections.setdefault(current, [])
            continue
        if not line or line in (CONTEXT_BLOCK_HEADER, CONTEXT_BLOCK_FOOTER):
            continue
        if current:
            sections[current].append(line)
    return sections


def _history_lines(sections: Dict[str, List[str]]) -> List[str]:
    return sections.get("history:", [])


def current_lines(context: str, sections: Dict[str, List[str]]) -> List[str]:
    """Every line that presents information as CURRENT.

    The history section is excluded, as are lines explicitly marked superseded:
    an obsolete value appearing there does not count as the current answer.
    """
    excluded = set(_history_lines(sections))
    out: List[str] = []
    for raw in context.splitlines():
        line = raw.strip()
        if not line or line in excluded:
            continue
        if "-> replaced" in line or "(superseded)" in line:
            continue
        out.append(line)
    return out


def _co_located(lines: Sequence[str], *values: str) -> bool:
    return any(all(v in line for v in values) for line in lines)


def _mentions(lines: Sequence[str], value: str) -> bool:
    return any(value in line for line in lines)


# ── provenance ─────────────────────────────────────────────────────────────

def provenance_accuracy(block: str, mid_to_content: Dict[str, str]) -> Tuple[int, int]:
    """Is each cited ``[msg_…]`` actually the message that carries the payload?

    A citation that merely exists proves nothing: the payload of the line must
    be found in the message the line cites.
    """
    token_re = re.compile(r"[A-Za-z_][A-Za-z0-9_.:\-]{1,}|[0-9]+(?:\.[0-9]+)?")
    correct = 0
    total = 0
    for raw in (block or "").splitlines():
        line = raw.strip()
        match = re.search(r"\[(msg_[0-9a-f]+)\]", line)
        if not match:
            continue
        cited = mid_to_content.get(match.group(1), "")
        payload = line[: match.start()].strip()
        payload = re.sub(r"^-\s*", "", payload)
        payload = re.sub(r"\s*\((?:active|superseded|rejected|proposed)\)\s*$", "", payload)
        if not payload:
            continue
        total += 1
        cited_tokens = {t.lower() for t in token_re.findall(cited)}
        payload_tokens = [t.lower() for t in token_re.findall(payload)]
        if payload_tokens and all(t in cited_tokens for t in payload_tokens):
            correct += 1
    return correct, total


# ── measurement ────────────────────────────────────────────────────────────

@dataclass
class MemoryMeasurement:
    scenario: str
    exchanges: int
    variant: str
    question_kind: str
    question: str
    # token ledger (all counts: see count_kind)
    tokens_raw: int
    tokens_after_duplicates: int
    tokens_after_selection: int
    tokens_after_compression: int
    tokens_memory_block: int
    tokens_final: int
    saving_absolute: int
    saving_relative_pct: float
    latency_ms: float
    count_kind: str
    # cross-variant quality
    mandatory_ok: bool
    unsupported: int
    unsupported_checked: int
    current_value_present: int
    current_value_total: int
    obsolete_not_current: int
    obsolete_total: int
    current_vs_obsolete: int
    current_vs_obsolete_total: int
    historical_value_available: int
    historical_value_total: int
    constraint_present: int
    constraint_total: int
    reason_present: int
    reason_total: int
    relation_present: int
    relation_total: int
    cross_project_lines: int
    foreign_entity_lines: int
    # memory-specific (D/E)
    decision_in_current_section: int
    decision_total: int
    history_in_history_section: int
    history_total: int
    provenance_correct: int
    provenance_total: int
    impossible_identified: int
    impossible_total: int
    facts_omitted: int
    variant_used: str
    block_sections: Tuple[str, ...] = ()
    block_hash: str = ""
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["block_sections"] = list(self.block_sections)
        data["notes"] = list(self.notes)
        return data


def measure(scenario: Scenario, question: Question, variant: str, *,
            max_tokens: int = 2000, level: str = "balanced") -> MemoryMeasurement:
    messages = [dict(m) for m in scenario.conversation]
    head = [{"role": "system", "content": scenario.system_prompt}]

    start = time.perf_counter()
    if variant == "B_classic":
        result = run_pipeline(messages=messages, system_prompt=scenario.system_prompt,
                              max_total_tokens=max_tokens, compress_output_msgs=True,
                              summarize_history=True, model="")
        system_prompt = result.compressed_system
        produced = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                    for m in result.selected_messages]
        stages = {}
    else:
        out = run_deterministic_pipeline(
            messages, system_prompt=scenario.system_prompt, query=question.text,
            config=PipelineConfig(
                max_tokens=max_tokens, level=level, model="",
                conversation_id=scenario.conversation_id,
                use_memory=variant != "C_no_memory",
                reconstruct=variant == "E_memory_reconstructed",
            ),
        )
        system_prompt = out.system_prompt
        produced = out.messages
        stages = {s.name: s.detail for s in out.stages}
    latency_ms = (time.perf_counter() - start) * 1000.0

    context = system_prompt + "\n" + "\n".join(str(m["content"]) for m in produced)
    block_start = context.find(CONTEXT_BLOCK_HEADER)
    block = context[block_start:] if block_start >= 0 else ""
    sections = block_sections(block)
    live = current_lines(context, sections)
    history = _history_lines(sections)

    sources = scenario.source_texts()
    unsupported, unsupported_checked = unsupported_claim_check(context, sources)

    # Provenance ids come from the memory layer's durable scheme, NOT from the
    # pipeline's turn ids: using the wrong function reported 0/149 correct when
    # the truth was 72/72. ``message_id_for`` is the authoritative function.
    mid_to_content = {message_id_for(m, conversation_id=scenario.conversation_id):
                      str(m.get("content", "")) for m in messages}

    current_facts = [f for f in scenario.facts if f.status == "current"]
    value_present = sum(1 for f in current_facts if _mentions(live, f.value))

    active = [d for d in scenario.decisions if d.status == "active"]
    superseded = [d for d in scenario.decisions if d.status != "active"]

    # The structured "current" view. Variant D emits decisions as "- decision: V"
    # lines without a section header, so both forms are accepted — but the value
    # must be marked active, never merely mentioned.
    decision_lines = list(sections.get("current decisions:", []))
    decision_lines = [line for line in decision_lines
                      if "superseded" not in line and "rejected" not in line]
    decision_lines += [line.strip() for line in block.splitlines()
                       if line.strip().startswith("- decision:")
                       and "superseded" not in line and "rejected" not in line]
    decision_in_section = sum(1 for d in active if _mentions(decision_lines, d.value)
                              and "active" in " ".join(decision_lines))

    # An obsolete value must never read as current. Judged on the structured view
    # when there is one; on a plain chronological transcript the fair test is that
    # the newest mention wins, which is what ``current_vs_obsolete`` measures.
    obsolete_not_current = sum(1 for f in scenario.facts if f.status != "current"
                               and not _mentions(decision_lines, f.value))
    obsolete_total = sum(1 for f in scenario.facts if f.status != "current") if decision_lines else 0

    all_lines = context.splitlines()

    def _last_position(value: str) -> int:
        positions = [index for index, line in enumerate(all_lines) if value in line]
        return max(positions) if positions else -1

    current_vs_obsolete = 0
    for old, new, _index in scenario.contradictions:
        if decision_lines:
            if _mentions(decision_lines, new) and not _mentions(decision_lines, old):
                current_vs_obsolete += 1
        elif _last_position(new) > _last_position(old) >= 0:
            current_vs_obsolete += 1

    constraints_present = sum(1 for text, _i in scenario.constraints if _mentions(live, text))
    reasons_present = sum(1 for text, _i in scenario.reasons if _mentions(live, text))
    relations_present = sum(1 for r in scenario.relations
                            if _co_located(live, r.subject, r.object))

    cross_project = 0
    for line in context.splitlines():
        orion = "Orion" in line
        atlas = "Atlas" in line
        if orion and ("Cassandra" in line):
            cross_project += 1
        if atlas and ("PostgreSQL" in line or "Redis" in line):
            cross_project += 1
    foreign = sum(1 for line in live if _mentions([line], "Atlas") or _mentions([line], "Cassandra"))

    history_ok = sum(1 for d in superseded if _mentions(history, d.value))
    # A superseded decision that was never extracted as a decision still has to be
    # reachable: what matters is that the historical value is present and not
    # presented as current.
    historical_available = sum(
        1 for f in scenario.facts if f.status != "current"
        and _mentions(live, f.value) and not _mentions(decision_lines, f.value)
    )
    historical_total = sum(1 for f in scenario.facts if f.status != "current")
    prov_correct, prov_total = provenance_accuracy(block, mid_to_content)
    impossible_ok = 0
    if question.impossible:
        unknown_lines = sections.get("unknown:", [])
        if question.unknown_subject and _mentions(unknown_lines, question.unknown_subject):
            impossible_ok = 1

    notes: List[str] = []
    has_block = bool(block)
    if variant == "B_classic":
        notes.append("historical pipeline: no memory layer, section-specific metrics N/A")
    if not has_block and variant != "B_classic":
        # No reconstruction block: the conversation fitted the budget, so
        # section-specific metrics do not apply (they would read 0/6 for a
        # context that simply does not use sections). Cross-variant metrics stay.
        notes.append("no context block emitted (conversation fits the budget)")

    raw = estimate_messages_tokens(head + [{"role": m["role"], "content": m["content"]} for m in messages], "")
    final = estimate_messages_tokens(
        ([{"role": "system", "content": system_prompt}] if system_prompt else []) + produced, "")

    return MemoryMeasurement(
        scenario=scenario.name, exchanges=scenario.exchanges, variant=variant,
        question_kind=question.kind, question=question.text,
        tokens_raw=raw,
        tokens_after_duplicates=stages.get("4_duplicate_detector", {}).get(
            "tokens_after_duplicates_estimated", raw),
        tokens_after_selection=stages.get("5_information_selector", {}).get(
            "selected_tokens_estimated", final),
        tokens_after_compression=stages.get("8_context_compressor", {}).get(
            "tokens_after_compression_estimated", final),
        tokens_memory_block=stages.get("7_context_reconstructor", {}).get(
            "context_block_tokens_estimated", 0),
        tokens_final=final,
        saving_absolute=raw - final,
        saving_relative_pct=round(100.0 * (raw - final) / raw, 2) if raw else 0.0,
        latency_ms=round(latency_ms, 3),
        count_kind="estimated",
        mandatory_ok=bool(stages.get("10_integrity_validator", {}).get("ok", True)),
        unsupported=unsupported, unsupported_checked=unsupported_checked,
        current_value_present=value_present, current_value_total=len(current_facts),
        obsolete_not_current=obsolete_not_current, obsolete_total=obsolete_total if has_block else 0,
        current_vs_obsolete=current_vs_obsolete, current_vs_obsolete_total=len(scenario.contradictions),
        historical_value_available=historical_available if has_block else 0,
        historical_value_total=historical_total if has_block else 0,
        constraint_present=constraints_present, constraint_total=len(scenario.constraints),
        reason_present=reasons_present, reason_total=len(scenario.reasons),
        relation_present=relations_present, relation_total=len(scenario.relations),
        cross_project_lines=cross_project, foreign_entity_lines=foreign,
        decision_in_current_section=decision_in_section if has_block else 0,
        decision_total=len(active) if has_block else 0,
        history_in_history_section=history_ok, history_total=len(superseded) if has_block else 0,
        provenance_correct=prov_correct, provenance_total=prov_total,
        impossible_identified=impossible_ok,
        impossible_total=1 if (has_block and question.impossible) else 0,
        facts_omitted=int(stages.get("7_context_reconstructor", {}).get("block", {}).get(
            "omitted", {}).get("facts", 0)),
        variant_used=stages.get("7_context_reconstructor", {}).get("variant", "n/a"),
        block_sections=tuple(sorted(sections)),
        block_hash=hashlib.sha256(block.encode()).hexdigest()[:12] if block else "",
        notes=tuple(notes),
    )


def run_matrix(sizes: Sequence[int] = (10, 25, 50, 100, 200), *, max_tokens: int = 2000
               ) -> List[MemoryMeasurement]:
    """Measure every variant on every question of the given scenarios."""
    results: List[MemoryMeasurement] = []
    for scenario in all_scenarios(sizes):
        for question in scenario.questions:
            for variant in VARIANTS:
                results.append(measure(scenario, question, variant, max_tokens=max_tokens))
    return results


def summarise(results: Sequence[MemoryMeasurement]) -> str:
    """Per-variant totals, counting each class only where the question asks for it."""
    lines: List[str] = []
    for variant in VARIANTS:
        rows = [r for r in results if r.variant == variant]
        raw = sum(r.tokens_raw for r in rows)
        final = sum(r.tokens_final for r in rows)

        def ratio(kept_field: str, total_field: str) -> str:
            kept = total = 0
            for row in rows:
                if row.question_kind not in APPLICABLE_KINDS.get(kept_field, ()):
                    continue
                kept += getattr(row, kept_field)
                total += getattr(row, total_field)
            return f"{kept}/{total}"

        lines.append(
            f"{variant:22} saving {round(100 * (raw - final) / max(raw, 1), 2):>6}% "
            f"| current-value {ratio('current_value_present', 'current_value_total')} "
            f"| current-vs-obsolete {ratio('current_vs_obsolete', 'current_vs_obsolete_total')} "
            f"| historical-available {ratio('historical_value_available', 'historical_value_total')} "
            f"| constraints {ratio('constraint_present', 'constraint_total')} "
            f"| reasons {ratio('reason_present', 'reason_total')} "
            f"| relations {ratio('relation_present', 'relation_total')} "
            f"| in-section {ratio('decision_in_current_section', 'decision_total')} "
            f"| impossible {ratio('impossible_identified', 'impossible_total')} "
            f"| provenance {sum(r.provenance_correct for r in rows)}/{sum(r.provenance_total for r in rows)} "
            f"| mandatory {'ok' if all(r.mandatory_ok for r in rows) else 'VIOLATED'} "
            f"| invented {sum(r.unsupported for r in rows)} "
            f"| cross-project {sum(r.cross_project_lines for r in rows)}")
    return "\n".join(lines)


def format_matrix(results: Sequence[MemoryMeasurement]) -> str:
    head = (f"{'scenario':8} {'kind':14} {'variant':22} {'raw':>6} {'dup':>6} {'sel':>6} {'cmp':>6} "
            f"{'mem':>6} {'final':>6} {'sav%':>6} {'ms':>6} {'cur':>5} {'obs':>5} "
            f"{'cons':>5} {'reas':>5} {'rel':>4} {'prov':>7} {'unk':>4} {'leak':>4} {'inv':>4}")
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(
            f"{r.scenario:8} {r.question_kind:14} {r.variant:22} {r.tokens_raw:>6} {r.tokens_after_duplicates:>6} "
            f"{r.tokens_after_selection:>6} {r.tokens_after_compression:>6} "
            f"{r.tokens_memory_block:>6} {r.tokens_final:>6} {r.saving_relative_pct:>6} "
            f"{r.latency_ms:>6.1f} "
            f"{str(r.current_value_present) + '/' + str(r.current_value_total):>5} "
            f"{str(r.current_vs_obsolete) + '/' + str(r.current_vs_obsolete_total):>5} "
            f"{str(r.constraint_present) + '/' + str(r.constraint_total):>5} "
            f"{str(r.reason_present) + '/' + str(r.reason_total):>5} "
            f"{str(r.relation_present) + '/' + str(r.relation_total):>4} "
            f"{str(r.provenance_correct) + '/' + str(r.provenance_total):>7} "
            f"{str(r.impossible_identified) + '/' + str(r.impossible_total):>4} "
            f"{r.cross_project_lines:>4} {r.unsupported:>4}"
        )
    return "\n".join(lines)


def reconstruction_table(scenario: Scenario, *, level: str = "balanced",
                         max_tokens: int = 2000) -> Tuple[List[Dict[str, Any]], str]:
    """One conversation, every question: are the contexts different and relevant?"""
    rows: List[Dict[str, Any]] = []
    output: List[str] = []
    for question in scenario.questions:
        measurement = measure(scenario, question, "E_memory_reconstructed",
                              max_tokens=max_tokens, level=level)
        out = run_deterministic_pipeline(
            [dict(m) for m in scenario.conversation], system_prompt=scenario.system_prompt,
            query=question.text, config=PipelineConfig(
                max_tokens=max_tokens, level=level, model="",
                conversation_id=scenario.conversation_id, use_memory=True, reconstruct=True),
        )
        block = out.system_prompt[out.system_prompt.find(CONTEXT_BLOCK_HEADER):]
        missing = [v for v in question.expect_in_context if v not in block]
        present = [v for v in question.expect_in_context if v in block]
        leaked = [v for v in question.expect_absent if v in block]
        rows.append({
            "question": question.text, "kind": question.kind,
            "block_hash": measurement.block_hash,
            "sections": list(measurement.block_sections),
            "required_present": present, "required_missing": missing,
            "leaked": leaked,
            "history_shown": "history:" in measurement.block_sections,
            "block_tokens": measurement.tokens_memory_block,
            "final_tokens": measurement.tokens_final,
        })
        output.append(
            f"  {question.kind:14} block={measurement.block_hash} "
            f"tok={measurement.tokens_memory_block:>5} sections={','.join(measurement.block_sections)}\n"
            f"      Q: {question.text}\n"
            f"      present={present} missing={missing} leaked={leaked}"
        )
    hashes = {row["block_hash"] for row in rows}
    output.append(f"  distinct contexts for {len(hashes)}/{len(rows)} questions")
    return rows, "\n".join(output)


def main(argv: Sequence[str]) -> int:
    results = run_matrix()
    print(format_matrix(results))
    print()
    print("token counts: estimated (chars/4 — no tokenizer installed)")
    print()
    print(summarise(results))
    print()
    print("per-question-kind detail (E and C, saving and answerability):")
    for variant in ("C_no_memory", "E_memory_reconstructed"):
        for kind in ("current", "history", "reason", "unknown", "multi_episode"):
            rows = [r for r in results if r.variant == variant and r.question_kind == kind]
            if not rows:
                continue
            raw = sum(r.tokens_raw for r in rows)
            final = sum(r.tokens_final for r in rows)
            print(f"  {variant:22} {kind:14} saving {round(100 * (raw - final) / max(raw, 1), 2):>6}% "
                  f"| current-value {sum(r.current_value_present for r in rows)}/"
                  f"{sum(r.current_value_total for r in rows)} "
                  f"| current-vs-obsolete {sum(r.current_vs_obsolete for r in rows)}/"
                  f"{sum(r.current_vs_obsolete_total for r in rows)} "
                  f"| history {sum(r.historical_value_available for r in rows)}/"
                  f"{sum(r.historical_value_total for r in rows)} "
                  f"| reasons {sum(r.reason_present for r in rows)}/"
                  f"{sum(r.reason_total for r in rows)} "
                  f"| relations {sum(r.relation_present for r in rows)}/"
                  f"{sum(r.relation_total for r in rows)} "
                  f"| impossible {sum(r.impossible_identified for r in rows)}/"
                  f"{sum(r.impossible_total for r in rows)}")
    print()
    print("reconstruction over one conversation (mem_200), every question:")
    rows, table = reconstruction_table(all_scenarios((200,))[0])
    print(table)
    if "--json" in argv:
        path = argv[argv.index("--json") + 1]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"matrix": [r.to_dict() for r in results],
                       "reconstruction": rows}, handle, indent=2, sort_keys=True)
        print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
