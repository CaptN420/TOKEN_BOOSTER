"""Independent evaluator + metrics for the adversarial memory benchmark.

The evaluator NEVER reuses the extractor under test to build its expectations:
expected names, relations, decisions and sources come from ``scenarios.py``.
Every metric reports its numerator/denominator (and ``None`` when it cannot be
measured) so low results cannot be hidden behind an average.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from boster.memory.consolidation import consolidate_to_episodes
from boster.memory.models import Episode, SemanticMemory

from . import scenarios as S


# ── Metric container ────────────────────────────────────────────────

@dataclass
class Metric:
    name: str
    numerator: int
    denominator: int
    note: str = ""

    @property
    def value(self) -> Optional[float]:
        if self.denominator == 0:
            return None          # explicitly NOT measurable
        return self.numerator / self.denominator

    def render(self) -> str:
        v = "n/a" if self.value is None else f"{self.value:.2f}"
        return f"{self.name:28s} {self.numerator:>3}/{self.denominator:<3} = {v:<5} {self.note}"


# ── Consolidation helpers ───────────────────────────────────────────

def consolidate(gt: S.GroundTruth, *, use_conversation_id: bool = True):
    """Consolidate a scenario (keep_recent=0 -> everything is 'older')."""
    conv = gt.conversation if use_conversation_id else None
    return consolidate_to_episodes(list(gt.messages), keep_recent=0,
                                   conversation_id=conv)


def _norm(text: str) -> str:
    return (text or "").strip().lower()


# ── Metrics ─────────────────────────────────────────────────────────

def fact_recall(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Each source message must survive verbatim as a stored item."""
    contents = {_norm(i.content) for i in sem.items}
    hit = sum(1 for m in gt.messages if _norm(m["content"]) in contents)
    return Metric("fact_recall", hit, len(gt.messages))


