"""Append-only memory journal — the KV-cache-friendly memory layout (Phase 4).

Why this module exists
----------------------
Phase KV CACHE 3 measured, with the engine's own counters, what a prompt prefix
costs (``llama-server``'s ``timings.cache_n`` on two separate builds):

* **a pure append reuses the whole prefix** (1 381 / 1 381 tokens);
* **an in-place change falls back to an internal reprise point** (~63 % of the
  prompt, the same value for a divergence at 75 % and at 98 %);
* **a divergence in the first half reuses nothing** (0), even when half the
  prompt is still identical.

Phase 2's zonal block is textually very stable (94,6 % measured) and still only
reused 65,3 %: re-deriving a section and re-emitting it in the middle of the
prompt *is* an in-place change.  This module therefore enforces the one layout
the engine rewards: **never modify a byte already emitted; append at the end.**

Layout produced
---------------
::

    === MEMORY (append-only) ===
    <immutable prefix: instructions, constraints known at open, reference>
    --- journal ---
    F001 [fact] Project Orion uses SQLite for local storage. [src:...]
    D001 [decision] PostgreSQL (supersedes F001) [src:...]
    Q003 [question] Which database does Orion use now? [src:...]
    ...
    and nothing after: the turn's question is the journal's LAST entry, so the
    whole prompt is a pure append from turn to turn.  A rolling user message
    after the block would be *removed* at the next turn — an in-place change in
    the middle of the prompt, which is exactly what the measured engine law
    punishes — and a closing terminator (``</system>``) would shift at every
    turn, so the previous prompt would stop being a byte-prefix of the next.
    Both were found by measurement, not by reading.

Four categories (brief §4)
--------------------------
A. **Immutable prefix** — frozen when the session opens; never touched again.
   A constraint *discovered mid-session* deliberately does **not** enter the
   prefix (that would modify it): it becomes a ``constraint`` journal entry, so
   it is still in the prompt and still traceable.
B. **Append-only journal** — typed entries with a stable id, content, provenance,
   an optional timestamp, an optional reference to the entry they correct, a
   status and an optional confidence.  Each entry's rendered line is **frozen at
   emission** (``frozen``): re-emitting the journal never reformats the past.
C. **Computed current state** — :meth:`AppendOnlyMemory.fold_current` walks the
   journal in order and applies the corrections ("last writer wins" per subject).
   No past entry is ever mutated: an entry is *superseded* because a later entry
   names it, and that is derived, not written into the old line.  The correcting
   entry carries ``(supersedes F001)``; the old line keeps saying exactly what it
   said.  A frozen line therefore never claims to be current, because it cannot
   know what will come next.
D. **Controlled consolidation** — :meth:`AppendOnlyMemory.consolidate` starts a
   new generation: the prefix gains a folded state snapshot (current values,
   with provenance and the ids they came from) and a fresh journal follows.  The
   original entries are **archived, never deleted**, retrievable by id.  The
   operation is always explicit and always reports
   ``cache_invalidated=True`` — a consolidation *does* break the prefix, and this
   module never pretends otherwise.

Determinism
-----------
Nothing here reads the clock.  ``created_at`` is ``None`` unless the caller
supplies it (and then the line says ``[date:...]``); a missing date is never
invented.  No iteration over a ``set``, no dependence on dict order beyond
insertion.  ``save()`` takes an explicit ``saved_at`` to stay byte-stable.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..core.tokenizer import count_tokens, get_tokenizer

SCHEMA_VERSION = 1

# ── entry types (brief §4.B) ────────────────────────────────────────────────
TYPE_FACT = "fact"
TYPE_CORRECTION = "correction"
TYPE_DECISION = "decision"
TYPE_DECISION_CHANGE = "decision_change"
TYPE_RELATION = "relation"
TYPE_CONSTRAINT = "constraint"
TYPE_EVENT = "event"
TYPE_RESOLUTION = "resolution"
TYPE_OBSOLETE = "obsolete"
TYPE_EXCHANGE = "exchange"
TYPE_QUESTION = "question"
TYPE_ANSWER = "answer"

ENTRY_TYPES: Tuple[str, ...] = (
    TYPE_FACT, TYPE_CORRECTION, TYPE_DECISION, TYPE_DECISION_CHANGE,
    TYPE_RELATION, TYPE_CONSTRAINT, TYPE_EVENT, TYPE_RESOLUTION,
    TYPE_OBSOLETE, TYPE_EXCHANGE, TYPE_QUESTION, TYPE_ANSWER,
)

# Stable id prefix per type, one series per type (as in the brief's
# FACT_001/FACT_002 example): F001, F002 … / C001, C002 … / D001 … / K001 … /
# R001 … / B001 … / E001 … / S001 … / O001 … / X001 …  Ids keep counting across
# generations, so an id is unique for the whole session and an archived entry
# can always be looked up.
TYPE_LETTERS: Dict[str, str] = {
    TYPE_FACT: "F",
    TYPE_CORRECTION: "C",
    TYPE_DECISION: "D",
    TYPE_DECISION_CHANGE: "K",
    TYPE_RELATION: "R",
    TYPE_CONSTRAINT: "B",
    TYPE_EVENT: "E",
    TYPE_RESOLUTION: "S",
    TYPE_OBSOLETE: "O",
    TYPE_EXCHANGE: "X",
    TYPE_QUESTION: "Q",
    TYPE_ANSWER: "A",
}

#: types whose content names a subject and can therefore supersede an earlier
#: entry about that subject
FOLDABLE_TYPES: Tuple[str, ...] = (
    TYPE_FACT, TYPE_CORRECTION, TYPE_DECISION, TYPE_DECISION_CHANGE,
    TYPE_CONSTRAINT,
)

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_OBSOLETE = "obsolete"

BLOCK_HEADER = "=== MEMORY (append-only) ==="
JOURNAL_SEPARATOR = "--- journal ---"
SNAPSHOT_HEADER = "state (folded)"

_WS_RE = re.compile(r"\s+")
_ASSIGN_RE = re.compile(r"^\s*([^=]{1,60}?)\s*=\s*(.+)$")


def _one_line(text: str) -> str:
    """Collapse any whitespace run to a single space; strip the ends.

    An entry is one line, so that appending an entry never rewraps the entries
    before it.
    """
    return _WS_RE.sub(" ", str(text or "")).strip()


def _norm(text: str) -> str:
    """Comparison form: one line, lower case, no trailing sentence punctuation.

    Used only to decide whether a line is *already* in the journal.  The stored
    and rendered text keeps its original case and punctuation — this never
    rewrites anything, it just stops the same information from being appended
    twice because an extractor added a full stop.
    """
    return _one_line(text).lower().rstrip(" .;:,")


def _fmt_num(value: Any) -> str:
    """Deterministic, non-invented rendering of a numeric timestamp."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return f"{value:g}" if isinstance(value, float) else str(value)


