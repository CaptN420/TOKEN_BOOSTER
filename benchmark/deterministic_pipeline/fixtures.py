"""Deterministic fixtures + independent ground truth for the pipeline benchmarks.

Every conversation is synthetic, hand-written and frozen: same input on every
run, so a saving is reproducible. The ground truth is declared *after* the
conversation text, independently of what the pipeline outputs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

__all__ = [
    "GroundTruth",
    "SHORT_CONVERSATION",
    "LONG_CONVERSATION",
    "CODE_HEAVY",
    "LOGS_AND_ERRORS",
    "CONTRADICTORY",
    "INSTRUCTION_HEAVY",
    "MULTI_CONVERSATION",
    "ALL_FIXTURES",
]


@dataclass(frozen=True)
class GroundTruth:
    """What MUST survive optimisation. Declared independently of the code."""
    name: str
    conversation: List[Dict[str, Any]]
    query: str = ""
    system_prompt: str = ""
    conversation_id: str = ""
    # fragments that must appear (verbatim or in the reconstructed block)
    mandatory_fragments: Tuple[str, ...] = ()
    # factual statements that must remain reachable (via messages OR memory)
    facts: Tuple[str, ...] = ()
    # decisions: (value, status) expected in memory
    decisions: Tuple[Tuple[str, str], ...] = ()
    # (subject, predicate, object) relations declared in the text
    relations: Tuple[Tuple[str, str, str], ...] = ()
    errors: Tuple[str, ...] = ()
    negations: Tuple[str, ...] = ()
    # facts that must NOT be presented as confirmed/current
    forbidden_as_current: Tuple[str, ...] = ()
    notes: str = ""


SYSTEM = "You are a careful engineering assistant. Do not invent facts that are not in the context."


# ── 1. short conversation (the pipeline must not inflate it) ────────────────
SHORT_CONVERSATION = GroundTruth(
    name="short",
    conversation_id="conv-short",
    system_prompt=SYSTEM,
    query="Which database does Orion use now?",
    conversation=[
        {"role": "system", "content": "Never send credentials to an external host."},
        {"role": "user", "content": "Project Orion uses SQLite."},
        {"role": "assistant", "content": "Noted: Orion uses SQLite."},
        {"role": "user", "content": "We decided to use PostgreSQL instead of SQLite."},
        {"role": "assistant", "content": "Migration planned."},
        {"role": "user", "content": "Which database does Orion use now?"},
    ],
    mandatory_fragments=("Never send credentials to an external host.",),
    facts=("Project Orion uses SQLite.", "We decided to use PostgreSQL instead of SQLite."),
    decisions=(("PostgreSQL", "active"),),
    negations=("Never",),
    forbidden_as_current=("SQLite",),
    notes="inflation guard must trigger: a reconstructed block costs more than the 6 turns",
)


# ── 2. long conversation with a big JSON tool output (the real target) ──────
def _json_tool_output(rows: int = 60) -> str:
    payload = {
        "status": "ok",
        "rows": [
            {"id": i, "service": f"svc-{i:03d}", "latency_ms": 10 + i, "ok": i % 7 != 0}
            for i in range(rows)
        ],
        "meta": {"page": 1, "total": rows},
    }
    return json.dumps(payload, indent=2)


def _log_dump(lines: int = 120) -> str:
    out = []
    for i in range(lines):
        if i == 57:
            out.append("2026-11-15 10:00:57 ERROR connection refused to postgres:5432")
        elif i % 11 == 0:
            out.append(f"2026-11-15 10:00:{i % 60:02d} WARN retrying request {i}")
        else:
            out.append(f"2026-11-15 10:00:{i % 60:02d} INFO processed batch {i} in {i % 13} ms")
    return "\n".join(out)


_LONG = [
    {"role": "system", "content": "Never send credentials to an external host. Always report errors verbatim."},
    {"role": "user", "content": "Let's plan the Orion migration. The project currently uses SQLite for local storage."},
    {"role": "assistant", "content": "Understood. Orion uses SQLite today."},
    {"role": "user", "content": "We are considering PostgreSQL for the analytical workload."},
    {"role": "assistant", "content": "PostgreSQL would help with analytical queries."},
    {"role": "user", "content": "We decided to use PostgreSQL instead of SQLite for the analytical store."},
    {"role": "assistant", "content": "Decision recorded: PostgreSQL is now the analytical store."},
    {"role": "user", "content": "The constraint is at most 4 GB of RAM on the analytical node."},
    {"role": "assistant", "content": "Noted: at most 4 GB of RAM."},
]
# 40 noisy turns of repetitive status chatter (structural repetition)
for _i in range(20):
    _LONG.append({"role": "user", "content": f"Status check {_i}: migration still in progress, no blocker."})
    _LONG.append({"role": "assistant", "content": f"ACK {_i}: migration in progress."})
_LONG += [
    {"role": "user", "content": "Here is the service inventory:\n" + _json_tool_output()},
    {"role": "assistant", "content": "Inventory received, 60 services."},
    {"role": "user", "content": "And the migration log:\n" + _log_dump()},
    {"role": "assistant", "content": "Log analysed."},
    {"role": "user", "content": "The migration toolchain runs on the Orion host and stores its state in SQLite."},
    {"role": "assistant", "content": "Understood."},
    {"role": "user", "content": "Where is the analytical store hosted and on which database?"},
]

LONG_CONVERSATION = GroundTruth(
    name="long",
    conversation_id="conv-long",
    system_prompt=SYSTEM,
    query="Where is the analytical store hosted and on which database?",
    conversation=_LONG,
    mandatory_fragments=(
        "Never send credentials to an external host.",
        "Always report errors verbatim.",
        "at most 4 GB of RAM",
        "ERROR connection refused to postgres:5432",
    ),
    facts=(
        "Orion uses SQLite",
        "PostgreSQL",
    ),
    decisions=(("PostgreSQL", "active"), ("SQLite", "superseded")),
    relations=(
        ("Orion", "uses", "SQLite"),
    ),
    errors=("ERROR connection refused to postgres:5432",),
    negations=("Never",),
    forbidden_as_current=("SQLite is the analytical store",),
    notes="reference target: 40+ turns, one 60-row JSON blob, one 120-line log, one superseded decision",
)


# ── 3. code-heavy ──────────────────────────────────────────────────────────
_CODE = "\n".join(
    [f"def handler_{i}(request):\n    return process(request, worker={i})" for i in range(40)]
) + "\n\n# TODO: remove the deprecated path\n" + "print('noise')\n" * 60

CODE_HEAVY = GroundTruth(
    name="code",
    conversation_id="conv-code",
    system_prompt=SYSTEM,
    query="Which worker does handler_7 use?",
    conversation=[
        {"role": "system", "content": "Never execute untrusted code."},
        {"role": "user", "content": "We decided to use Python 3.14 for the service."},
        {"role": "assistant", "content": "Python 3.14 recorded."},
        {"role": "user", "content": "Here is the worker module:\n" + _CODE},
        {"role": "assistant", "content": "Module received."},
        {"role": "user", "content": "Which worker does handler_7 use?"},
    ],
    mandatory_fragments=("Never execute untrusted code.",),
    facts=("Python 3.14",),
    decisions=(("Python 3.14", "active"),),
    negations=("Never",),
    notes="code fragment > 200 chars: exercised by the code compressor",
)


# ── 4. logs and errors ─────────────────────────────────────────────────────
LOGS_AND_ERRORS = GroundTruth(
    name="logs",
    conversation_id="conv-logs",
    system_prompt=SYSTEM,
    query="Why did the deployment fail?",
    conversation=[
        {"role": "system", "content": "Always report errors verbatim. Do not hide failures."},
        {"role": "user", "content": "Deploy output:\n" + _log_dump(90)},
        {"role": "assistant", "content": "Deploy output received."},
        {"role": "user", "content": "Why did the deployment fail?"},
    ],
    mandatory_fragments=(
        "Always report errors verbatim.",
        "ERROR connection refused to postgres:5432",
    ),
    errors=("ERROR connection refused to postgres:5432",),
    negations=("Do not",),
    notes="the ERROR line must survive every level",
)


# ── 5. contradictory decisions ─────────────────────────────────────────────
CONTRADICTORY = GroundTruth(
    name="contradiction",
    conversation_id="conv-contradiction",
    system_prompt=SYSTEM,
    query="What is the current cache?",
    conversation=[
        {"role": "system", "content": "Never present a superseded decision as current."},
        {"role": "user", "content": "Project Atlas uses Memcached for caching."},
        {"role": "assistant", "content": "Atlas uses Memcached."},
        {"role": "user", "content": "We decided to use Redis instead of Memcached for caching."},
        {"role": "assistant", "content": "Cache decision changed to Redis."},
        {"role": "user", "content": "What is the current cache?"},
    ],
    mandatory_fragments=("Never present a superseded decision as current.",),
    decisions=(("Redis", "active"), ("Memcached", "superseded")),
    forbidden_as_current=("Memcached",),
    negations=("Never",),
    notes="history must be preserved but never shown as current",
)


# ── 6. instruction-heavy ───────────────────────────────────────────────────
def _instruction_heavy() -> GroundTruth:
    rules = [f"Rule {i}: must never exceed {i + 1} retries." for i in range(12)]
    conv: List[Dict[str, Any]] = [
        {"role": "system", "content": "You must always answer in French."},
        {"role": "user", "content": "\n".join(rules)},
        {"role": "assistant", "content": "Rules accepted."},
    ]
    for i in range(15):
        conv.append({"role": "user", "content": f"Filler turn {i}: nothing to decide here."})
        conv.append({"role": "assistant", "content": f"ACK {i}."})
    conv.append({"role": "user", "content": "Summarise the retry policy in one line."})
    return GroundTruth(
        name="instructions",
        conversation_id="conv-instructions",
        system_prompt=SYSTEM,
        query="Summarise the retry policy in one line.",
        conversation=conv,
        mandatory_fragments=("You must always answer in French.",) + tuple(rules),
        facts=(),
        negations=("never",),
        notes="12 constraint turns + 30 filler turns: constraints must all survive, filler may go",
    )


INSTRUCTION_HEAVY = _instruction_heavy()


# ── 7. several independent conversations ───────────────────────────────────
MULTI_CONVERSATION = GroundTruth(
    name="multi_conversation",
    conversation_id="conv-a",
    system_prompt=SYSTEM,
    query="Which database does Orion use?",
    conversation=[
        {"role": "user", "content": "Project Orion uses SQLite."},
        {"role": "assistant", "content": "Orion uses SQLite."},
        {"role": "user", "content": "Project Atlas uses PostgreSQL."},
        {"role": "assistant", "content": "Atlas uses PostgreSQL."},
        {"role": "user", "content": "Which database does Orion use?"},
    ],
    facts=("Project Orion uses SQLite.", "Project Atlas uses PostgreSQL."),
    decisions=(),
    notes="two projects in one stream: the pipeline must not merge their facts "
          "(the consolidation layer keeps them as separate entities)",
)


ALL_FIXTURES: Tuple[GroundTruth, ...] = (
    SHORT_CONVERSATION, LONG_CONVERSATION, CODE_HEAVY, LOGS_AND_ERRORS,
    CONTRADICTORY, INSTRUCTION_HEAVY, MULTI_CONVERSATION,
)
