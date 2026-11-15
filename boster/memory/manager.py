"""Memory manager: CRUD operations, relevance scoring, consolidation."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional

from .models import MemoryEntry, MemoryQueryResult, MemorySummary

logger = logging.getLogger("boster.memory")

# ── Category keywords for classification ────────────────────────────

CATEGORY_PATTERNS: Dict[str, List[re.Pattern]] = {
    "fact": [
        re.compile(r"\b(path|dir|directory|folder|workspace|file)\s*[:=]", re.IGNORECASE),
        re.compile(r"\bis\s+(a|the)\s+\w+", re.IGNORECASE),
    ],
    "preference": [
        re.compile(r"\b(prefer|like|dislike|want|need|love|hate)\s", re.IGNORECASE),
        re.compile(r"\b(i\s+always|i\s+never|i\s+usually)\s", re.IGNORECASE),
    ],
    "decision": [
        re.compile(r"\b(decided|chose|selected|opted|settled|confirmed)\s+", re.IGNORECASE),
        re.compile(r"\b(reason|because|therefore|thus)\s+", re.IGNORECASE),
    ],
    "task": [
        re.compile(r"\b(todo|task|action|work on|implement|build|fix)\s+", re.IGNORECASE),
        re.compile(r"\bdone\b|\bcomplete\b|\bfinished\b", re.IGNORECASE),
    ],
    "constraint": [
        re.compile(r"\b(constraint|limitation|requirement|must|cannot|should not)\s+", re.IGNORECASE),
        re.compile(r"\b(warning|caution|note:?)\s+", re.IGNORECASE),
    ],
    "event": [
        re.compile(r"\b(happened|occurred|event|incident|release|deploy)\s+", re.IGNORECASE),
    ],
    "dead_end": [
        re.compile(r"\b(dead\s*end|abandoned|didn't work|failed|gave\s*up)\s+", re.IGNORECASE),
    ],
    "resolved": [
        re.compile(r"\b(resolved|fixed|solved|completed)\s+", re.IGNORECASE),
    ],
    "reference": [
        re.compile(r"\b(see|refer|link|url|http)\s+", re.IGNORECASE),
    ],
}


def classify_entry(text: str) -> str:
    """Classify text into a memory category."""
    scores: Dict[str, float] = {}
    for category, patterns in CATEGORY_PATTERNS.items():
        score = sum(1 for p in patterns if p.search(text))
        if score > 0:
            scores[category] = score

    if not scores:
        return "fact"
    return max(scores, key=scores.get)


def generate_id(content: str, prefix: str = "mem") -> str:
    """Generate a unique ID for a memory entry."""
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    return f"{prefix}_{h}"


class MemoryManager:
    """Manages persistent structured memory."""

    def __init__(self, max_entries: int = 500, expiration_hours: float = 720.0):
        self._entries: Dict[str, MemoryEntry] = {}
        self.max_entries = max_entries
        self.expiration_hours = expiration_hours

    @property
    def count(self) -> int:
        return len(self._entries)

    def add(
        self,
        content: str,
        category: str = "",
        importance: float = 0.5,
        tags: Optional[List[str]] = None,
        source: str = "",
    ) -> MemoryEntry:
        """Add a memory entry."""
        if not content.strip():
            raise ValueError("Memory content cannot be empty")

        cat = category or classify_entry(content)
        entry_id = generate_id(content)

        # Check for existing entry (update if found)
        if entry_id in self._entries:
            entry = self._entries[entry_id]
            entry.content = content
            entry.importance = max(entry.importance, importance)
            entry.updated_at = time.time()
            if tags:
                entry.tags = list(set(entry.tags + tags))
            entry.touch()
            return entry

        # Evict oldest low-importance entries if at capacity
        if len(self._entries) >= self.max_entries:
            self._evict_lowest_importance(1)

        entry = MemoryEntry(
            id=entry_id,
            content=content.strip(),
            category=cat,
            importance=importance,
            tags=tags or [],
            source=source,
        )
        self._entries[entry_id] = entry
        logger.debug("Added memory entry: %s (%s)", entry_id, cat)
        return entry

    def retrieve(
        self,
        query: str,
        max_results: int = 10,
        relevance_threshold: float = 0.3,
    ) -> MemoryQueryResult:
        """Retrieve relevant memory entries."""
        if not query or not self._entries:
            return MemoryQueryResult(query=query)

        query_words = set(query.lower().split())
        scored: List[tuple] = []

        for entry in self._entries.values():
            entry.touch()
            entry_words = set(re.sub(r"[^\w\s]", " ", entry.content).lower().split())
            overlap = len(query_words & entry_words)
            if overlap == 0:
                continue

            # Relevance score: overlap + recency + importance
            time_score = min(1.0, (time.time() - entry.created_at) / 86400)  # newer = higher
            relevance = (overlap / max(len(query_words), 3)) * 0.5
            recency = time_score * 0.2
            importance = entry.importance * 0.3
            score = relevance + recency + importance

            if score >= relevance_threshold:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        entries = [e for _, e in scored[:max_results]]

        # Build summary by category
        summaries: Dict[str, MemorySummary] = {}
        for entry in entries:
            if entry.category not in summaries:
                summaries[entry.category] = MemorySummary(category=entry.category)
            summaries[entry.category].entries_count += 1
            summaries[entry.category].total_chars += len(entry.content)
            if len(summaries[entry.category].key_points) < 5:
                summaries[entry.category].key_points.append(entry.content[:80])

        return MemoryQueryResult(
            entries=entries,
            query=query,
            relevance_threshold=relevance_threshold,
            total_candidates=len(scored),
        )

    def consolidate(self) -> int:
        """Consolidate duplicate or related entries. Returns count of merged entries."""
        merged = 0
        by_category: Dict[str, List[MemoryEntry]] = {}

        for entry in self._entries.values():
            by_category.setdefault(entry.category, []).append(entry)

        for category, entries in by_category.items():
            if len(entries) <= 1:
                continue
            # Sort by importance descending
            entries.sort(key=lambda e: -e.importance)
            # Keep top entry, mark others as consolidated
            for entry in entries[1:]:
                logger.debug("Consolidating low-importance entry: %s", entry.id)
                del self._entries[entry.id]
                merged += 1

        return merged

    def expire(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        now = time.time()
        cutoff = now - (self.expiration_hours * 3600)
        to_remove = [eid for eid, e in self._entries.items() if e.created_at < cutoff]
        for eid in to_remove:
            del self._entries[eid]
        return len(to_remove)

    def get_all(self, category: Optional[str] = None) -> List[MemoryEntry]:
        """Get all entries, optionally filtered by category."""
        if category:
            return [e for e in self._entries.values() if e.category == category]
        return list(self._entries.values())

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all memory."""
        cats: Dict[str, int] = {}
        for entry in self._entries.values():
            cats[entry.category] = cats.get(entry.category, 0) + 1
        return {
            "total_entries": len(self._entries),
            "by_category": cats,
            "avg_importance": (
                sum(e.importance for e in self._entries.values()) / max(len(self._entries), 1)
            ),
        }

    def _evict_lowest_importance(self, count: int = 10) -> None:
        """Remove lowest-importance entries to make room."""
        sorted_entries = sorted(
            self._entries.items(),
            key=lambda x: x[1].importance,
        )
        for eid, _ in sorted_entries[:count]:
            del self._entries[eid]

    def clear(self) -> None:
        """Remove all memory."""
        self._entries.clear()


__all__ = ["MemoryManager", "MemoryEntry", "MemorySummary", "MemoryQueryResult", "classify_entry"]