@dataclass
class Budget:
    """Token budget and its reserves (brief §6).

    ``total=0`` means unbounded: reserves then mean nothing and the budget never
    saturates.  ``allow_auto_consolidate`` is the *only* way a consolidation can
    happen without an explicit ``approved=True``; it defaults to False because a
    silent consolidation would silently destroy the prefix.
    """

    total: int = 0
    reserve_query: int = 512
    reserve_answer: int = 512
    safety: int = 256
    warn_ratio: float = 0.75
    consolidate_ratio: float = 0.90
    allow_auto_consolidate: bool = False

    @property
    def reserves(self) -> int:
        return self.reserve_query + self.reserve_answer + self.safety

    def capacity(self) -> int:
        """Tokens the journal + prefix may use, reserves taken out."""
        if self.total <= 0:
            return 0
        return max(0, self.total - self.reserves)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total, "reserve_query": self.reserve_query,
            "reserve_answer": self.reserve_answer, "safety": self.safety,
            "warn_ratio": self.warn_ratio,
            "consolidate_ratio": self.consolidate_ratio,
            "allow_auto_consolidate": self.allow_auto_consolidate,
            "capacity": self.capacity(),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Budget":
        data = data or {}
        return cls(
            total=int(data.get("total", 0)),
            reserve_query=int(data.get("reserve_query", 512)),
            reserve_answer=int(data.get("reserve_answer", 512)),
            safety=int(data.get("safety", 256)),
            warn_ratio=float(data.get("warn_ratio", 0.75)),
            consolidate_ratio=float(data.get("consolidate_ratio", 0.90)),
            allow_auto_consolidate=bool(data.get("allow_auto_consolidate", False)),
        )


