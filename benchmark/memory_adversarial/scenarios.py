"""Deterministic adversarial scenarios with an INDEPENDENT ground truth.

The ground truth (valid names, expected relations, expected decisions, the
exact source of each element) is declared here by hand. It is never derived
from Boster's extractors, so the evaluator is not circular.

Messages carry explicit native ids so their durable ids (``mid_<id>``) are
known by construction and can be used as independent expected sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


def msg(mid: str, content: str, role: str = "user") -> Dict[str, str]:
    """A conversation message with an explicit native id."""
    return {"id": mid, "role": role, "content": content}


@dataclass
class GroundTruth:
    """What a faithful memory MUST and MUST NOT contain."""
    name: str
    messages: Sequence[Dict[str, str]]
    conversation: str
    # declared valid entity names (independent of the extractor)
    allowed_names: Sequence[str] = field(default_factory=list)
    # relation triples that are genuinely present in the sources
    expected_relations: Sequence[Tuple[str, str, str]] = field(default_factory=list)
    # relation triples that are NOT supported by the sources
    forbidden_relations: Sequence[Tuple[str, str, str]] = field(default_factory=list)
    # decision values that were explicitly decided
    expected_decisions: Sequence[str] = field(default_factory=list)
    # names that were never mentioned at all
    unknown_terms: Sequence[str] = field(default_factory=list)
    expected_contradictions: int = 0
    # (value, source message id) pairs that must survive as history
    expected_history: Sequence[Tuple[str, str]] = field(default_factory=list)

    @property
    def message_ids(self) -> List[str]:
        return [f"mid_{m['id']}" for m in self.messages]


# ── Scenario 1: confusion between two conversations ─────────────────
CONV_A = GroundTruth(
    name="cross_conversation_A",
    conversation="conversation-A",
    messages=[
        msg("a1", "Project Orion uses SQLite."),
        msg("a2", "Alex is responsible for Project Orion."),
    ],
    allowed_names=["Orion", "Project Orion", "SQLite", "Alex"],
    expected_decisions=[],
)

CONV_B = GroundTruth(
    name="cross_conversation_B",
    conversation="conversation-B",
    messages=[
        msg("b1", "Project Orion uses PostgreSQL."),
        msg("b2", "Sam is responsible for Project Orion."),
    ],
    allowed_names=["Orion", "Project Orion", "PostgreSQL", "Sam"],
    expected_decisions=[],
)

# ── Scenario 2: contradictory information ───────────────────────────
CONTRADICTION = GroundTruth(
    name="contradiction",
    conversation="conversation-C",
    messages=[
        msg("c1", "Project Orion uses SQLite."),
        msg("c2", "Project Orion uses PostgreSQL."),
        msg("c3", "The Project Orion decision is replaced by PostgreSQL."),
    ],
    allowed_names=["Orion", "SQLite", "PostgreSQL"],
    expected_contradictions=1,
    expected_history=[("SQLite", "mid_c1"), ("PostgreSQL", "mid_c2")],
)

# ── Scenario 3: hypotheses vs facts ─────────────────────────────────
HYPOTHESES = GroundTruth(
    name="hypotheses_vs_facts",
    conversation="conversation-D",
    messages=[
        msg("h1", "We could use Redis."),
        msg("h2", "I think PostgreSQL would be preferable."),
        msg("h3", "Maybe Alex will handle the backend."),
        msg("h4", "We decided to use SQLite."),
    ],
    allowed_names=["Redis", "PostgreSQL", "Alex", "SQLite"],
    expected_decisions=["SQLite"],  # ONLY the explicit decision
)

# ── Scenario 4: invented relations ──────────────────────────────────
RELATIONS = GroundTruth(
    name="relations",
    conversation="conversation-E",
    messages=[
        msg("r1", "Alex works on Orion."),
        msg("r2", "SQLite is used by Orion."),
        msg("r3", "Sam works on Atlas."),
    ],
    allowed_names=["Alex", "Orion", "SQLite", "Sam", "Atlas"],
    # canonical direction: "SQLite is used by Orion" == "Orion -> uses -> SQLite"
    expected_relations=[
        ("Alex", "works-on", "Orion"),
        ("Orion", "uses", "SQLite"),
        ("Sam", "works-on", "Atlas"),
    ],
    # none of these are stated anywhere
    forbidden_relations=[
        ("Sam", "works-on", "Orion"),
        ("SQLite", "used-by", "Atlas"),
        ("Alex", "uses", "SQLite"),
    ],
)

# ── Scenario 5: revised decisions ───────────────────────────────────
REVISIONS = GroundTruth(
    name="revised_decisions",
    conversation="conversation-F",
    messages=[
        msg("d1", "We decided to use SQLite."),
        msg("d2", "We could use PostgreSQL."),
        msg("d3", "We decided to use PostgreSQL."),
    ],
    allowed_names=["SQLite", "PostgreSQL"],
    expected_decisions=["SQLite", "PostgreSQL"],
    expected_history=[("SQLite", "mid_d1"), ("PostgreSQL", "mid_d3")],
)

# ── Scenario 6: unknown information ─────────────────────────────────
UNKNOWN = GroundTruth(
    name="unknown_information",
    conversation="conversation-G",
    messages=[
        msg("k1", "Project Orion uses SQLite."),
    ],
    allowed_names=["Orion", "SQLite"],
    unknown_terms=["Zephyr", "247", "$9000", "Alex", "Redis", "Atlas", "PostgreSQL"],
)

# ── Scenario 7: provenance with near-identical messages ─────────────
PROVENANCE = GroundTruth(
    name="provenance",
    conversation="conversation-H",
    messages=[
        msg("p1", "Orion uses SQLite."),
        msg("p2", "Atlas uses SQLite."),          # same value, different subject
        msg("p3", "Orion uses PostgreSQL."),
    ],
    allowed_names=["Orion", "Atlas", "SQLite", "PostgreSQL"],
)


# ── Scenario 2b: contradiction with explicit signalling ─────────────
CONTRADICTION_SIGNALLED = GroundTruth(
    name="contradiction_signalled",
    conversation="conversation-C2",
    messages=[
        msg("s1", "We decided on SQLite for Orion."),
        msg("s2", "Actually we should use PostgreSQL for Orion."),
    ],
    allowed_names=["Orion", "SQLite", "PostgreSQL"],
    expected_decisions=["SQLite"],          # s1 is firm; s2 is a suggestion
    expected_contradictions=1,
    expected_history=[("SQLite", "mid_s1"), ("PostgreSQL", "mid_s2")],
)

# ── Scenario 5b: explicit supersession ──────────────────────────────
SUPERSESSION = GroundTruth(
    name="explicit_supersession",
    conversation="conversation-F2",
    messages=[
        msg("e1", "We decided to use SQLite."),
        msg("e2", "We decided to use PostgreSQL instead of SQLite."),
    ],
    allowed_names=["SQLite", "PostgreSQL"],
    expected_decisions=["SQLite", "PostgreSQL"],
    expected_history=[("SQLite", "mid_e1"), ("PostgreSQL", "mid_e2")],
)

# ── Scenario 7b: relation object garbled by greedy capture ──────────
GARBLED = GroundTruth(
    name="garbled_relation",
    conversation="conversation-I",
    messages=[msg("g1", "Orion uses SQLite because it is lightweight.")],
    allowed_names=["Orion", "SQLite"],
    expected_relations=[("Orion", "uses", "SQLite")],
)


ALL_SCENARIOS: List[GroundTruth] = [
    CONV_A, CONV_B, CONTRADICTION, CONTRADICTION_SIGNALLED, HYPOTHESES,
    RELATIONS, REVISIONS, SUPERSESSION, UNKNOWN, PROVENANCE, GARBLED,
]
