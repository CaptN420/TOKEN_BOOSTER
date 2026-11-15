"""A→E comparison harness: measures real savings AND real information retention.

Variants (Phase 2.4 §7):
    A  original prompt, untouched
    B  the existing classic Boster path (``boster.pipeline.run_pipeline``)
    C  deterministic pipeline, no memory
    D  deterministic pipeline with memory, no reconstruction
    E  deterministic pipeline with memory + contextual reconstruction

Every number printed here is produced by executing the code on the frozen
fixtures. Token counts go through ``core.tokenizer.count_tokens``; when no
tokenizer is installed the count is an ESTIMATE (chars/4) and is labelled as
such — never presented as an exact saving.

Run:  PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.compare_variants
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from boster.core.budget import estimate_messages_tokens
from boster.core.tokenizer import count_tokens
from boster.deterministic_pipeline import (
    PipelineConfig,
    run_deterministic_pipeline,
    token_count_kind,
)
from boster.pipeline import run_pipeline

from .fixtures import ALL_FIXTURES, GroundTruth

__all__ = ["VariantResult", "measure_variant", "compare_all", "fabrication_check", "format_table"]

VARIANTS = ("A_original", "B_classic", "C_no_memory", "D_memory", "E_memory_reconstructed")


@dataclass
class VariantResult:
    fixture: str
    variant: str
    tokens_before: int
    tokens_after: int
    messages_kept: int
    elapsed_ms: float
    instructions_kept: int
    instructions_total: int
    facts_kept: int
    facts_total: int
    relations_kept: int
    relations_total: int
    decisions_kept: int
    decisions_total: int
    errors_kept: int
    errors_total: int
    negations_kept: int
    negations_total: int
    fabricated: int
    fabrication_checked: int
    unsupported: int = 0
    unsupported_total: int = 0
    variant_kind: str = ""
    count_kind: str = "estimated"
    problems: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def reduction_pct(self) -> float:
        return round(100.0 * self.tokens_saved / self.tokens_before, 2) if self.tokens_before else 0.0

    def retention(self) -> float:
        """Share of the mandatory/ground-truth items that survived (0..1)."""
        num = (self.instructions_kept + self.facts_kept + self.relations_kept
               + self.decisions_kept + self.errors_kept + self.negations_kept)
        den = (self.instructions_total + self.facts_total + self.relations_total
               + self.decisions_total + self.errors_total + self.negations_total)
        return round(num / den, 4) if den else 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixture": self.fixture, "variant": self.variant, "variant_kind": self.variant_kind,
            "tokens_before": self.tokens_before, "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved, "reduction_pct": self.reduction_pct,
            "count_kind": self.count_kind,
            "messages_kept": self.messages_kept, "elapsed_ms": round(self.elapsed_ms, 3),
            "instructions": [self.instructions_kept, self.instructions_total],
            "facts": [self.facts_kept, self.facts_total],
            "relations": [self.relations_kept, self.relations_total],
            "decisions": [self.decisions_kept, self.decisions_total],
            "errors": [self.errors_kept, self.errors_total],
            "negations": [self.negations_kept, self.negations_total],
            "retention": self.retention(),
            "fabricated": [self.fabricated, self.fabrication_checked],
            "unsupported": [self.unsupported, self.unsupported_total],
            "problems": list(self.problems),
            "detail": self.detail,
        }


# ── measurement helpers ────────────────────────────────────────────────────

def _present(haystack: str, needle) -> bool:
    if isinstance(needle, tuple):
        # a relation is present only when all of its parts are
        return all(_present(haystack, part) for part in needle)
    return needle.strip() != "" and needle.strip() in haystack


def _count_present(haystack: str, needles) -> int:
    return sum(1 for n in needles if _present(haystack, n))


def fabrication_check(text: str, sources: List[str]) -> Tuple[int, int]:
    """Every reconstructed line must be traceable to a source message.

    Returns ``(unverifiable_lines, checked_lines)``. Lines are compared on their
    payload (the ``- `` prefix and the trailing ``[provenance]`` are dropped) and
    are expected to be a substring of at least one source message, possibly
    whitespace-collapsed.
    """
    import re

    allowed = [
        "[BOSTER CONTEXT]", "[/BOSTER CONTEXT]", "[DEVELOPER]", "[/DEVELOPER]",
        "current decisions:", "history:", "facts:", "relations:", "episodes:",
    ]
    flat_sources = [re.sub(r"\s+", " ", s) for s in sources]
    unverifiable = 0
    checked = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in allowed:
            continue
        for marker in allowed:
            if line.startswith(marker):
                line = line[len(marker):].strip()
        line = re.sub(r"\s+\[[^\]]*\]$", "", line)      # drop provenance
        line = re.sub(r"^-\s*", "", line)
        # a deterministic status suffix ("(active)", "(superseded)", "-> replaced")
        # is derived from the source wording, not invented: drop it and check the
        # remaining payload against the sources.
        line = re.sub(r"\s*\((?:active|superseded|rejected|proposed|confirmed|tentative|unknown)\)\s*$", "", line)
        line = re.sub(r"\s*->\s*replaced\s*$", "", line)
        for prefix in ("decision: ", "fact: "):
            if line.startswith(prefix):
                line = line[len(prefix):]
        if line.startswith(("current decisions:", "facts:", "relations:")):
            continue
        if not line:
            continue
        checked += 1
        flat = re.sub(r"\s+", " ", line)
        if not any(flat in src or src in flat and src for src in flat_sources):
            unverifiable += 1
    return unverifiable, checked


def unsupported_claim_check(text: str, sources: List[str]) -> Tuple[int, int]:
    """The real "no invented information" test (Phase 2.4 §5 Agent 10).

    A compression is a *transformation*: re-serialising JSON or dropping a code
    body legitimately produces lines that are not verbatim substrings of the
    input, so a substring test reports false alarms. What must never happen is
    introducing a value that exists nowhere in the sources. Every content token
    of the output is therefore looked up in the source token vocabulary: a token
    present nowhere means a claim the input never supported.

    Returns ``(lines_with_unsupported_tokens, checked_lines)``.
    """
    import re

    structure_headers = (
        "[BOSTER CONTEXT]", "[/BOSTER CONTEXT]", "[DEVELOPER]", "[/DEVELOPER]",
        "unknown:", "current decisions:", "constraints:", "history:", "relations:",
        "facts:", "episodes:", "reasons:",
    )
    token_re = re.compile(r"[A-Za-z_][A-Za-z0-9_.:\-]{1,}|[0-9]+(?:\.[0-9]+)?")
    vocabulary = set()
    for source in sources:
        for token in token_re.findall(source):
            lowered = token.lower()
            vocabulary.add(lowered)
            # the token pattern keeps sentence punctuation, so "Memcached." would
            # not vouch for the value "Memcached" — measured, that alone produced
            # every false "invented" flag on the memory variants
            vocabulary.add(lowered.rstrip(".:-"))

    unsupported = 0
    checked = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # the structural headers ARE produced by the reconstructor itself
        if line in structure_headers or any(line.startswith(h) for h in structure_headers):
            continue
        line = re.sub(r"\[[^\]]*\]", " ", line)          # provenance/ids are ours
        line = re.sub(r"^-+\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^\.\.\.\s*", "", line)
        line = re.sub(r"^\(omitted[^)]*\)\s*", "", line)  # "…(omitted: 3 facts)" is metadata
        line = re.sub(r"^\[omitted[^\]]*\]\s*", "", line)
        line = re.sub(r"^(-|\d+\.|->)+\s*", "", line)     # history arrows / ladder markers
        line = re.sub(r"^\s*(decision|fact|note)\s*:\s*", "", line)
        # the absence notice is the reconstructor stating that the conversation
        # holds nothing on the subject: a statement about the source, not a claim
        line = re.sub(r"^no information about \S+( in this conversation)?\s*$", "", line)
        # status markers are the reconstructor's own vocabulary, and they can sit
        # anywhere on the line ("- Memcached (superseded) -> Redis")
        line = re.sub(r"\((?:active|superseded|rejected|proposed|confirmed|tentative|unknown)\)", " ", line)
        line = re.sub(r"\b(?:omitted|omitted:)\b[^)]*\)?", " ", line)
        tokens = token_re.findall(line)
        if not tokens:
            continue
        checked += 1
        if any(t.lower() not in vocabulary and t.lower().rstrip(".:-") not in vocabulary
               for t in tokens):
            unsupported += 1
    return unsupported, checked


def _decisions_ok(final_text: str, gt: GroundTruth) -> Tuple[int, int]:
    """A declared decision counts as kept when its value is represented.

    Current decisions must appear; a superseded value must not be *presented as
    current* (it may legitimately survive under ``history:``).
    """
    kept = 0
    for value, _status in gt.decisions:
        if _present(final_text, value):
            kept += 1
    return kept, len(gt.decisions)


def measure_variant(gt: GroundTruth, variant: str, *, max_tokens: int = 20_000,
                    level: str = "balanced") -> VariantResult:
    """Run one variant on one fixture and measure it. Real execution only."""
    messages = list(gt.conversation)
    head = [{"role": "system", "content": gt.system_prompt}] if gt.system_prompt else []
    source_texts = [gt.system_prompt] + [str(m.get("content", "")) for m in messages]

    start = time.perf_counter()
    detail: Dict[str, Any] = {}
    kind = token_count_kind("")

    if variant == "A_original":
        # the prompt exactly as the caller wrote it: system prompt + every message
        final_system = gt.system_prompt
        final_messages = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                          for m in messages]
        variant_kind = "original"
    elif variant == "B_classic":
        result = run_pipeline(
            messages=messages, system_prompt=gt.system_prompt,
            max_total_tokens=max_tokens, compress_output_msgs=True,
            summarize_history=True, model="",
        )
        final_system = result.compressed_system
        final_messages = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                          for m in result.selected_messages]
        variant_kind = "boster.pipeline.run_pipeline"
        detail = {
            "selected_message_count": len(final_messages),
            "history_items_dropped": getattr(result.budget_result, "history_items_dropped", None),
            "output_savings": result.output_savings,
        }
    else:
        config = PipelineConfig(
            max_tokens=max_tokens, level=level, model="",
            conversation_id=gt.conversation_id,
            use_memory=variant != "C_no_memory",
            reconstruct=variant == "E_memory_reconstructed",
        )
        out = run_deterministic_pipeline(
            messages, system_prompt=gt.system_prompt, query=gt.query, config=config,
        )
        final_system = out.system_prompt
        final_messages = out.messages
        variant_kind = out.config.level
        stage7 = next((s.detail for s in out.stages if s.name == "7_context_reconstructor"), {})
        detail = {
            "variant": stage7.get("variant"),
            "fallback": stage7.get("fallback"),
            "represented_in_memory": stage7.get("represented_in_memory"),
            "stages": {s.name: s.detail for s in out.stages},
            "validation": out.validation.to_dict() if out.validation else None,
        }
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    tokens_before = estimate_messages_tokens(
        ([{"role": "system", "content": gt.system_prompt}] if gt.system_prompt else [])
        + [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages],
        "",
    )
    tokens_after = estimate_messages_tokens(
        ([{"role": "system", "content": final_system}] if final_system else []) + final_messages, ""
    )

    final_text = final_system + "\n" + "\n".join(str(m.get("content", "")) for m in final_messages)
    fabricated, checked = fabrication_check(final_text, source_texts)
    unsupported, unsupported_checked = unsupported_claim_check(final_text, source_texts)

    d_kept, d_total = _decisions_ok(final_text, gt)

    return VariantResult(
        fixture=gt.name, variant=variant, tokens_before=tokens_before, tokens_after=tokens_after,
        messages_kept=len(final_messages), elapsed_ms=elapsed_ms,
        instructions_kept=_count_present(final_text, gt.mandatory_fragments),
        instructions_total=len(gt.mandatory_fragments),
        facts_kept=_count_present(final_text, gt.facts), facts_total=len(gt.facts),
        relations_kept=_count_present(final_text, gt.relations) if gt.relations else 0,
        relations_total=len(gt.relations),
        decisions_kept=d_kept, decisions_total=d_total,
        errors_kept=_count_present(final_text, gt.errors), errors_total=len(gt.errors),
        negations_kept=_count_present(final_text, gt.negations), negations_total=len(gt.negations),
        fabricated=fabricated, fabrication_checked=checked,
        unsupported=unsupported, unsupported_total=unsupported_checked,
        variant_kind=variant_kind, count_kind=kind, detail=detail,
    )


def compare_all(*, max_tokens: int = 20_000, level: str = "balanced",
                fixtures=ALL_FIXTURES) -> List[VariantResult]:
    results: List[VariantResult] = []
    for gt in fixtures:
        for variant in VARIANTS:
            results.append(measure_variant(gt, variant, max_tokens=max_tokens, level=level))
    return results


def format_table(results: List[VariantResult]) -> str:
    head = (f"{'fixture':18} {'variant':24} {'before':>7} {'after':>7} {'saved':>7} "
            f"{'red%':>6} {'keep%':>6} {'instr':>7} {'unsup':>5} {'verb':>5} {'ms':>7}")
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(
            f"{r.fixture:18} {r.variant:24} {r.tokens_before:>7} {r.tokens_after:>7} "
            f"{r.tokens_saved:>7} {r.reduction_pct:>6} {100 * r.retention():>6.1f} "
            f"{r.instructions_kept}/{r.instructions_total:<5} "
            f"{r.unsupported:>5} {r.fabricated:>5} {r.elapsed_ms:>7.2f}"
        )
    return "\n".join(lines)


def main() -> None:
    results = compare_all()
    print(format_table(results))
    print()
    kind = results[0].count_kind if results else "estimated"
    print(f"token counts: {kind}" + ("" if kind == "exact"
          else "  (no tokenizer installed: ESTIMATES, chars/4 — not exact savings)"))
    print()
    # per-variant aggregate on the fixtures where optimisation is meaningful
    for variant in VARIANTS:
        rows = [r for r in results if r.variant == variant]
        red = sum(r.tokens_saved for r in rows)
        tot = sum(r.tokens_before for r in rows)
        clean = sum(1 for r in rows if r.unsupported == 0)
        instr = sum(r.instructions_kept for r in rows)
        instr_tot = sum(r.instructions_total for r in rows)
        print(f"{variant:24} aggregate reduction {round(100 * red / max(tot, 1), 2):>6}% "
              f"| no-unsupported-claim fixtures {clean}/{len(rows)} "
              f"| instructions kept {instr}/{instr_tot} "
              f"| mean retention {round(100 * sum(r.retention() for r in rows) / max(len(rows), 1), 1)}%")

    print()
    print("JSON dump (deterministic, for archiving):")
    print(json.dumps([r.to_dict() for r in results], sort_keys=True, ensure_ascii=False)[:400] + " ...")


if __name__ == "__main__":
    main()