@dataclass
class JournalEntry:
    """One append-only memory event.

    ``status`` is *data*, not text: it is derived by the fold and may change
    (an entry becomes ``superseded`` when a later entry names it), while the
    rendered line stays frozen.  That is the whole point of the layout.
    """

    entry_id: str
    kind: str
    content: str
    source_ids: List[str] = field(default_factory=list)
    created_at: Optional[float] = None
    supersedes: Optional[str] = None
    status: str = STATUS_ACTIVE
    confidence: Optional[float] = None
    subject: Optional[str] = None
    generation: int = 1
    seq: int = 0

    def render_line(self) -> str:
        """The canonical, frozen text of this entry (one line, no clock).

        Deliberately absent from the line: ``status``.  A status is *derived*
        (an entry becomes ``superseded`` when a later entry names it), so
        printing it into a line that can never be rewritten would make the line
        claim something that stops being true.  The status lives in the entry's
        data, and the fold exposes it; the obsolete case is carried by the
        ``obsolete`` type marker itself.
        """
        parts: List[str] = [self.entry_id, f"[{self.kind}]", self.content]
        if self.source_ids:
            parts.append(f"[src:{'|'.join(self.source_ids)}]")
        if self.created_at is not None:
            parts.append(f"[date:{_fmt_num(self.created_at)}]")
        if self.supersedes:
            parts.append(f"(supersedes {self.supersedes})")
        if self.confidence is not None:
            parts.append(f"(conf:{self.confidence:g})")
        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id, "kind": self.kind, "content": self.content,
            "source_ids": list(self.source_ids), "created_at": self.created_at,
            "supersedes": self.supersedes, "status": self.status,
            "confidence": self.confidence, "subject": self.subject,
            "generation": self.generation, "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        return cls(
            entry_id=data["entry_id"], kind=data.get("kind", TYPE_FACT),
            content=data.get("content", ""),
            source_ids=list(data.get("source_ids") or []),
            created_at=data.get("created_at"),
            supersedes=data.get("supersedes"), status=data.get("status", STATUS_ACTIVE),
            confidence=data.get("confidence"), subject=data.get("subject"),
            generation=int(data.get("generation", 1)), seq=int(data.get("seq", 0)),
        )


@dataclass
class CurrentState:
    """The state *computed* from the journal (brief §4.C)."""

    values: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # subject -> (content, entry_id)
    superseded: Dict[str, str] = field(default_factory=dict)          # entry_id -> superseding id
    obsolete: List[str] = field(default_factory=list)

    def current(self, subject: str) -> Optional[Tuple[str, str]]:
        return self.values.get(subject)

    def is_current(self, entry_id: str) -> bool:
        return (entry_id not in self.superseded) and (entry_id not in self.obsolete)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "values": {k: list(v) for k, v in self.values.items()},
            "superseded": dict(self.superseded),
            "obsolete": list(self.obsolete),
        }


@dataclass
class StabilityReport:
    """Byte-level check of the stability contract (brief §5)."""

    prefix_identical: bool = True
    contract_ok: bool = True
    previous_chars: int = 0
    current_chars: int = 0
    common_prefix_chars: int = 0
    appended_chars: int = 0
    divergent_entry: Optional[str] = None
    frozen_lines_stable: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prefix_identical": self.prefix_identical,
            "contract_ok": self.contract_ok,
            "previous_chars": self.previous_chars,
            "current_chars": self.current_chars,
            "common_prefix_chars": self.common_prefix_chars,
            "appended_chars": self.appended_chars,
            "divergent_entry": self.divergent_entry,
            "frozen_lines_stable": self.frozen_lines_stable,
            "note": self.note,
        }


@dataclass
class ConsolidationResult:
    """What a consolidation did — never silent, never described as free."""

    done: bool = False
    reason: str = ""
    generation_from: int = 1
    generation_to: int = 1
    archived_entries: int = 0
    retained_values: int = 0
    entries_dropped: int = 0
    prefix_grew_chars: int = 0
    cache_invalidated: bool = True
    at: Optional[float] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "done": self.done, "reason": self.reason,
            "generation_from": self.generation_from,
            "generation_to": self.generation_to,
            "archived_entries": self.archived_entries,
            "retained_values": self.retained_values,
            "entries_dropped": self.entries_dropped,
            "prefix_grew_chars": self.prefix_grew_chars,
            "cache_invalidated": self.cache_invalidated,
            "at": self.at, "detail": self.detail,
        }


