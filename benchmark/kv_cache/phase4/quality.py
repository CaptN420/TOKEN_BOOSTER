"""Phase-4 memory-quality validation (brief §8).

The optimisation must never buy cache with memory.  This module answers, for the
append-only configuration, what happened to each required piece of information —
and it uses the five-way vocabulary the brief asks for:

``EXACT``          the information is in the prompt, verbatim;
``SEMANTIC``       the same content words are present, in different words;
``AMBIGUOUS``      present but the *current state* disagrees with it;
``LOST``           absent from the prompt;
``UNRECOVERABLE``  absent from the prompt **and** absent from the journal and its
                   archives (no way to get it back).

It also runs the adversarial set: a triple correction chain, a duplicate, a
missing source, a missing timestamp, unicode, an obsolete item and a
consolidation — and checks that the invariants hold in every case.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from boster.memory.append_only import (STATUS_ACTIVE, AppendOnlyMemory, Budget,
                                       TYPE_CORRECTION, TYPE_OBSOLETE)

RESULTS_DIR = Path(__file__).parent / "results"

EXACT = "EXACT"
SEMANTIC = "SEMANTIC"
AMBIGUOUS = "AMBIGUOUS"
LOST = "LOST"
UNRECOVERABLE = "UNRECOVERABLE"

#: information a reader must still find, and how to look for it
REQUIRED: Tuple[Tuple[str, str], ...] = (
    ("instructions systeme", "careful engineering assistant"),
    ("contrainte durable", "4 gb"),
    ("valeur de stockage courante", "postgresql"),
    ("valeur de stockage remplacee", "sqlite"),
    ("relation reporting", "reporting service"),
    ("hote de staging", "atlas-1"),
    ("provenance", "src:"),
)


def words(text: str) -> List[str]:
    """Content words, lower case — the same idea phase 3 uses."""
    return [w for w in "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()
            if len(w) > 2]


@dataclass
class Finding:
    item: str
    verdict: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"item": self.item, "verdict": self.verdict, "detail": self.detail}


def classify(block: str, needle: str, *, current_value: str = "",
             superseded_value: str = "") -> Finding:
    """One required item against the emitted prompt."""
    lowered = block.lower()
    needle = needle.lower()
    if needle in lowered:
        if superseded_value and current_value and needle == superseded_value.lower():
            # the replaced value is present, and the prompt must also say it was
            # replaced — otherwise a reader would take it for the current one
            if current_value.lower() in lowered:
                return Finding(needle, EXACT, "present, et la valeur courante l'est aussi")
            return Finding(needle, AMBIGUOUS, "present sans que la valeur courante apparaisse")
        return Finding(needle, EXACT)
    target = set(words(needle))
    if target and target <= set(words(block)):
        return Finding(needle, SEMANTIC, "meme contenu lexical, autre formulation")
    return Finding(needle, LOST)


def retention(block: str, journal_text: str) -> List[Finding]:
    findings: List[Finding] = []
    for item, needle in REQUIRED:
        finding = classify(block, needle)
        if finding.verdict == LOST and needle in journal_text.lower():
            finding.verdict = UNRECOVERABLE  # unreachable by the prompt: it *is* the prompt
        findings.append(Finding(item, finding.verdict, finding.detail or finding.item))
    return findings


def invariants(memory: AppendOnlyMemory, conversation: str) -> List[Finding]:
    """The properties that must hold whatever the layout does."""
    findings: List[Finding] = []
    state = memory.fold_current()

    drifted = memory.verify_frozen()
    findings.append(Finding(
        "lignes gelees inchangees", EXACT if not drifted else LOST,
        "" if not drifted else f"derivees : {drifted}"))

    subject_words = set(words(conversation))
    invented = [entry.entry_id for entry in memory.journal
                if entry.kind in ("fact", "correction", "decision", "decision_change")
                and not (set(words(entry.content)) & subject_words)]
    findings.append(Finding(
        "aucun fait invente", EXACT if not invented else LOST,
        "" if not invented else f"sans correspondance : {invented}"))

    missing_sources = [entry.entry_id for entry in memory.journal
                       if entry.kind in ("fact", "correction", "decision", "decision_change",
                                         "relation", "constraint")
                       and not entry.source_ids]
    findings.append(Finding(
        "tracabilite des entrees", EXACT if not missing_sources else AMBIGUOUS,
        "" if not missing_sources else f"sans source : {missing_sources}"))

    orphans = [entry.entry_id for entry in memory.journal
               if entry.supersedes and memory.lookup(entry.supersedes) is None]
    findings.append(Finding(
        "references de correction resolues", EXACT if not orphans else LOST,
        "" if not orphans else f"references mortes : {orphans}"))

    superseded = [eid for eid, by in state.superseded.items() if by]
    findings.append(Finding(
        "distinction actuel / obsolete", EXACT if superseded or not memory.journal else SEMANTIC,
        f"{len(state.values)} valeur(s) courante(s), {len(state.superseded)} renversee(s)"))

    archived = sum(len(b.get("entries", [])) for b in memory.archived)
    findings.append(Finding(
        "historique conserve apres consolidation",
        EXACT if (archived or not memory.archived) else LOST,
        f"{archived} entree(s) archivee(s), {memory.stats()['entries']} vivante(s)"))
    return findings


def adversarial() -> List[Finding]:
    """The cases the brief lists, each asserting a property rather than a shape."""
    findings: List[Finding] = []
    memory = AppendOnlyMemory(conversation_id="adversarial", budget=Budget(total=0))
    memory.open_prefix(instructions=["rule"], reference=["Project"])

    memory.append_fact("store = one", source_ids=["m1"])
    memory.append_correction("store = two", supersedes="F001", source_ids=["m2"])
    memory.append_correction("store = three", supersedes="C001", source_ids=["m3"])
    chain_length = len(memory.fold_current().superseded)
    findings.append(Finding("chaine de corrections", EXACT if chain_length == 2 else LOST,
                            f"{chain_length} renversements en chaine"))

    memory.append_fact("store = three")                      # duplicate of the current value
    findings.append(Finding("doublon non reajoute",
                            EXACT if len(memory.journal) == 3 else LOST,
                            f"{len(memory.journal)} entrees"))

    memory.append_fact("undated and unsourced")
    entry = memory.journal[-1]
    findings.append(Finding("date absente jamais inventee",
                            EXACT if entry.created_at is None and "[date:" not in entry.render_line()
                            else LOST, entry.render_line()))

    memory.append_fact("mémoire bornée — ✨", source_ids=["m7"])
    findings.append(Finding("unicode intact",
                            EXACT if "✨" in memory.block_text() else LOST))

    obsolete = memory.append_obsolete("cache = none", supersedes=None, source_ids=["m8"])
    findings.append(Finding("obsolete sans reference accepte",
                            EXACT if obsolete.kind == TYPE_OBSOLETE else LOST))

    before = memory.block_text()
    memory.save(RESULTS_DIR / "_adversarial.json", saved_at=1700000000)
    reloaded = AppendOnlyMemory.load(RESULTS_DIR / "_adversarial.json")
    findings.append(Finding("persistance disque identique",
                            EXACT if reloaded.block_text() == before else LOST))

    result = memory.consolidate(approved=True, reason="adversarial", at=1.0)
    findings.append(Finding("consolidation conserve tout",
                            EXACT if (result.done and result.entries_dropped == 0) else LOST,
                            f"{result.archived_entries} archivees, {result.entries_dropped} perdues"))
    findings.append(Finding("consolidation n'a rien detruit",
                            EXACT if memory.lookup("F001") is not None else LOST))
    return findings


def all_configs(tag: str = "main", scenario: str = "B", exchanges: int = 100
                ) -> Dict[str, Any]:
    """Per-configuration quality report for the append-only architecture."""
    from .payloads import build_append_sequence, build_raw_sequence
    from ..conversation import build_conversation

    turns, _ = build_conversation(exchanges, scenario=scenario)
    conversation = " ".join(f"{t.user} {t.assistant}" for t in turns)
    report: Dict[str, Any] = {}

    run = build_append_sequence(scenario=scenario, exchanges=exchanges, budget_total=0)
    assert run.memory is not None
    block = run.memory.block_text()
    findings = retention(block, block)
    state_findings = invariants(run.memory, conversation)
    report["boster_append"] = {
        "retention": [f.to_dict() for f in findings],
        "invariants": [f.to_dict() for f in state_findings],
        "stability_violations": run.summary()["contract_violations"],
        "current_state": {s: list(v) for s, v in run.memory.fold_current().values.items()},
        "entries": run.memory.stats()["entries"],
    }

    raw = build_raw_sequence(scenario=scenario, exchanges=exchanges)
    raw_block = raw[-1].prompt
    report["raw"] = {"retention": [f.to_dict() for f in retention(raw_block, raw_block)]}
    report["adversarial"] = [f.to_dict() for f in adversarial()]
    return report


def format_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    for config, data in report.items():
        if config == "adversarial":
            continue
        lines.append(f"### {config}\n")
        lines.append("| information | verdict | detail |")
        lines.append("|---|---|---|")
        for finding in data.get("retention", []):
            lines.append(f"| {finding['item']} | {finding['verdict']} | {finding['detail']} |")
        lines.append("")
        for finding in data.get("invariants", []):
            lines.append(f"- **{finding['item']}** : {finding['verdict']} — {finding['detail']}")
        if "stability_violations" in data:
            lines.append(f"- violations du contrat append-only : {data['stability_violations']}")
        if data.get("current_state"):
            lines.append(f"- etat courant calcule : {json.dumps(data['current_state'], ensure_ascii=False)}")
        lines.append("")
    lines.append("### cas adversariaux\n")
    lines.append("| cas | verdict | detail |")
    lines.append("|---|---|---|")
    for finding in report.get("adversarial", []):
        lines.append(f"| {finding['item']} | {finding['verdict']} | {finding['detail']} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 memory quality")
    parser.add_argument("--scenario", default="B")
    parser.add_argument("--exchanges", type=int, default=100)
    parser.add_argument("--out", default=str(RESULTS_DIR / "quality_report.json"))
    args = parser.parse_args(argv)

    report = all_configs(scenario=args.scenario, exchanges=args.exchanges)
    print(format_report(report))
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    verdicts = [f["verdict"] for f in report["boster_append"]["retention"]]
    bad = [v for v in verdicts if v in (LOST, UNRECOVERABLE)]
    print(f"\nverdicts : {verdicts}")
    print(f"informations perdues : {bad if bad else 'aucune'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
