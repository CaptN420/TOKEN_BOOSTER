"""Deterministic memory scenarios (10 → 200 exchanges) with independent ground truth.

The generator is a pure function of ``exchanges``: same size, same conversation,
same ground truth, on every run and every machine. The ground truth is declared
HERE, next to the text that carries it, and never derived from pipeline output.

Every scenario deliberately contains the ten content classes the brief requires:
early facts, decision changes, entity relations, persistent constraints,
repeated information, obsolete information, contradictions, decision reasons,
a second independent conversation, and questions that need several episodes.

Run:  PYTHONPATH="." uv run python -m benchmark.deterministic_pipeline.scenarios
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MemoryFact", "MemoryDecision", "MemoryRelation", "Question", "Scenario",
    "SIZES", "build_scenario", "all_scenarios",
]

SIZES = (10, 25, 50, 100, 200)


@dataclass(frozen=True)
class MemoryFact:
    subject: str
    value: str
    source_index: int
    status: str = "current"          # current | obsolete


@dataclass(frozen=True)
class MemoryDecision:
    value: str
    status: str                      # active | superseded
    source_index: int
    reason: str = ""
    reason_index: int = -1


@dataclass(frozen=True)
class MemoryRelation:
    subject: str
    predicate: str
    object: str
    source_index: int


@dataclass(frozen=True)
class Question:
    """A question asked over the same conversation."""
    text: str
    kind: str                        # current | history | reason | unknown | multi_episode
    expect_in_context: Tuple[str, ...] = ()     # values the context must contain
    expect_absent: Tuple[str, ...] = ()         # values the context must NOT contain
    expect_history: bool = False                # the history section is required
    impossible: bool = False                    # the answer does not exist in the conversation
    unknown_subject: str = ""                   # the name an impossible question asks about


@dataclass(frozen=True)
class Scenario:
    name: str
    exchanges: int
    conversation_id: str
    system_prompt: str
    conversation: List[Dict[str, str]]
    questions: Tuple[Question, ...]
    facts: Tuple[MemoryFact, ...] = ()
    decisions: Tuple[MemoryDecision, ...] = ()
    relations: Tuple[MemoryRelation, ...] = ()
    constraints: Tuple[Tuple[str, int], ...] = ()       # (text, source_index)
    reasons: Tuple[Tuple[str, int], ...] = ()
    obsolete_values: Tuple[str, ...] = ()
    contradictions: Tuple[Tuple[str, str, int], ...] = ()   # (old, new, index)
    foreign_entities: Tuple[str, ...] = ()              # must not leak into the context
    notes: str = ""

    def question(self, kind: str) -> Question:
        for candidate in self.questions:
            if candidate.kind == kind:
                return candidate
        raise KeyError(kind)

    def source_texts(self) -> List[str]:
        return [self.system_prompt] + [m["content"] for m in self.conversation]


SYSTEM = ("You are a careful engineering assistant. Do not invent facts that are "
          "not in the context. Always report errors verbatim.")

# Distinctive values, so a name appearing in the output cannot be a coincidence.
DB_OLD = "SQLite"
DB_NEW = "PostgreSQL"
CACHE_OLD = "Memcached"
CACHE_NEW = "Redis"
OTHER_PROJECT = "Atlas"
OTHER_DB = "Cassandra"
UNKNOWN_PROJECT = "Vega"


def _positions(count: int, fractions: Sequence[float], reserve_last: int = 1) -> List[int]:
    """Strictly increasing indices, spread by fraction, never touching the last turn."""
    limit = max(count - reserve_last - 1, len(fractions) - 1)
    out: List[int] = []
    for fraction in fractions:
        raw = int(round(fraction * max(limit, 1)))
        raw = max(0, min(raw, limit))
        while out and raw <= out[-1]:
            raw += 1
        if raw > limit:
            raw = out[-1] + 1 if out else 0
        out.append(raw)
    return out


def build_scenario(exchanges: int) -> Scenario:
    """Build one scenario of exactly ``exchanges`` user turns."""
    f = _positions(exchanges, (0.0, 0.05, 0.25, 0.26, 0.35, 0.5, 0.6, 0.62), reserve_last=1)
    i_fact, i_constraint, i_decision, i_reason, i_relation, i_other, i_cache_old, i_cache_new = f
    i_question = exchanges - 1

    slots: Dict[int, List[Dict[str, str]]] = {}

    def add(index: int, content: str, role: str = "user") -> None:
        slots.setdefault(index, []).append({"role": role, "content": content})

    # 1. early fact (becomes obsolete later) — must survive a whole conversation
    add(i_fact, f"Project Orion uses {DB_OLD} for local storage.")
    add(i_fact, f"Noted: Orion uses {DB_OLD}.", role="assistant")
    # 2. persistent constraint
    add(i_constraint, "The constraint is at most 4 GB of RAM on the analytical node.")
    add(i_constraint, "Recorded: at most 4 GB of RAM.", role="assistant")
    # 3. decision change (current) + 4. its reason on the NEXT exchange
    add(i_decision, f"We decided to use {DB_NEW} instead of {DB_OLD} for the analytical store.")
    add(i_decision, f"Decision: {DB_NEW} is now the analytical store.", role="assistant")
    add(i_reason, f"We switched because {DB_OLD} could not handle the analytical load.")
    add(i_reason, "Reason recorded.", role="assistant")
    # 5. entity relation
    add(i_relation, f"The reporting service depends on {DB_NEW}.")
    add(i_relation, "Relation recorded.", role="assistant")
    # 6. second, independent conversation (leak check)
    add(i_other, f"Project {OTHER_PROJECT} uses {OTHER_DB} for telemetry.")
    add(i_other, f"Noted: {OTHER_PROJECT} uses {OTHER_DB}.", role="assistant")
    # 7. obsolete information + 8. contradiction
    add(i_cache_old, f"The cache in Orion is {CACHE_OLD}.")
    add(i_cache_old, f"Cache: {CACHE_OLD}.", role="assistant")
    add(i_cache_new, f"We changed the cache to {CACHE_NEW} instead of {CACHE_OLD}.")
    add(i_cache_new, f"Cache is now {CACHE_NEW}.", role="assistant")

    conversation: List[Dict[str, str]] = []
    filler = 0
    for index in range(exchanges):
        if index in slots:
            conversation.extend(slots[index])
            continue
        if index == i_question:
            break
        # repeated information: the same status line, over and over
        conversation.append({"role": "user",
                             "content": f"Status check {filler}: migration in progress, no blocker."})
        conversation.append({"role": "assistant", "content": f"ACK {filler}: migration in progress."})
        filler += 1

    conversation.append({"role": "user",
                         "content": f"Where is the analytical store hosted and on which database?"})
    conversation.append({"role": "assistant", "content": "Let me check the context."})
    conversation.append({"role": "user", "content": f"Which database does Orion use now?"})

    by_content = {m["content"]: i for i, m in enumerate(conversation)}

    def src(text: str) -> int:
        return by_content.get(text, -1)

    questions = (
        Question(
            text=f"Which database does Orion use now?",
            kind="current",
            expect_in_context=(DB_NEW,),
            expect_absent=(OTHER_DB,),
        ),
        Question(
            text=f"Which database did Orion use before?",
            kind="history",
            expect_in_context=(DB_OLD,),
            expect_history=True,
        ),
        Question(
            text="Why was the storage change made?",
            kind="reason",
            expect_in_context=(DB_OLD,),
            expect_history=True,
        ),
        Question(
            text=f"Which database does project {UNKNOWN_PROJECT} use?",
            kind="unknown",
            expect_absent=(OTHER_DB,),
            impossible=True,
            unknown_subject=UNKNOWN_PROJECT,
        ),
        Question(
            text="What decisions were made about storage and caching over time?",
            kind="multi_episode",
            expect_in_context=(DB_NEW, CACHE_NEW),
            expect_history=True,
        ),
    )

    return Scenario(
        name=f"mem_{exchanges}",
        exchanges=exchanges,
        conversation_id=f"conv-mem-{exchanges}",
        system_prompt=SYSTEM,
        conversation=conversation,
        questions=questions,
        facts=(
            MemoryFact("Orion", DB_OLD, src(f"Project Orion uses {DB_OLD} for local storage."),
                       status="obsolete"),
            MemoryFact("Orion", DB_NEW,
                       src(f"We decided to use {DB_NEW} instead of {DB_OLD} for the analytical store.")),
            MemoryFact("Orion", CACHE_OLD, src(f"The cache in Orion is {CACHE_OLD}."),
                       status="obsolete"),
            MemoryFact("Orion", CACHE_NEW,
                       src(f"We changed the cache to {CACHE_NEW} instead of {CACHE_OLD}.")),
            MemoryFact(OTHER_PROJECT, OTHER_DB, src(f"Project {OTHER_PROJECT} uses {OTHER_DB} for telemetry.")),
        ),
        decisions=(
            MemoryDecision(DB_NEW, "active",
                           src(f"We decided to use {DB_NEW} instead of {DB_OLD} for the analytical store."),
                           reason=f"{DB_OLD} could not handle the analytical load",
                           reason_index=src(f"We switched because {DB_OLD} could not handle the analytical load.")),
            MemoryDecision(DB_OLD, "superseded",
                           src(f"Project Orion uses {DB_OLD} for local storage.")),
            MemoryDecision(CACHE_NEW, "active",
                           src(f"We changed the cache to {CACHE_NEW} instead of {CACHE_OLD}.")),
            MemoryDecision(CACHE_OLD, "superseded", src(f"The cache in Orion is {CACHE_OLD}.")),
        ),
        relations=(
            MemoryRelation("reporting service", "depends on", DB_NEW,
                           src(f"The reporting service depends on {DB_NEW}.")),
        ),
        constraints=(
            ("at most 4 GB of RAM", src("The constraint is at most 4 GB of RAM on the analytical node.")),
        ),
        reasons=(
            (f"{DB_OLD} could not handle the analytical load",
             src(f"We switched because {DB_OLD} could not handle the analytical load.")),
        ),
        obsolete_values=(DB_OLD, CACHE_OLD),
        contradictions=((CACHE_OLD, CACHE_NEW,
                         src(f"We changed the cache to {CACHE_NEW} instead of {CACHE_OLD}.")),),
        foreign_entities=(OTHER_PROJECT, OTHER_DB),
        notes=("early fact at turn 0, constraint at turn 1, decision change at 25 %, reason at "
               "26 %, relation at 35 %, second project at 50 %, obsolete value at 60 %, "
               "contradiction at 62 %, repeated filler elsewhere"),
    )


def all_scenarios(sizes: Sequence[int] = SIZES) -> Tuple[Scenario, ...]:
    return tuple(build_scenario(size) for size in sizes)


if __name__ == "__main__":
    for scenario in all_scenarios():
        specials = len(scenario.questions)
        print(f"{scenario.name}: turns={len(scenario.conversation)} "
              f"chars={sum(len(m['content']) for m in scenario.conversation)} "
              f"facts={len(scenario.facts)} decisions={len(scenario.decisions)} "
              f"relations={len(scenario.relations)} constraints={len(scenario.constraints)} "
              f"reasons={len(scenario.reasons)} questions={specials}")