class AppendOnlyMemory:
    """A conversation's append-only memory (see the module docstring)."""

    def __init__(self, *, conversation_id: str = "", budget: Optional[Budget] = None,
                 model: str = "", generation: int = 1) -> None:
        self.conversation_id = conversation_id
        self.budget = budget or Budget()
        self.model = model
        self.generation = int(generation)
        self._prefix_lines: List[str] = []
        self._prefix_frozen = False
        self.journal: List[JournalEntry] = []
        self.archived: List[Dict[str, Any]] = []
        self.consolidations: List[ConsolidationResult] = []
        self._frozen: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}
        self._seq = 0
        self._last_snapshot: Optional[str] = None
        self._appended_since_check = 0

    # ── A. immutable prefix ────────────────────────────────────────────────
    @property
    def prefix_lines(self) -> List[str]:
        return list(self._prefix_lines)

    @property
    def prefix_frozen(self) -> bool:
        return self._prefix_frozen

    def open_prefix(self, *, instructions: Sequence[str] = (),
                    constraints: Sequence[str] = (),
                    reference: Sequence[str] = (),
                    extra: Sequence[str] = ()) -> None:
        """Freeze the immutable prefix. Callable once per session.

        Raises ``RuntimeError`` on a second call: a prefix that can be reopened
        is not immutable, and every later guarantee in this module depends on
        this one.
        """
        if self._prefix_frozen:
            raise RuntimeError("the immutable prefix is already frozen")
        lines: List[str] = []
        for title, values in (("instructions", instructions),
                             ("constraints", constraints),
                             ("reference", reference)):
            kept = [_one_line(v) for v in values if _one_line(v)]
            if kept:
                lines.append(f"{title}:")
                lines.extend(f"- {v}" for v in kept)
        lines.extend(_one_line(v) for v in extra if _one_line(v))
        self._prefix_lines = lines
        self._prefix_frozen = True

    def prefix_text(self) -> str:
        return "\n".join(self._prefix_lines)

    def prefix_bytes(self) -> bytes:
        return self.prefix_text().encode("utf-8")

    # ── B. append-only journal ─────────────────────────────────────────────
    def _next_id(self, kind: str) -> str:
        letter = TYPE_LETTERS.get(kind, "M")
        self._counters[letter] = self._counters.get(letter, 0) + 1
        return f"{letter}{self._counters[letter]:03d}"

    def append(self, kind: str, content: str, *, source_ids: Sequence[str] = (),
               created_at: Optional[float] = None, supersedes: Optional[str] = None,
               status: str = STATUS_ACTIVE, confidence: Optional[float] = None,
               subject: Optional[str] = None) -> JournalEntry:
        """Append one entry. The only way anything enters the memory.

        No past entry is touched: a correction is a *new* entry that names the
        entry it replaces, and the fold derives the rest.
        """
        if kind not in ENTRY_TYPES:
            raise ValueError(f"unknown entry kind: {kind!r}")
        text = _one_line(content)
        if not text:
            raise ValueError("an entry needs content")
        if supersedes and self.lookup(supersedes) is None:
            # A dangling reference would make the journal lie about its history.
            raise ValueError(f"supersedes unknown entry: {supersedes!r}")
        if supersedes and supersedes in self._frozen:
            # The referenced line is already emitted: fine, that is the design.
            pass
        self._seq += 1
        entry = JournalEntry(
            entry_id=self._next_id(kind), kind=kind, content=text,
            source_ids=[_one_line(s) for s in source_ids if _one_line(s)],
            created_at=created_at, supersedes=supersedes, status=status,
            confidence=confidence,
            subject=_one_line(subject) if subject else self.derive_subject(kind, text),
            generation=self.generation, seq=self._seq,
        )
        self.journal.append(entry)
        # Freeze the rendered line now: later appends never reformat it.
        self._frozen[entry.entry_id] = entry.render_line()
        self._appended_since_check += 1
        return entry

    def append_exchange(self, user: str, assistant: str, *,
                        source_ids: Sequence[str] = (),
                        created_at: Optional[float] = None) -> JournalEntry:
        """Append one conversation exchange as a single frozen line.

        Convenient when a caller wants one line per turn.  A caller that wants
        the *question* to sit at the end of the prompt — the layout that makes
        every turn a pure append — uses :meth:`append_question` then
        :meth:`append_answer` instead, so the text is not duplicated.
        """
        return self.append(
            TYPE_EXCHANGE, f"user: {_one_line(user)} | assistant: {_one_line(assistant)}",
            source_ids=source_ids, created_at=created_at,
        )

    def append_question(self, text: str, *, source_ids: Sequence[str] = (),
                        created_at: Optional[float] = None) -> JournalEntry:
        """Append the turn's question, as the journal's last line.

        The question must be *inside* the appended region: a rolling user
        message after the block would disappear from the tail of the next
        prompt, and that removal is an in-place change in the middle of the
        prompt — the one thing the measured engine law punishes.
        """
        return self.append(TYPE_QUESTION, text, source_ids=source_ids,
                           created_at=created_at)

    def append_answer(self, text: str, *, source_ids: Sequence[str] = (),
                      created_at: Optional[float] = None) -> JournalEntry:
        """Append the answer to the previous question (known one turn later)."""
        return self.append(TYPE_ANSWER, text, source_ids=source_ids,
                           created_at=created_at)

    def append_fact(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_FACT, content, **kw)

    def append_correction(self, content: str, *, supersedes: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_CORRECTION, content, supersedes=supersedes, **kw)

    def append_decision(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_DECISION, content, **kw)

    def append_decision_change(self, content: str, *, supersedes: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_DECISION_CHANGE, content, supersedes=supersedes, **kw)

    def append_relation(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_RELATION, content, **kw)

    def append_constraint(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_CONSTRAINT, content, **kw)

    def append_event(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_EVENT, content, **kw)

    def append_resolution(self, content: str, **kw: Any) -> JournalEntry:
        return self.append(TYPE_RESOLUTION, content, **kw)

    def append_obsolete(self, content: str, *, supersedes: Optional[str] = None,
                        **kw: Any) -> JournalEntry:
        return self.append(TYPE_OBSOLETE, content, supersedes=supersedes,
                           status=STATUS_OBSOLETE, **kw)

    # ── subject derivation ────────────────────────────────────────────────
    @staticmethod
    def derive_subject(kind: str, content: str) -> Optional[str]:
        """Best-effort, *reportable* subject for a content line.

        Only two rules, both deterministic and explainable: an explicit
        ``subject`` (handled by the caller) and an ``X = Y`` assignment, whose
        left side is the subject.  Anything else yields ``None``, which simply
        means "no automatic correction detection for this line" — a guess here
        would silently merge unrelated facts, so there is no guess.
        """
        if kind not in FOLDABLE_TYPES:
            return None
        match = _ASSIGN_RE.match(content or "")
        return match.group(1).strip().lower() if match else None

    # ── C. computed current state ──────────────────────────────────────────
    def fold_current(self, *, include_archived: bool = True) -> CurrentState:
        """Fold the journal (and, by default, the archives) into the state.

        Last writer wins per subject; an entry named by a later ``supersedes``
        is superseded; an ``obsolete`` entry marks its target obsolete.
        """
        state = CurrentState()
        entries: List[JournalEntry] = []
        if include_archived:
            for block in self.archived:
                entries.extend(JournalEntry.from_dict(d) for d in block.get("entries", []))
        entries.extend(self.journal)

        for entry in entries:
            if entry.supersedes:
                state.superseded[entry.supersedes] = entry.entry_id
            if entry.kind == TYPE_OBSOLETE and entry.supersedes:
                if entry.supersedes not in state.obsolete:
                    state.obsolete.append(entry.supersedes)
            # An entry *declared* historical when it was appended (the project's
            # own consolidator marks a replaced decision ``superseded``) is never
            # a current value — otherwise re-reading the conversation would put
            # the old value back merely because it was read later.
            if entry.status != STATUS_ACTIVE:
                state.superseded.setdefault(entry.entry_id, "")
                continue
            if entry.subject and entry.kind in FOLDABLE_TYPES:
                state.values[entry.subject] = (entry.content, entry.entry_id)
        # A subject whose last writer was itself superseded by an entry that does
        # not write that subject (an `obsolete` marker, for instance) has no
        # current value any more.  The value is dropped from the *current* view
        # only; the entry, its content and its provenance stay in the journal
        # and in the archives.
        for subject in list(state.values):
            if not state.is_current(state.values[subject][1]):
                del state.values[subject]
        return state

    def current_value(self, subject: str) -> Optional[Tuple[str, str]]:
        """The (content, entry_id) currently in force for ``subject``."""
        state = self.fold_current()
        for name, pair in state.values.items():
            if name.lower() == subject.lower():
                if state.is_current(pair[1]):
                    return pair
                return None
        return None

    def lookup(self, entry_id: str) -> Optional[JournalEntry]:
        """Find an entry by id, live or archived (traceability, brief §4.D)."""
        for entry in self.journal:
            if entry.entry_id == entry_id:
                return entry
        for block in self.archived:
            for data in block.get("entries", []):
                if data.get("entry_id") == entry_id:
                    return JournalEntry.from_dict(data)
        return None

    # ── ingestion of a MemoryView (harness + pipeline use) ─────────────────
    def ingest_memory_view(self, view: Any, *,
                           subject_resolver: Optional[Callable[[str, str], Optional[str]]] = None,
                           source_ids: Sequence[str] = ()) -> List[JournalEntry]:
        """Append everything a :class:`MemoryView` knows that is not known yet.

        Only *new* knowledge is appended — appending a fact the journal already
        carries under the same subject with the same content would grow the
        prompt without informing anyone.  When the subject already has a
        different value, the new line is appended as a **correction** that names
        the entry it replaces, so nothing is rewritten.
        """
        added: List[JournalEntry] = []
        resolver = subject_resolver or (lambda kind, text: self.derive_subject(kind, text))

        def already_in_prefix(text: str) -> bool:
            needle = _one_line(text).lower()
            return any(needle == _one_line(line).lstrip("- ").lower()
                       for line in self._prefix_lines)

        for constraint in getattr(view, "constraints", ()) or ():
            if already_in_prefix(constraint):
                continue
            if self._content_present(constraint):
                continue
            added.append(self.append_constraint(constraint, source_ids=source_ids))

        for fact in getattr(view, "facts", ()) or ():
            subject = resolver(TYPE_FACT, fact)
            entry = self._append_knowledge(TYPE_FACT, TYPE_CORRECTION, fact,
                                           subject=subject, source_ids=source_ids)
            if entry is not None:
                added.append(entry)

        for value, status, decision_id in getattr(view, "decisions", ()) or ():
            subject = resolver(TYPE_DECISION, value)
            entry = self._append_knowledge(TYPE_DECISION, TYPE_DECISION_CHANGE, value,
                                           subject=subject, source_ids=source_ids,
                                           status=status if status in (STATUS_ACTIVE,
                                                                       STATUS_SUPERSEDED,
                                                                       STATUS_OBSOLETE)
                                           else STATUS_ACTIVE,
                                           origin=decision_id)
            if entry is not None:
                added.append(entry)

        for subject_name, predicate, obj in getattr(view, "relations", ()) or ():
            text = f"{_one_line(subject_name)} {_one_line(predicate)} {_one_line(obj)}"
            if self._content_present(text):
                continue
            added.append(self.append_relation(text, source_ids=source_ids))
        return added

    def _content_present(self, content: str) -> bool:
        """Is this exact line already in the journal or any archive?

        Any kind, deliberately: a line the memory already carries must not come
        back as a *correction* just because the extractor re-reads it with the
        same text.  Measured on the fixture: without this, every turn re-asserted
        an older value and the current state flipped back and forth.

        After consolidation the journal is empty but the content lives in the
        archive; checking only the live journal would make every subsequent
        ingestion re-add the whole conversation history.
        """
        needle = _norm(content)
        for entry in self.journal:
            if _norm(entry.content) == needle:
                return True
        for block in self.archived:
            for data in block.get("entries", []):
                if _norm(data.get("content", "")) == needle:
                    return True
        return False

    def _append_knowledge(self, new_kind: str, change_kind: str, content: str, *,
                          subject: Optional[str], source_ids: Sequence[str] = (),
                          status: str = STATUS_ACTIVE,
                          origin: str = "") -> Optional[JournalEntry]:
        """Append new knowledge, or a correction naming whatever it replaces."""
        text = _one_line(content)
        if not text:
            return None
        current = self.current_value(subject) if subject else None
        if current and _norm(current[0]) == _norm(text):
            return None  # already the current value: appending adds tokens only
        if self._content_present(text):
            return None  # the line is already in the journal under some status
        sources = [_one_line(s) for s in source_ids if _one_line(s)]
        if origin:
            sources = sources + [origin]
        if current and status == STATUS_ACTIVE:
            return self.append(change_kind, text, supersedes=current[1],
                               source_ids=sources, status=status, subject=subject)
        # A line the source already declares historical (the project's own
        # consolidator marks a replaced decision ``superseded``) is stored as
        # history: it must NOT be appended as a change, or the journal would read
        # as if the old value replaced the current one.  Measured on the fixture:
        # it produced ``K001 [decision_change] Memcached (supersedes D002)`` — a
        # line that says the opposite of the truth.
        return self.append(new_kind, text, source_ids=sources, status=status,
                           subject=subject)

    # ── rendering: the append-only contract ───────────────────────────────
    def journal_lines(self) -> List[str]:
        """The journal's frozen lines, in emission order.

        Each line is taken from the emission-time freeze, so re-rendering the
        journal is byte-identical to the previous render by construction.
        """
        lines: List[str] = []
        for entry in self.journal:
            frozen = self._frozen.get(entry.entry_id)
            lines.append(frozen if frozen is not None else entry.render_line())
        return lines

    def block_text(self) -> str:
        """Header + immutable prefix + frozen journal. Grows by appending only."""
        parts: List[str] = [BLOCK_HEADER]
        if self._prefix_lines:
            parts.extend(self._prefix_lines)
        parts.append(JOURNAL_SEPARATOR)
        parts.extend(self.journal_lines())
        return "\n".join(parts)

    def render(self, question: str = "") -> str:
        """The block, plus the turn's question at the very end when given."""
        block = self.block_text()
        if question:
            return f"{block}\n--- question ---\n{_one_line(question)}"
        return block

    def snapshot(self) -> str:
        """The block as emitted, remembered for the stability check."""
        text = self.block_text()
        self._last_snapshot = text
        self._appended_since_check = 0
        return text

    def stability_report(self, previous: Optional[str] = None) -> StabilityReport:
        """Compare the current block with the previous emission, byte by byte.

        ``contract_ok`` is the strongest form of the contract: the previous
        block must be a **prefix** of the current one — i.e. every turn is a
        pure append.  Any other relation is reported, not hidden.
        """
        current = self.block_text()
        report = StabilityReport()
        if previous is None:
            previous = self._last_snapshot
        if previous is None:
            report.previous_chars = 0
            report.current_chars = len(current)
            report.common_prefix_chars = 0
            report.appended_chars = len(current)
            report.note = "premiere emission : rien a comparer"
            report.frozen_lines_stable = self._frozen_lines_match()
            self.snapshot()
            return report

        report.previous_chars = len(previous)
        report.current_chars = len(current)
        limit = min(len(previous), len(current))
        common = 0
        while common < limit and previous[common] == current[common]:
            common += 1
        report.common_prefix_chars = common
        report.appended_chars = max(0, len(current) - common)
        report.contract_ok = current.startswith(previous)
        report.prefix_identical = True
        report.frozen_lines_stable = self._frozen_lines_match()
        if not report.contract_ok:
            # Locate the first frozen line whose text moved.
            for entry in self.journal:
                frozen = self._frozen.get(entry.entry_id, "")
                if frozen and entry.entry_id in previous and frozen not in previous:
                    report.divergent_entry = entry.entry_id
                    break
            report.note = "le bloc precedent n'est pas un prefixe du bloc courant"
        else:
            report.note = f"ajout pur de {report.appended_chars} caracteres"
        self.snapshot()
        return report

    def _frozen_lines_match(self) -> bool:
        """Every frozen line must still be exactly what was emitted."""
        for entry in self.journal:
            frozen = self._frozen.get(entry.entry_id)
            if frozen is None:
                return False
            if frozen != JournalEntry(
                entry_id=entry.entry_id, kind=entry.kind, content=entry.content,
                source_ids=entry.source_ids, created_at=entry.created_at,
                supersedes=entry.supersedes, status=entry.status,
                confidence=entry.confidence).render_line():
                return False
        return True

    def verify_frozen(self) -> List[str]:
        """Ids whose frozen line no longer matches their data (must stay empty)."""
        drifted: List[str] = []
        for entry in self.journal:
            fresh = JournalEntry(
                entry_id=entry.entry_id, kind=entry.kind, content=entry.content,
                source_ids=entry.source_ids, created_at=entry.created_at,
                supersedes=entry.supersedes, status=entry.status,
                confidence=entry.confidence).render_line()
            if self._frozen.get(entry.entry_id) != fresh:
                drifted.append(entry.entry_id)
        return drifted

    # ── budget (brief §6) ─────────────────────────────────────────────────
    def count(self, text: str, model: str = "") -> Tuple[int, str]:
        """Token count and how it was obtained: ``real`` or ``estimate``."""
        used_model = model or self.model
        tokenizer = get_tokenizer(used_model)
        kind = "estimate" if type(tokenizer).__name__ == "CharEstimateAdapter" else "real"
        return count_tokens(text, used_model), kind

    def budget_status(self, model: str = "") -> Dict[str, Any]:
        count, kind = self.count(self.block_text(), model)
        capacity = self.budget.capacity()
        if capacity <= 0:
            return {
                "used": count, "kind": kind, "capacity": 0, "ratio": 0.0,
                "state": "unbounded", "needs_consolidation": False,
                "warning": False, "reserves": self.budget.reserves,
            }
        ratio = count / capacity
        needs = ratio >= self.budget.consolidate_ratio
        return {
            "used": count, "kind": kind, "capacity": capacity,
            "ratio": round(ratio, 4), "reserves": self.budget.reserves,
            "state": "saturate" if needs else ("warn" if ratio >= self.budget.warn_ratio else "ok"),
            "needs_consolidation": needs,
            "warning": ratio >= self.budget.warn_ratio,
        }

    def needs_consolidation(self, model: str = "") -> bool:
        return bool(self.budget_status(model)["needs_consolidation"])

    # ── D. controlled consolidation (brief §4.D) ──────────────────────────
    def consolidate(self, *, approved: bool = False, reason: str = "manual",
                    at: Optional[float] = None) -> ConsolidationResult:
        """Start a new generation from the folded state.

        Refuses to run unless ``approved`` is true or the budget explicitly
        allows automatic consolidation.  Never silent, never described as
        cache-preserving: the prefix grows, so the KV cache **is** invalidated,
        and the result says so.
        """
        if not (approved or self.budget.allow_auto_consolidate):
            return ConsolidationResult(
                done=False, reason=reason, generation_from=self.generation,
                generation_to=self.generation, detail=(
                    "consolidation refusee : elle doit etre explicitement autorisee"),
            )
        before = len(self.block_text())
        state = self.fold_current()
        retained = {name: pair for name, pair in state.values.items()
                    if state.is_current(pair[1])}

        lines: List[str] = [f"{SNAPSHOT_HEADER} from generation {self.generation}:"]
        for name in sorted(retained):
            content, entry_id = retained[name]
            chain = [entry_id]
            cursor = state.superseded.get(entry_id)
            while cursor:
                chain.append(cursor)
                cursor = state.superseded.get(cursor)
            origin = self.lookup(entry_id)
            srcs = origin.source_ids if origin else []
            provenance = f" [src:{'|'.join(srcs)}]" if srcs else ""
            lines.append(f"- {name} = {content} (from {entry_id}){provenance}")
        superseded_count = sum(1 for e in self.journal
                               if e.entry_id in state.superseded)
        lines.append(f"- prior entries: {len(self.journal)} archived "
                     f"({superseded_count} superseded, none deleted)")

        self.archived.append({
            "generation": self.generation,
            "entries": [e.to_dict() for e in self.journal],
            "closed_at": at,
            "reason": reason,
        })
        self._prefix_lines = self._prefix_lines + lines
        self.journal = []
        self._frozen = {}
        self.generation += 1
        self._appended_since_check = 0
        after = len(self.block_text())
        result = ConsolidationResult(
            done=True, reason=reason, generation_from=self.generation - 1,
            generation_to=self.generation, archived_entries=len(
                self.archived[-1]["entries"]),
            retained_values=len(retained), entries_dropped=0,
            prefix_grew_chars=after - before, cache_invalidated=True, at=at,
            detail=("le prefixe a change : le KV cache est invalide a partir de "
                    "cette consolidation"),
        )
        self.consolidations.append(result)
        self._last_snapshot = None
        return result

    # ── persistence (brief §9) ────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "conversation_id": self.conversation_id,
            "generation": self.generation,
            "model": self.model,
            "budget": self.budget.to_dict(),
            "prefix_lines": list(self._prefix_lines),
            "prefix_frozen": self._prefix_frozen,
            "journal": [e.to_dict() for e in self.journal],
            "archived": list(self.archived),
            "consolidations": [c.to_dict() for c in self.consolidations],
            "frozen": dict(self._frozen),
            "counters": dict(self._counters),
            "seq": self._seq,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppendOnlyMemory":
        memory = cls(
            conversation_id=data.get("conversation_id", ""),
            budget=Budget.from_dict(data.get("budget")),
            model=data.get("model", ""),
            generation=int(data.get("generation", 1)),
        )
        memory._prefix_lines = list(data.get("prefix_lines") or [])
        memory._prefix_frozen = bool(data.get("prefix_frozen", bool(memory._prefix_lines)))
        memory.journal = [JournalEntry.from_dict(d) for d in data.get("journal", [])]
        memory.archived = list(data.get("archived") or [])
        memory.consolidations = [
            ConsolidationResult(**{k: v for k, v in c.items()
                                   if k in ConsolidationResult.__dataclass_fields__})
            for c in data.get("consolidations", [])
        ]
        memory._frozen = dict(data.get("frozen") or {})
        memory._counters = {str(k): int(v) for k, v in (data.get("counters") or {}).items()}
        memory._seq = int(data.get("seq", len(memory.journal)))
        # A missing freeze must be rebuilt from the data, never left empty:
        # otherwise the next re-render would reformat the past.
        for entry in memory.journal:
            memory._frozen.setdefault(entry.entry_id, entry.render_line())
        return memory

    def save(self, path: Any, *, saved_at: Optional[float] = None) -> bool:
        """Atomic save (temp file then rename), same pattern as the memory layer."""
        target = Path(path)
        payload = self.to_dict()
        payload["saved_at"] = saved_at
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            tmp.replace(target)
            return True
        except Exception:
            if tmp.exists():
                tmp.unlink()
            return False

    @classmethod
    def load(cls, path: Any) -> "AppendOnlyMemory":
        with open(Path(path), "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    # ── misc ──────────────────────────────────────────────────────────────
    def content_hash(self) -> str:
        return hashlib.sha256(self.block_text().encode("utf-8")).hexdigest()[:12]

    def stats(self) -> Dict[str, Any]:
        block = self.block_text()
        count, kind = self.count(block)
        state = self.fold_current()
        return {
            "conversation_id": self.conversation_id,
            "generation": self.generation,
            "entries": len(self.journal),
            "entries_archived": sum(len(b.get("entries", [])) for b in self.archived),
            "consolidations": len([c for c in self.consolidations if c.done]),
            "subjects": len(state.values),
            "superseded": len(state.superseded),
            "prefix_lines": len(self._prefix_lines),
            "journal_chars": sum(len(line) for line in self.journal_lines()),
            "block_chars": len(block),
            "block_tokens": count,
            "token_kind": kind,
            "content_hash": self.content_hash(),
        }
