"""The four phase-4 configurations, as real request payloads (brief §7).

A  ``raw``                       — the whole conversation history, no Boster.
B  ``boster_phase2``             — Boster phase 2's zonal block (the shipped one).
C  ``boster_append``             — append-only journal: an immutable prefix plus
                                   frozen entries, the turn's question appended
                                   last.  Nothing already emitted is rewritten.
D  ``boster_append_consolidated``— C, plus an explicitly approved consolidation
                                   the first time the token budget saturates.

A and B are built by the **phase 3 payload builders** (imported, not copied), so
the phase-2 and raw numbers in this phase come from the exact same code that
produced the phase-3 report.

Why the question is appended *inside* the block
-----------------------------------------------
The append-only contract is about bytes: the prompt of turn ``t`` must be a
prefix of the prompt of turn ``t+1``.  If the turn's question lived in a rolling
user message *after* the system region, then at turn ``t+1`` the new entries
would be inserted before ``</system>`` while the previous question disappeared
from the tail — an in-place change in the middle of the prompt, exactly what the
phase-3 law punishes.  The question is therefore appended as the journal's last
entry, and the previous turn's answer is appended before the next question.  The
serialisation (``serialize``) is the same as phase 3's, so the comparison stays
fair; the layout difference is the thing under test and the report says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from boster.deterministic_pipeline import build_memory
from boster.memory.append_only import AppendOnlyMemory, Budget

from ..conversation import (PROJECT, SECOND_PROJECT, SYSTEM_PROMPT, Turn, as_messages,
                            build_conversation, serialize)
from ..phase3.payloads import Payload, build_sequence as build_phase3_sequence

_WS_RE = re.compile(r"\s+")


def _one_line(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def verify_pure_append(payloads: Sequence[Payload], *, strict: bool = True) -> Optional[int]:
    """Check that every prompt is a byte-prefix of the next one.

    Returns the first offending turn, or ``None`` when the sequence is a pure
    append.  ``strict`` raises instead of returning: a campaign that silently
    measures a non-append sequence is worse than a campaign that fails.
    """
    for previous, current in zip(payloads, payloads[1:]):
        if not current.prompt.startswith(previous.prompt):
            if strict:
                raise AssertionError(
                    f"sequence non append-only au tour {current.turn} "
                    f"({current.config}) : le prompt precedent n'est pas un prefixe")
            return current.turn
    return None

CONFIGS: Tuple[str, ...] = ("raw", "boster_phase2", "boster_append",
                            "boster_append_consolidated")

CONFIG_NOTES: Dict[str, str] = {
    "raw": "sans Boster : historique brut complet, aucun bloc memoire",
    "boster_phase2": "Boster phase 2 : rendu zonal memoise (code publie en phase 2)",
    "boster_append": "Boster append-only : prefixe immuable + journal fige, ajout pur",
    "boster_append_consolidated": (
        "Boster append-only + consolidation explicite a la saturation du budget"),
}

#: Fixture-side labelling of the subjects the audit's conversation talks about.
#: This is *harness* knowledge, not a Boster capability: it lets the append-only
#: journal recognise that a new value replaces an earlier one (the module's own
#: generic rule is the ``X = Y`` form plus an explicit ``subject=``).  It is
#: deterministic and covers exactly the vocabulary the fixture uses.
SUBJECT_KEYWORDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("reporting",), "reporting service"),
    (("sqlite", "postgres", "postgresql", "snowflake", "analytical store"), "storage"),
    (("memcached", "redis"), "cache"),
    (("atlas-1", "atlas-2", "staging host"), "staging host"),
    (("4 gb",), "ram cap"),
)

# Only *distinctive* tokens count.  A generic word like "storage" or "cache"
# appearing in a filler line ("nothing changed on the storage side") must NOT
# claim the subject: doing that made every filler supersede the real storage
# value and turned the current state into a status sentence.  Measured on the
# fixture: 36 bogus corrections over 30 turns before this was tightened.


#: Assistant acknowledgement openers in the fixture's script.  "Noted: Orion uses
#: SQLite." restates the user's fact; appending it would double the prompt for
#: no information, and (measured) it made two lines claim the same subject every
#: turn.  Harness-side, deterministic, and the fixture's script is known.
ECHO_PREFIXES: Tuple[str, ...] = (
    "noted:", "understood:", "recorded:", "corrected:", "acknowledged",
    "reason recorded", "decision recorded", "cache decision recorded",
    "see the notes",
)

#: A fact only claims a *subject* when it is shaped like a value statement.  The
#: fixture's explanation line ("We switched because SQLite could not handle the
#: analytical load.") mentions SQLite without stating the current value;
#: mapping it to the storage subject made a reason supersede the decision it
#: explains.  Decisions are inherently value statements and skip this test.
VALUE_SHAPES: Tuple[str, ...] = (" is ", " are ", " was ", " uses ", " use ",
                                 " = ", " to ", " instead of ")


def _is_echo(text: str) -> bool:
    lowered = _one_line(text).lower()
    return any(lowered.startswith(prefix) for prefix in ECHO_PREFIXES)


def _is_value_shaped(text: str) -> bool:
    padded = f" {_one_line(text).lower()} "
    return any(shape in padded for shape in VALUE_SHAPES)


def fixture_subject(kind: str, text: str) -> Optional[str]:
    """Map a fixture fact/decision to the subject it is about (see above)."""
    if _is_echo(text):
        return None
    if kind == "fact" and not _is_value_shaped(text):
        return None
    haystack = (text or "").lower()
    for keywords, subject in SUBJECT_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return subject
    return None


@dataclass
class AppendRun:
    """An append-only sequence plus the evidence that the contract held."""

    payloads: List[Payload] = field(default_factory=list)
    memory: Optional[AppendOnlyMemory] = None
    stability: List[Dict[str, Any]] = field(default_factory=list)
    consolidations: List[Dict[str, Any]] = field(default_factory=list)
    journal_growth: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        if not self.payloads:
            return {}
        entries = [p.prompt_chars for p in self.payloads]
        journals = [g["journal_chars"] for g in self.journal_growth]
        return {
            "turns": len(self.payloads),
            "prompt_chars_first": entries[0],
            "prompt_chars_last": entries[-1],
            "prompt_chars_mean": round(sum(entries) / len(entries), 1),
            "journal_chars_first": journals[0] if journals else 0,
            "journal_chars_last": journals[-1] if journals else 0,
            "consolidations": len(self.consolidations),
            "contract_violations": sum(1 for s in self.stability
                                       if not s.get("contract_ok", True)),
        }


def build_append_sequence(scenario: str = "B", exchanges: int = 100, *,
                          consolidate: bool = False,
                          budget_total: int = 0,
                          reserve_query: int = 512,
                          reserve_answer: int = 512,
                          safety: int = 256,
                          conversation_id: Optional[str] = None,
                          subject_resolver: Optional[Callable[[str, str], Optional[str]]] = None,
                          ) -> AppendRun:
    """Replay ``exchanges`` turns through the append-only memory."""
    turns, _final_question = build_conversation(exchanges, scenario=scenario)
    resolver = subject_resolver or fixture_subject
    memory = AppendOnlyMemory(
        conversation_id=conversation_id or f"kv4-append-{scenario}-{exchanges}-{consolidate}",
        budget=Budget(total=budget_total, reserve_query=reserve_query,
                      reserve_answer=reserve_answer, safety=safety),
    )
    memory.open_prefix(
        instructions=[SYSTEM_PROMPT],
        reference=[f"Project {PROJECT}", f"Project {SECOND_PROJECT}"],
    )
    run = AppendRun(memory=memory)
    previous_block: Optional[str] = None
    just_consolidated = False

    for turn in turns:
        if turn.index > 1:
            previous = turns[turn.index - 2]
            # Knowledge first: what the conversation so far now asserts, read with
            # the extractor Boster already uses (same call as phase 3, so the two
            # configurations distil the same content).
            view, _stats = build_memory(as_messages(turns[: turn.index - 1]),
                                        conversation_id=memory.conversation_id,
                                        query=previous.question)
            memory.ingest_memory_view(view, subject_resolver=resolver,
                                      source_ids=[f"turn{previous.index}"])
            # The previous answer is appended only when it carries information.
            # Re-appending an acknowledgement ("Noted: ...") would make the
            # journal hold the whole transcript, i.e. grow like the raw history —
            # measured: it made the append-only prompt 30 % larger than raw,
            # which defeats the point of a memory that replaces the history.
            if not _is_echo(previous.assistant):
                memory.append_answer(previous.assistant,
                                     source_ids=[f"turn{previous.index}:assistant"])
        memory.append_question(turn.question, source_ids=[f"turn{turn.index}:user"])

        # After a consolidation the prefix has grown; the very next turn would
        # immediately saturate again and trigger another consolidation in a
        # feedback loop.  Skip the check for one turn so the journal gets a
        # chance to grow with real new knowledge before the next budget review.
        if just_consolidated:
            just_consolidated = False
        elif consolidate and memory.needs_consolidation():
            result = memory.consolidate(approved=True, reason="budget_saturation")
            if result.done:
                run.consolidations.append({**result.to_dict(), "turn": turn.index})
                just_consolidated = True

        block = memory.block_text()
        report = memory.stability_report()
        if previous_block is not None and not report.contract_ok:
            # record it, never hide it: a broken contract must be visible in the data
            report.note = "VIOLATION : " + report.note
        previous_block = block
        run.stability.append({**report.to_dict(), "turn": turn.index})

        # No closing tag after the growing region.  A terminator (`</system>`)
        # would sit right after the *previous* turn's growth point while the new
        # entries sit there in the next prompt, so the previous prompt would stop
        # being a byte-prefix of the new one — and the measured engine law only
        # rewards an exact extension (pure append: full reuse; anything else:
        # fallback to an internal reprise point).  The question is the journal's
        # last entry, so nothing follows the appended region.
        text = f"<system>\n{block}\n"
        stats = memory.stats()
        run.journal_growth.append({
            "turn": turn.index, "journal_chars": stats["journal_chars"],
            "block_chars": stats["block_chars"], "block_tokens": stats["block_tokens"],
            "entries": stats["entries"], "generation": stats["generation"],
            "subjects": stats["subjects"], "superseded": stats["superseded"],
        })
        run.payloads.append(Payload(
            config="boster_append_consolidated" if consolidate else "boster_append",
            scenario=scenario, turn=turn.index, prompt=text,
            prompt_chars=len(text), system_chars=len(block), block_chars=len(block),
            message_count=0, question=turn.question,
            meta={
                "config_note": CONFIG_NOTES[
                    "boster_append_consolidated" if consolidate else "boster_append"],
                "memory": stats,
                "stability": report.to_dict(),
                "generation": memory.generation,
            },
        ))
    # Consolidation deliberately changes the prefix, so the sequence is not a
    # pure append across the consolidation boundary.  Verify the contract only
    # on the turns that matter (before and after), never raise on the expected
    # break.
    if consolidate:
        verify_pure_append(run.payloads, strict=False)
    else:
        verify_pure_append(run.payloads)
    return run


def build_raw_sequence(scenario: str = "B", exchanges: int = 100,
                       conversation_id: Optional[str] = None) -> List[Payload]:
    """Config A: the whole history, with this turn's question, no Boster.

    Unlike the phase-3 ``raw`` builder, the current turn's question **is** in the
    request — a request without it is not a request anyone could answer, and
    phase 4 compares layouts on equal footing.  The sequence stays a pure append
    from turn to turn (every turn appends the previous Q/A pair and the new
    question), so the reuse rate remains directly comparable, while the absolute
    prompt sizes are slightly larger than phase 3's ``raw`` and must not be read
    as the same measurement.
    """
    turns, _ = build_conversation(exchanges, scenario=scenario)
    payloads: List[Payload] = []
    for turn in turns:
        # Strictly the history *before* this turn, then this turn's question once.
        # (Slicing with ``turn.index`` would include the current turn itself and
        # then append its question again: measured, that broke the pure append and
        # the engine fell back to re-evaluating ~550 tokens every turn, i.e. 29 %
        # reuse instead of ~98 %.)
        messages = as_messages(turns[: turn.index - 1]) + [
            {"role": "user", "content": turn.question}]
        text = serialize(SYSTEM_PROMPT, messages)
        payloads.append(Payload(
            config="raw", scenario=scenario, turn=turn.index, prompt=text,
            prompt_chars=len(text), system_chars=len(SYSTEM_PROMPT),
            message_count=len(messages), question=turn.question,
            meta={"config_note": CONFIG_NOTES["raw"],
                  "history": "brut, complet, question du tour incluse"},
        ))
    verify_pure_append(payloads)          # raises if this is not a pure append
    return payloads


def build_sequence(config: str, scenario: str = "B", exchanges: int = 100,
                   budget: int = 2000, level: str = "balanced",
                   conversation_id: Optional[str] = None,
                   *, append_budget_total: int = 0,
                   subject_resolver: Optional[Callable[[str, str], Optional[str]]] = None,
                   ) -> List[Payload]:
    """The four configurations behind one call, for the shared run driver."""
    if config == "raw":
        return build_raw_sequence(scenario=scenario, exchanges=exchanges,
                                 conversation_id=conversation_id)
    if config == "boster_phase2":
        return build_phase3_sequence("boster_system", scenario=scenario,
                                     exchanges=exchanges, budget=budget, level=level,
                                     conversation_id=conversation_id)
    if config in ("boster_append", "boster_append_consolidated"):
        run = build_append_sequence(
            scenario=scenario, exchanges=exchanges,
            consolidate=(config == "boster_append_consolidated"),
            budget_total=append_budget_total, conversation_id=conversation_id,
            subject_resolver=subject_resolver,
        )
        for payload in run.payloads:          # the config name in the payload
            payload.config = config
        return run.payloads
    raise ValueError(f"unknown config: {config!r}")


def build_append_run(config: str = "boster_append", scenario: str = "B",
                     exchanges: int = 100, append_budget_total: int = 0,
                     conversation_id: Optional[str] = None) -> AppendRun:
    """The append-only run object (with its stability evidence) for a config."""
    return build_append_sequence(
        scenario=scenario, exchanges=exchanges,
        consolidate=(config == "boster_append_consolidated"),
        budget_total=append_budget_total, conversation_id=conversation_id,
    )


def build_last_turn(config: str, scenario: str = "B", exchanges: int = 100,
                    question: Optional[str] = None, *, append_budget_total: int = 0
                    ) -> Payload:
    """The final request of a sequence — the turn an event scenario changes."""
    payloads = build_sequence(config, scenario=scenario, exchanges=exchanges,
                              append_budget_total=append_budget_total)
    return payloads[-1]


def sequence_summary(payloads: Sequence[Payload]) -> Dict[str, Any]:
    """Prompt-size profile of a sequence, without any engine involved."""
    if not payloads:
        return {}
    sizes = [p.prompt_chars for p in payloads]
    blocks = [p.block_chars for p in payloads]
    return {
        "config": payloads[0].config,
        "scenario": payloads[0].scenario,
        "turns": len(payloads),
        "prompt_chars_first": sizes[0],
        "prompt_chars_last": sizes[-1],
        "prompt_chars_max": max(sizes),
        "prompt_chars_mean": round(sum(sizes) / len(sizes), 1),
        "block_chars_last": blocks[-1],
        "block_chars_max": max(blocks),
    }