def relationship_precision(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Correct = both subject and object are declared valid names.

    The valid-name set is the benchmark's ground truth, not the extractor's
    output, so garbled objects ('Sqlite because') count as incorrect.
    """
    allowed = {_norm(n) for n in gt.allowed_names}
    rels = sem.relations
    if not rels:
        return Metric("relationship_precision", 0, 0, "no relation extracted (not measurable)")
    correct = sum(1 for r in rels if _norm(r.subject) in allowed and _norm(r.object) in allowed)
    return Metric("relationship_precision", correct, len(rels))


def relationship_recall(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Expected relations actually present (normalised subject/predicate/object)."""
    extracted = {(_norm(r.subject), _norm(r.predicate), _norm(r.object)) for r in sem.relations}
    hit = sum(1 for t in gt.expected_relations
              if (_norm(t[0]), _norm(t[1]), _norm(t[2])) in extracted)
    if not gt.expected_relations:
        return Metric("relationship_recall", 0, 0, "no expected relation declared")
    return Metric("relationship_recall", hit, len(gt.expected_relations))


def forbidden_relations_found(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """LOCAL metric: fewer is better. Claims not supported by the sources."""
    if not gt.forbidden_relations:
        return Metric("forbidden_relations_found", 0, 0, "no forbidden relation declared")
    found = 0
    for subj, _pred, obj in gt.forbidden_relations:
        if any((_norm(r.subject) == _norm(subj) and _norm(r.object) == _norm(obj))
               for r in sem.relations):
            found += 1
    return Metric("forbidden_relations_found", found, len(gt.forbidden_relations),
                  "lower is better")


def decision_accuracy(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Fraction of stored decisions whose value matches an expected decision."""
    if not sem.decisions:
        return Metric("decision_accuracy", 0, 0, "no decision stored (not measurable)")
    expected = {_norm(d) for d in gt.expected_decisions}
    correct = sum(1 for d in sem.decisions if _norm(d.value) in expected)
    return Metric("decision_accuracy", correct, len(sem.decisions))


def decision_recall(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    if not gt.expected_decisions:
        return Metric("decision_recall", 0, 0, "no expected decision declared")
    stored = {_norm(d.value) for d in sem.decisions}
    hit = sum(1 for v in gt.expected_decisions if _norm(v) in stored)
    return Metric("decision_recall", hit, len(gt.expected_decisions))


def contradiction_recall(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    if gt.expected_contradictions == 0:
        return Metric("contradiction_recall", 0, 0, "no expected contradiction declared")
    return Metric("contradiction_recall", min(len(sem.contradictions), gt.expected_contradictions),
                  gt.expected_contradictions)


def contradiction_precision(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Over-detection is a real failure mode: it creates contradictions that
    are not in the ground truth."""
    if not sem.contradictions:
        return Metric("contradiction_precision", 0, 0, "none detected (not measurable)")
    want = gt.expected_contradictions
    good = min(len(sem.contradictions), want)
    return Metric("contradiction_precision", good, len(sem.contradictions))


def provenance_accuracy(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Every extracted element must point at a message that really exists."""
    valid = set(gt.message_ids)
    total = 0
    good = 0
    for item in sem.items:
        total += 1
        good += 1 if set(item.source_ids) and set(item.source_ids) <= valid else 0
    for r in sem.relations:
        total += 1
        good += 1 if r.source_id in valid else 0
    for e in sem.entities.values():
        total += 1
        good += 1 if set(e.source_ids) and set(e.source_ids) <= valid else 0
    if total == 0:
        return Metric("provenance_accuracy", 0, 0, "nothing extracted")
    return Metric("provenance_accuracy", good, total)


def historical_integrity(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Expected history entries must still be present (never deleted)."""
    if not gt.expected_history:
        return Metric("historical_integrity", 0, 0, "no history declared")
    # retention is measured at the source-message level: history must never be
    # deleted, independently of how well the extractor labelled it.
    retained = set()
    for item in sem.items:
        retained.update(item.source_ids)
    hit = sum(1 for _value, source in gt.expected_history if source in retained)
    return Metric("historical_integrity", hit, len(gt.expected_history))


def idempotence(gt: S.GroundTruth) -> Metric:
    """Consolidating the same input twice must not duplicate episodes."""
    _, em1, _ = consolidate(gt)
    _, em2, _ = consolidate(gt, use_conversation_id=True)
    # second pass into the same episodic store
    _, em1, _ = consolidate_to_episodes(list(gt.messages), keep_recent=0,
                                        conversation_id=gt.conversation, episodic=em1)
    eps1 = [e for e in em1.episodes if isinstance(e, Episode)]
    eps2 = [e for e in em2.episodes if isinstance(e, Episode)]
    ok = 1 if len(eps1) == len(eps2) else 0
    return Metric("idempotence", ok, 1, f"{len(eps1)} vs {len(eps2)} episodes")


def cross_conversation_leakage(gt_a: S.GroundTruth, gt_b: S.GroundTruth) -> Metric:
    """Information from B must never appear in A's memory (and vice-versa)."""
    _, _, sem_a = consolidate(gt_a)
    _, _, sem_b = consolidate(gt_b)
    ids_a = {i.source_ids[0] for i in sem_a.items if i.source_ids}
    ids_b = {i.source_ids[0] for i in sem_b.items if i.source_ids}
    shared_ids = ids_a & ids_b
    values_a = {_norm(i.content) for i in sem_a.items}
    values_b = {_norm(i.content) for i in sem_b.items}
    shared_values = values_a & values_b
    leaks = len(shared_ids) + len(shared_values)
    return Metric("cross_conversation_leakage", leaks, 1, "lower is better (0 = clean)")


def entity_source_completeness(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """A name mentioned in several messages must keep ALL of them as sources.

    Ground truth = the messages whose text literally mentions the name.
    """
    expected: Dict[str, set] = {}
    for m in gt.messages:
        mid = f"mid_{m['id']}"
        text = _norm(m["content"])
        for name in gt.allowed_names:
            if _norm(name) in text:
                expected.setdefault(_norm(name), set()).add(mid)
    if not expected:
        return Metric("entity_source_completeness", 0, 0, "no entity matched")
    good = 0
    for name, mids in expected.items():
        entity = sem.entities.get(name)
        if entity is not None and set(entity.source_ids) >= mids:
            good += 1
    return Metric("entity_source_completeness", good, len(expected))


def unsupported_claim_rate(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Elements whose source points outside the scenario, or relations with a
    non-grounded object — claims the memory cannot justify."""
    valid = set(gt.message_ids)
    allowed = {_norm(n) for n in gt.allowed_names}
    bad = 0
    total = 0
    for r in sem.relations:
        total += 1
        # unsupported when the source is unknown, or the object is not a declared
        # name (the extractor captured something never asserted as an entity)
        if r.source_id not in valid or _norm(r.object) not in allowed:
            bad += 1
    for item in sem.items:
        total += 1
        if item.source_ids and not (set(item.source_ids) <= valid):
            bad += 1
    for d in sem.decisions:
        total += 1
        if not d.source_ids or not (set(d.source_ids) <= valid):
            bad += 1
    if total == 0:
        return Metric("unsupported_claim_rate", 0, 0, "nothing extracted")
    return Metric("unsupported_claim_rate", bad, total, "lower is better")


# ── Q&A evaluator (data-level only, NO LLM) ─────────────────────────

DIRECTLY_SUPPORTED = "directly_supported"
PARTIALLY_SUPPORTED = "partially_supported"
CONTRADICTORY = "contradictory"
UNKNOWN = "unknown"
OBSOLETE = "obsolete"
AMBIGUOUS = "ambiguous"


@dataclass
class Answer:
    status: str
    evidence: List[str] = field(default_factory=list)


def answer_question(sem: SemanticMemory, question: str) -> Answer:
    """Classify what the *stored data* says about a term in ``question``.

    This inspects retrieved memory only — it does not generate language and
    does not call any model.
    """
    term = _norm(question)
    mentions = [i for i in sem.items if term and term in _norm(i.content)]
    decisions = [d for d in sem.decisions if term and term in _norm(d.value)]
    entities = [e for e in sem.entities.values() if term and _norm(e.name) == term]

    if not mentions and not decisions and not entities:
        return Answer(UNKNOWN, [])

    evidence = [s for i in mentions for s in i.source_ids]
    # two stored statements about the same term -> the memory cannot decide
    if len(mentions) >= 2:
        return Answer(CONTRADICTORY, evidence)
    # a decision on the term exists but a later decision also exists -> stale
    if decisions and len(sem.decisions) > len(decisions):
        return Answer(OBSOLETE, [d.id for d in decisions])
    if mentions and entities:
        return Answer(DIRECTLY_SUPPORTED, evidence)
    if mentions:
        return Answer(PARTIALLY_SUPPORTED, evidence)
    return Answer(AMBIGUOUS, evidence)


def unknown_answer_accuracy(sem: SemanticMemory, gt: S.GroundTruth) -> Metric:
    """Questions about never-mentioned information must be refused."""
    if not gt.unknown_terms:
        return Metric("unknown_answer_accuracy", 0, 0, "no unknown term declared")
    correct = 0
    for term in gt.unknown_terms:
        if answer_question(sem, term).status == UNKNOWN:
            correct += 1
    return Metric("unknown_answer_accuracy", correct, len(gt.unknown_terms))


# ── Report ──────────────────────────────────────────────────────────

def scenario_report() -> str:
    lines: List[str] = []
    for gt in S.ALL_SCENARIOS:
        _, _, sem = consolidate(gt)
        lines.append(f"### {gt.name} ({gt.conversation})")
        lines.append("  " + fact_recall(sem, gt).render())
        lines.append("  " + decision_accuracy(sem, gt).render())
        lines.append("  " + decision_recall(sem, gt).render())
        lines.append("  " + relationship_precision(sem, gt).render())
        lines.append("  " + relationship_recall(sem, gt).render())
        lines.append("  " + forbidden_relations_found(sem, gt).render())
        lines.append("  " + contradiction_recall(sem, gt).render())
        lines.append("  " + contradiction_precision(sem, gt).render())
        lines.append("  " + provenance_accuracy(sem, gt).render())
        lines.append("  " + entity_source_completeness(sem, gt).render())
        lines.append("  " + historical_integrity(sem, gt).render())
        lines.append("  " + unsupported_claim_rate(sem, gt).render())
        lines.append("  " + unknown_answer_accuracy(sem, gt).render())
        lines.append("  " + idempotence(gt).render())
    lines.append("### cross-conversation")
    lines.append("  " + cross_conversation_leakage(S.CONV_A, S.CONV_B).render())
    return "\n".join(lines)


if __name__ == "__main__":       # pragma: no cover
    print(scenario_report())
