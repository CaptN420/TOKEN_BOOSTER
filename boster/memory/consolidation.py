"""Memory consolidation: merge related entries, remove duplicates."""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from .models import MemoryEntry
from .manager import MemoryManager

logger = logging.getLogger("boster.consolidation")


def consolidate_similar(
    entries: List[MemoryEntry],
    similarity_threshold: float = 0.8,
) -> List[MemoryEntry]:
    """Merge similar memory entries, keeping the most important one.

    Args:
        entries: List of memory entries to consolidate.
        similarity_threshold: Jaccard similarity threshold for merging.

    Returns:
        Consolidated list of entries.
    """
    if len(entries) <= 1:
        return entries

    result: List[MemoryEntry] = []
    used: set = set()

    for i, entry_a in enumerate(entries):
        if entry_a.id in used:
            continue
        merged = entry_a
        for j, entry_b in enumerate(entries[i + 1:], i + 1):
            if entry_b.id in used:
                continue
            if _similarity(entry_a.content, entry_b.content) >= similarity_threshold:
                # Merge: keep higher importance, combine tags
                if entry_b.importance > merged.importance:
                    merged = MemoryEntry(
                        id=merged.id,
                        content=merged.content,
                        category=merged.category,
                        importance=max(merged.importance, entry_b.importance),
                        tags=list(set(merged.tags + entry_b.tags)),
                        source=f"{merged.source},{entry_b.source}" if merged.source else entry_b.source,
                    )
                used.add(entry_b.id)
        used.add(entry_a.id)
        result.append(merged)

    return result


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between two text strings."""
    words_a = set(re.sub(r"[^\w\s]", " ", a).lower().split())
    words_b = set(re.sub(r"[^\w\s]", " ", b).lower().split())
    if not words_a and not words_b:
        return 1.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / max(len(union), 1)


def deduplicate_by_category(
    manager: MemoryManager,
    category: str,
) -> int:
    """Deduplicate entries within a category. Returns count of removed entries."""
    entries = manager.get_all(category=category)
    if len(entries) <= 1:
        return 0

    original_count = len(entries)
    consolidated = consolidate_similar(entries)

    # Remove old entries and add consolidated ones
    for entry in entries:
        del manager._entries[entry.id]

    for entry in consolidated:
        manager._entries[entry.id] = entry

    return original_count - len(consolidated)


__all__ = ["consolidate_similar", "deduplicate_by_category"]
