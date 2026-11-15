"""Memory data models for the Context Memory Engine.

Three-tier architecture:
  - RecentContext: raw recent exchanges, ordered, not rewritten
  - EpisodicMemory: structured episodes with provenance refs
  - SemanticMemory: persistent facts, relations, decisions, contradictions

Each element carries provenance (source_ids) so compression never erases
the raw trace that produced it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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

    def touch(self) -> None:
        self.updated_at = time.time()
        self.confidence = min(1.0, self.confidence + 0.05)

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
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
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
                   created_at=data.get("created_at", time.time()))


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
                   created_at=data.get("created_at", time.time()))


@dataclass
class Decision:
    """A recorded decision with alternatives and status."""
    value: str
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)
    date: float = field(default_factory=time.time)
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "reason": self.reason,
                "alternatives": self.alternatives, "date": self.date,
                "status": self.status}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Decision":
        return cls(value=data["value"], reason=data.get("reason", ""),
                   alternatives=data.get("alternatives", []),
                   date=data.get("date", time.time()),
                   status=data.get("status", "active"))


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
                   created_at=data.get("created_at", time.time()))


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


@dataclass
class EpisodicMemory:
    """Structured episodes from older exchanges."""
    episodes: List[Dict[str, Any]] = field(default_factory=list)

    def add_episode(self, episode: Dict[str, Any]) -> None:
        self.episodes.append(episode)

    def to_dict(self) -> Dict[str, Any]:
        return {"episodes": self.episodes}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodicMemory":
        em = cls()
        em.episodes = data.get("episodes", [])
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
        self.entities[entity.name.lower()] = entity

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


# Backward compat
MemoryEntry = MemoryItem
MemorySummary = EpisodicMemory
MemoryQueryResult = Dict[str, Any]
