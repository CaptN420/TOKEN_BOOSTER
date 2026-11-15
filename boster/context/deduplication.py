"""Context deduplication: remove duplicate or near-duplicate content."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Set

logger = logging.getLogger("boster.dedup")


def hash_content(content: str) -> str:
    """Create a short hash of content for deduplication."""
    # Normalize whitespace for comparison
    normalized = re.sub(r"\s+", " ", content.strip()).lower()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def deduplicate_messages(
    messages: List[Dict[str, Any]],
    strategy: str = "exact",
) -> List[Dict[str, Any]]:
    """Remove duplicate messages from conversation history.

    Args:
        messages: List of OpenAI-format messages.
        strategy: "exact" (hash match) or "fuzzy" (similar content).

    Returns:
        Deduplicated message list preserving order.
    """
    seen: Set[str] = set()
    result: List[Dict[str, Any]] = []

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))

        h = hash_content(str(content))

        if strategy == "exact":
            if h not in seen:
                seen.add(h)
                result.append(msg)
        elif strategy == "fuzzy":
            # For fuzzy dedup, also check for high similarity
            is_dup = False
            for existing in result:
                ex_content = existing.get("content", "")
                if isinstance(ex_content, list):
                    ex_content = " ".join(p.get("text", "") for p in ex_content if isinstance(p, dict))
                if _similarity(content, ex_content) > 0.9:
                    is_dup = True
                    break
            if not is_dup:
                seen.add(h)
                result.append(msg)

    return result


def deduplicate_text_fragments(text: str, min_length: int = 50) -> str:
    """Remove duplicate paragraphs from text."""
    lines = text.split("\n")
    seen_paragraphs: Dict[str, str] = {}  # hash -> first occurrence
    result_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if len(stripped) < min_length:
            result_lines.append(line)
            continue

        h = hash_content(stripped)
        if h not in seen_paragraphs:
            seen_paragraphs[h] = stripped
            result_lines.append(line)
        else:
            logger.debug("Dedup: removed duplicate paragraph (%d chars)", len(stripped))

    return "\n".join(result_lines)


def _similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / max(len(union), 1)


__all__ = ["deduplicate_messages", "deduplicate_text_fragments", "hash_content"]
