"""Memory data models for the Context Memory Engine.

Three-tier architecture:
  - RecentContext: raw recent exchanges, ordered, not rewritten
  - EpisodicMemory: structured episodes with provenance refs
  - SemanticMemory: persistent facts, relations, decisions, contradictions

Each element carries provenance (source_ids) so compression never erases
the raw trace that produced it.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class SourceRef:
    """Where a memory item came from."""
    kind: str
    id: str = ""
    index: int = -1
    role: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "index": self.index, "role": self.role}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceRef":
        return cls(
            kind=data.get("kind", "message"),
            id=data.get("id", ""),
            index=data.get("index", -1),
            role=data.get("role", ""),
        )


# Deterministic stand-in for "no time information is available".  Never replace
# this with ``time.time()``: a pipeline that stamps its output with the wall
# clock is not reproducible, and a fabricated date is not knowledge.
UNKNOWN_TIME: float = 0.0


@dataclass
class MemoryItem:
    """Atomic piece of stored memory with provenance."""
    id: str
    content: str
    kind: str = "fact"
    source_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    confidence: float = 0.7
    status: str = "active"
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None

    def touch(self, at: Optional[float] = None) -> None:
        """Refresh the entry.  ``at`` makes the refresh reproducible."""
        self.updated_at = time.time() if at is None else at
        self.confidence = min(1.0, self.confidence + 0.05)

    # ── Backward-compatible surface (pre-v2.1 public contract) ─────────
    # Existing callers (retrieval, CLI, tests) read ``.category`` and
    # ``.importance``.  These map onto the new ``kind``/``confidence``
    # fields so no structure is duplicated and nothing is lost.
    @property
    def category(self) -> str:
        return self.kind

    @category.setter
    def category(self, value: str) -> None:
        self.kind = value

    @property
    def importance(self) -> float:
        return self.confidence

    @importance.setter
    def importance(self, value: float) -> None:
        self.confidence = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "content": self.content, "kind": self.kind,
            "source_ids": self.source_ids, "created_at": self.created_at,
            "updated_at": self.updated_at, "confidence": self.confidence,
            "status": self.status, "subject": self.subject,
            "predicate": self.predicate, "object": self.object,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        return cls(
            id=data["id"], content=data["content"],
            kind=data.get("kind", "fact"),
            source_ids=data.get("source_ids", []),
            created_at=data.get("created_at", UNKNOWN_TIME),
            updated_at=data.get("updated_at", UNKNOWN_TIME),
            confidence=data.get("confidence", 0.7),
            status=data.get("status", "active"),
            subject=data.get("subject"), predicate=data.get("predicate"),
            object=data.get("object"),
        )


@dataclass
class Entity:
    """Named entity mentioned in conversation."""
    name: str
    kind: str = "unknown"
    source_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "source_ids": self.source_ids, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        return cls(name=data["name"], kind=data.get("kind", "unknown"),
                   source_ids=data.get("source_ids", []),
                   created_at=data.get("created_at", UNKNOWN_TIME))


@dataclass
class Relation:
    """Explicit link between two entities."""
    subject: str
    predicate: str
    object: str
    source_id: str = ""
    confidence: float = 0.7
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"subject": self.subject, "predicate": self.predicate,
                "object": self.object, "source_id": self.source_id,
                "confidence": self.confidence, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Relation":
        return cls(subject=data["subject"], predicate=data["predicate"],
                   object=data["object"], source_id=data.get("source_id", ""),
                   confidence=data.get("confidence", 0.7),
                   created_at=data.get("created_at", UNKNOWN_TIME))


@dataclass
class Decision:
    """A recorded decision with alternatives and status.

    ``id`` is a durable, position-independent identifier and ``source_ids``
    carries provenance (the message ids the decision was extracted from).
    Both are appended after the historical fields so existing positional
    callers keep working, and both default to empty for legacy data.
    """
    value: str
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)
    # ``date`` is the date of the *source* decision, never the processing time:
    # it stays None when the message carries no timestamp and no explicit date.
    date: Optional[float] = None
    status: str = "active"
    id: str = ""
    source_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "reason": self.reason,
                "alternatives": self.alternatives, "date": self.date,
                "status": self.status, "id": self.id,
                "source_ids": list(self.source_ids)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Decision":
        return cls(value=data["value"], reason=data.get("reason", ""),
                   alternatives=data.get("alternatives", []),
                   date=data.get("date"),
                   status=data.get("status", "active"),
                   id=data.get("id", ""),
                   source_ids=list(data.get("source_ids") or []))


@dataclass
class Contradiction:
    """Links incompatible elements without deleting either."""
    id: str
    claim_a: str
    claim_b: str
    source_a: str = ""
    source_b: str = ""
    resolved: bool = False
    resolution: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "claim_a": self.claim_a, "claim_b": self.claim_b,
                "source_a": self.source_a, "source_b": self.source_b,
                "resolved": self.resolved, "resolution": self.resolution,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Contradiction":
        return cls(id=data["id"], claim_a=data["claim_a"], claim_b=data["claim_b"],
                   source_a=data.get("source_a", ""), source_b=data.get("source_b", ""),
                   resolved=data.get("resolved", False),
                   resolution=data.get("resolution", ""),
                   created_at=data.get("created_at", UNKNOWN_TIME))


@dataclass
class RecentContext:
    """Raw recent exchanges, ordered, not rewritten."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    max_messages: int = 20

    def add(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def to_dict(self) -> Dict[str, Any]:
        return {"messages": self.messages, "count": len(self.messages)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecentContext":
        rc = cls(max_messages=data.get("max_messages", 20))
        rc.messages = data.get("messages", [])
        return rc


# ── Episode: the structured unit of episodic memory ─────────────────
# An episode holds *references* to durable knowledge (keys into
# SemanticMemory) plus provenance back to the raw messages — never copies
# of the facts themselves.

EPISODE_STATUSES = frozenset({"active", "open", "resolved", "interrupted"})


def _dedupe_preserving_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _coerce_str_list(value: Any, field_name: str) -> List[str]:
    """Validate a list-of-non-empty-strings field, dropping duplicates."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set)):
        raise ValueError(f"Episode.{field_name} must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Episode.{field_name} entries must be non-empty strings")
        out.append(item)
    return _dedupe_preserving_order(out)


def _as_list(value: Any) -> List[Any]:
    """Tolerantly coerce a legacy field to a list (bare value -> [value])."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


@dataclass
class Episode:
    """A coherent, traceable unit of episodic memory.

    Stores references (message ids, entity names, relation/decision keys)
    rather than duplicated knowledge, so it never competes with
    SemanticMemory as a source of truth.
    """
    id: str
    summary: str
    message_ids: List[str] = field(default_factory=list)
    starts_at: Optional[float] = None
    ends_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: Optional[float] = None
    entity_names: List[str] = field(default_factory=list)
    relation_keys: List[str] = field(default_factory=list)
    decision_keys: List[str] = field(default_factory=list)
    status: str = "active"
    confidence: float = 1.0
    linked_episode_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Episode.id must be a non-empty string")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("Episode.summary must be a non-empty string")

        self.message_ids = _coerce_str_list(self.message_ids, "message_ids")
        self.entity_names = _coerce_str_list(self.entity_names, "entity_names")
        self.relation_keys = _coerce_str_list(self.relation_keys, "relation_keys")
        self.decision_keys = _coerce_str_list(self.decision_keys, "decision_keys")
        self.linked_episode_ids = _coerce_str_list(self.linked_episode_ids, "linked_episode_ids")

        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("Episode.confidence must be a number between 0 and 1")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Episode.confidence must be between 0 and 1")
        self.confidence = float(self.confidence)

        for name in ("created_at", "updated_at", "starts_at", "ends_at"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Episode.{name} must be a number or None")
            setattr(self, name, float(value))

        if self.starts_at is not None and self.ends_at is not None and self.starts_at > self.ends_at:
            raise ValueError("Episode.starts_at must be <= ends_at")

        if self.status not in EPISODE_STATUSES:
            raise ValueError(f"Episode.status must be one of {sorted(EPISODE_STATUSES)}")

    def touch(self, at: Optional[float] = None) -> None:
        """Mark the episode as just updated (``at`` keeps it reproducible)."""
        self.updated_at = time.time() if at is None else at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "message_ids": list(self.message_ids),
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entity_names": list(self.entity_names),
            "relation_keys": list(self.relation_keys),
            "decision_keys": list(self.decision_keys),
            "status": self.status,
            "confidence": self.confidence,
            "linked_episode_ids": list(self.linked_episode_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Episode":
        """Rebuild an episode, tolerating legacy free-form episode dicts.

        Legacy episodes (``{"type": ..., "content": ...}``) carry no ``id`` or
        ``summary``; a deterministic id is derived and the summary falls back
        to ``content``/``title``/``text``. Unknown keys are ignored, and no
        optional field is ever required, so historical data is never rejected
        for a missing optional field.
        """
        if not isinstance(data, dict):
            raise ValueError("Episode.from_dict expects a dict")

        summary = (
            data.get("summary")
            or data.get("title")
            or data.get("content")
            or data.get("text")
        )
        ep_id = data.get("id")
        if not ep_id:
            basis = summary if summary else json.dumps(data, sort_keys=True, default=str)
            ep_id = "ep_" + hashlib.md5(str(basis).encode("utf-8")).hexdigest()[:12]
        if not summary:
            summary = f"episode {ep_id}"

        return cls(
            id=ep_id,
            summary=summary,
            message_ids=_as_list(data.get("message_ids")),
            starts_at=data.get("starts_at"),
            ends_at=data.get("ends_at"),
            created_at=data.get("created_at", UNKNOWN_TIME),
            updated_at=data.get("updated_at"),
            entity_names=_as_list(data.get("entity_names")),
            relation_keys=_as_list(data.get("relation_keys")),
            decision_keys=_as_list(data.get("decision_keys")),
            status=data.get("status", "active"),
            confidence=data.get("confidence", 1.0),
            linked_episode_ids=_as_list(data.get("linked_episode_ids")),
        )


@dataclass
class EpisodicMemory:
    """Structured episodes from older exchanges.

    ``episodes`` may hold :class:`Episode` objects and/or legacy free-form
    dicts, and both round-trip. On load, a dict carrying both ``id`` and
    ``summary`` is reconstructed as an :class:`Episode`; any other dict is
    preserved verbatim (legacy compatibility).
    """
    episodes: List[Any] = field(default_factory=list)

    def add_episode(self, episode: Any) -> None:
        self.episodes.append(episode)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episodes": [
                e.to_dict() if isinstance(e, Episode) else e for e in self.episodes
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodicMemory":
        em = cls()
        for entry in data.get("episodes", []):
            if isinstance(entry, Episode):
                em.episodes.append(entry)
            elif isinstance(entry, dict) and "id" in entry and "summary" in entry:
                em.episodes.append(Episode.from_dict(entry))
            else:
                em.episodes.append(entry)
        return em


@dataclass
class SemanticMemory:
    """Persistent facts, relations, decisions, contradictions."""
    items: List[MemoryItem] = field(default_factory=list)
    entities: Dict[str, Entity] = field(default_factory=dict)
    relations: List[Relation] = field(default_factory=list)
    decisions: List[Decision] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)

    def add_item(self, item: MemoryItem) -> None:
        self.items.append(item)

    def upsert_entity(self, entity: Entity) -> None:
        """Insert an entity, merging sources with an existing one.

        Sources are *union*-ed in first-seen order and never replaced, so an
        entity named in several messages keeps every message that mentions it.
        Only the entity's own provenance is merged — nothing is spread onto
        other properties.
        """
        key = entity.name.lower()
        existing = self.entities.get(key)
        if existing is None:
            self.entities[key] = entity
            return
        merged = list(existing.source_ids)
        for sid in entity.source_ids:
            if sid and sid not in merged:
                merged.append(sid)
        existing.source_ids = merged
        if (not existing.kind or existing.kind == "unknown") and entity.kind:
            existing.kind = entity.kind

    def add_relation(self, relation: Relation) -> None:
        self.relations.append(relation)

    def add_decision(self, decision: Decision) -> None:
        self.decisions.append(decision)

    def add_contradiction(self, contradiction: Contradiction) -> None:
        self.contradictions.append(contradiction)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "relations": [r.to_dict() for r in self.relations],
            "decisions": [d.to_dict() for d in self.decisions],
            "contradictions": [c.to_dict() for c in self.contradictions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticMemory":
        sm = cls()
        sm.items = [MemoryItem.from_dict(d) for d in data.get("items", [])]
        sm.entities = {k: Entity.from_dict(v) for k, v in data.get("entities", {}).items()}
        sm.relations = [Relation.from_dict(d) for d in data.get("relations", [])]
        sm.decisions = [Decision.from_dict(d) for d in data.get("decisions", [])]
        sm.contradictions = [Contradiction.from_dict(d) for d in data.get("contradictions", [])]
        return sm


@dataclass
class MemorySummary:
    """Compressed summary of a memory category (pre-v2.1 public contract).

    Restored as a standalone class: the previous ``MemorySummary =
    EpisodicMemory`` alias was behaviourally incompatible (``EpisodicMemory``
    takes no ``category`` argument and has no ``key_points`` /
    ``unresolved_questions``).
    """
    category: str
    entries_count: int = 0
    total_chars: int = 0
    key_points: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)

    def render(self, max_lines: int = 20) -> str:
        parts = [f"## {self.category.title()} ({self.entries_count} entries)"]
        for kp in self.key_points[:max_lines]:
            parts.append(f"  - {kp}")
        if self.unresolved_questions:
            parts.append("### Open Questions")
            for q in self.unresolved_questions:
                parts.append(f"  - ? {q}")
        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "entries_count": self.entries_count,
            "total_chars": self.total_chars,
            "key_points": self.key_points,
            "unresolved_questions": self.unresolved_questions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemorySummary":
        return cls(
            category=data["category"],
            entries_count=data.get("entries_count", 0),
            total_chars=data.get("total_chars", 0),
            key_points=data.get("key_points", []),
            unresolved_questions=data.get("unresolved_questions", []),
        )


@dataclass
class MemoryQueryResult:
    """Result of a memory retrieval query (pre-v2.1 public contract).

    Restored as a standalone class: the previous ``MemoryQueryResult =
    Dict[str, Any]`` alias broke callers that access ``.entries`` (retrieval
    module, old tests).
    """
    entries: List[MemoryItem] = field(default_factory=list)
    summary: Optional[MemorySummary] = None
    query: str = ""
    relevance_threshold: float = 0.3
    total_candidates: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "total_candidates": self.total_candidates,
            "entries": [e.to_dict() for e in self.entries],
            "summary": self.summary.to_dict() if self.summary else None,
        }


# Backward compat: MemoryEntry is genuinely the same atomic unit as
# MemoryItem (``.category``/``.importance`` are provided as properties above),
# so the alias is behaviour-preserving.
MemoryEntry = MemoryItem
