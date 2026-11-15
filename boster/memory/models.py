"""Memory data models for the Context Memory Engine."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MemoryEntry:
    """A single piece of stored memory."""
    id: str
    content: str
    category: str  # fact, preference, decision, task, constraint, event, dead_end, resolved, reference
    importance: float = 0.5  # 0.0 to 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed: float = 0.0
    tags: List[str] = field(default_factory=list)
    source: str = ""  # Where this memory came from (session, tool, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        """Update access metadata."""
        self.access_count += 1
        self.last_accessed = time.time()
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "tags": self.tags,
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=data["id"],
            content=data["content"],
            category=data.get("category", "fact"),
            importance=data.get("importance", 0.5),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            access_count=data.get("access_count", 0),
            last_accessed=data.get("last_accessed", 0.0),
            tags=data.get("tags", []),
            source=data.get("source", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class MemorySummary:
    """Compressed summary of a memory category."""
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


@dataclass
class MemoryQueryResult:
    """Result of a memory retrieval query."""
    entries: List[MemoryEntry] = field(default_factory=list)
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
