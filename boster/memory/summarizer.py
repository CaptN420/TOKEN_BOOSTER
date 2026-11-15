"""Conversation summarizer for memory extraction."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .models import MemoryEntry, MemorySummary
from .manager import MemoryManager, classify_entry

logger = logging.getLogger("boster.summarizer")


def extract_summary(
    exchanges: List[Dict[str, str]],
    *,
    max_facts: int = 20,
    max_pending: int = 10,
) -> MemorySummary:
    """Extract structured summary from conversation exchanges.

    Args:
        exchanges: List of {role, content} dicts.
        max_facts: Maximum facts to extract.
        max_pending: Maximum pending items to track.

    Returns:
        MemorySummary with extracted facts, decisions, pending items.
    """
    summary = MemorySummary(category="conversation")
    seen_facts: set = set()
    pending: List[str] = []
    decisions: List[str] = []
    errors: List[str] = []

    for ex in exchanges:
        role = ex.get("role", "unknown")
        content = ex.get("content", ex.get("text", ""))
        if not content:
            continue

        # Extract paths
        path_matches = re.findall(
            r"(?:path|dir|directory|folder|workspace)[:=]\s*([~/][\w./\\-]+)",
            content, re.IGNORECASE,
        )
        for p in path_matches:
            if p not in seen_facts and len(seen_facts) < max_facts:
                summary.key_points.append(f"Path: {p}")
                seen_facts.add(p)

        # Extract decisions
        dec_match = re.search(
            r"(?:decided|chose|selected|opted|settled|confirmed)\s+(?:on\s+)?(.+?)[.!]?$",
            content, re.IGNORECASE,
        )
        if dec_match:
            d = dec_match.group(1).strip().rstrip(".")
            if d not in decisions and len(decisions) < 10:
                decisions.append(d)

        # Extract errors
        err_match = re.search(
            r"(?:error|failed|exception|traceback|crash)\s*:?\\s*(.+?)[.!]?$",
            content, re.IGNORECASE | re.MULTILINE,
        )
        if err_match:
            e = err_match.group(1).strip()
            if e not in errors:
                errors.append(f"Error: {e}")

        # Extract questions (pending)
        if role == "user" and "?" in content:
            q = content.strip()[:80].rstrip("?.") + "?"
            if q not in pending and len(pending) < max_pending:
                pending.append(q)

    summary.key_points = list(seen_facts) + decisions[:5]
    summary.unresolved_questions = pending + errors[:3]
    summary.entries_count = len(exchanges)
    return summary


def summarize_to_memory(
    exchanges: List[Dict[str, str]],
    memory_manager: MemoryManager,
    *,
    source: str = "",
) -> int:
    """Extract key information from exchanges and store in memory.

    Args:
        exchanges: Conversation exchanges.
        memory_manager: Memory manager to store extracted facts.
        source: Source identifier for the memory entries.

    Returns:
        Number of memory entries created.
    """
    summary = extract_summary(exchanges)
    count = 0

    # Store paths as facts
    for point in summary.key_points[:10]:
        if point.startswith("Path:"):
            memory_manager.add(
                content=point,
                category="fact",
                importance=0.7,
                source=source,
            )
            count += 1

    # Store pending items
    for q in summary.unresolved_questions[:5]:
        if q.startswith("?"):
            memory_manager.add(
                content=q[1:].strip(),
                category="task",
                importance=0.5,
                source=source,
            )
            count += 1

    # Store errors as dead ends
    for err in summary.unresolved_questions[5:]:
        if err.startswith("Error:"):
            memory_manager.add(
                content=err,
                category="dead_end",
                importance=0.3,
                source=source,
            )
            count += 1

    logger.info("Summarized %d exchanges → %d memory entries", len(exchanges), count)
    return count


__all__ = ["extract_summary", "summarize_to_memory"]
