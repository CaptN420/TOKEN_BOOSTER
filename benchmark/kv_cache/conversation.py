"""Deterministic turn-by-turn conversations for the KV-cache audit.

The audit needs a conversation that *grows*, one exchange at a time, so that each
step can be fed to the pipeline exactly as a caller would feed it at that moment.
Every turn carries the question the caller is asking at that turn, because that
question is what the pipeline's retrieval is conditioned on.

No randomness: every string is fixed, so two runs produce byte-identical text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

SYSTEM_PROMPT = (
    "You are a careful engineering assistant. Answer from the conversation; "
    "if something is not known, say so instead of guessing. Never drop a standing "
    "rule, a constraint or a current decision. Keep answers short."
)

PROJECT = "Orion"
SECOND_PROJECT = "Atlas"


@dataclass(frozen=True)
class Turn:
    """One exchange plus the question the caller asks at this turn."""

    index: int
    user: str
    assistant: str

    @property
    def question(self) -> str:
        """What the pipeline's retrieval is conditioned on at this turn."""
        return self.user


# The fixed opening script: early facts, a persistent constraint, a relation, a
# decision change with its reason, and a second change — the classes the audit
# must exercise (early fact, change, relation, constraint, obsolete value).
_SCRIPT: Dict[int, Tuple[str, str]] = {
    1: (f"Project {PROJECT} uses SQLite for local storage.",
        f"Noted: {PROJECT} uses SQLite."),
    2: ("The constraint is at most 4 GB of RAM on the analytical node, and it "
        "applies to every decision we make here.",
        "Understood: 4 GB cap recorded as a standing constraint."),
    3: (f"The staging host is {SECOND_PROJECT.lower()}-1.",
        f"Noted: staging host {SECOND_PROJECT.lower()}-1."),
    4: (f"The cache in {PROJECT} is Memcached.",
        "Noted: Memcached."),
    5: ("The reporting service depends on PostgreSQL.",
        "Recorded: the reporting service depends on PostgreSQL."),
    6: ("We decided to use PostgreSQL instead of SQLite for the analytical store.",
        "Decision recorded: PostgreSQL replaces SQLite."),
    7: ("We switched because SQLite could not handle the analytical load.",
        "Reason recorded."),
    8: ("We changed the cache to Redis instead of Memcached.",
        "Cache decision recorded: Redis replaces Memcached."),
}

# Late events, placed just before the final turn, so that the memory state
# changes *during* the measured window (that is the point of scenarios D and E).
LATE_EVENTS: Dict[str, Tuple[str, str]] = {
    "decision_change": (
        "We changed the analytical store to Snowflake instead of PostgreSQL.",
        "Decision recorded: Snowflake replaces PostgreSQL."),
    "fact_correction": (
        f"Correction: the staging host is {SECOND_PROJECT.lower()}-2, not "
        f"{SECOND_PROJECT.lower()}-1.",
        f"Corrected: staging host is {SECOND_PROJECT.lower()}-2."),
}

FINAL_QUESTIONS: Dict[str, str] = {
    "current": f"Which database does {PROJECT} use now?",
    "history": f"Which database did {PROJECT} use before?",
    "reason": "Why was the storage change made?",
    "relation": "What does the reporting service depend on?",
    "unknown": "Which database does project Vega use?",
    "multi": "What decisions were made about storage and caching over time?",
}


def _filler(index: int) -> Tuple[str, str]:
    return (f"Status check {index}: nothing changed on the storage side.",
            "Acknowledged.")


def build_conversation(exchanges: int, *, scenario: str = "B"
                       ) -> Tuple[List[Turn], str]:
    """Return ``(turns, final_question)`` for ``exchanges`` exchanges.

    ``scenario`` selects the late event / final question under test:
      B/C/G/H : no late event, the "current" question
      D       : a decision change at the second-to-last turn
      E       : a fact correction at the second-to-last turn
      F       : a historical question ("... use before?")
    """
    late = "decision_change" if scenario == "D" else (
        "fact_correction" if scenario == "E" else "")
    question_key = "history" if scenario == "F" else "current"

    turns: List[Turn] = []
    for index in range(1, exchanges + 1):
        if index in _SCRIPT:
            user, assistant = _SCRIPT[index]
        elif late and index == exchanges - 1:
            user, assistant = LATE_EVENTS[late]
        elif index == exchanges:
            user, assistant = FINAL_QUESTIONS[question_key], "See the notes above."
        else:
            user, assistant = _filler(index)
        turns.append(Turn(index=index, user=user, assistant=assistant))
    return turns, FINAL_QUESTIONS[question_key]


def as_messages(turns: Sequence[Turn]) -> List[Dict[str, str]]:
    """Flatten turns into the message list the pipeline consumes."""
    messages: List[Dict[str, str]] = []
    for turn in turns:
        messages.append({"role": "user", "content": turn.user})
        messages.append({"role": "assistant", "content": turn.assistant})
    return messages


def serialize(system_text: str, messages: Sequence[Dict[str, str]]) -> str:
    """Canonical text serialisation of a request.

    This is a *proxy* for the engine's own token stream: the real prefix a KV
    cache sees depends on the model's chat template (special tokens, separators).
    Character-level prefix equivalence measured here is therefore a necessary,
    not a sufficient, condition for cache reuse.
    """
    parts = [f"<system>\n{system_text}\n</system>\n"] if system_text else []
    for message in messages:
        parts.append(f"<{message['role']}>\n{message['content']}\n</{message['role']}>\n")
    return "".join(parts)


def split_system(system_text: str, block_header: str) -> Tuple[str, str]:
    """Split the system text into ``(stable instructions, volatile block)``."""
    index = system_text.find(block_header)
    if index < 0:
        return system_text, ""
    return system_text[:index].rstrip(), system_text[index:]
