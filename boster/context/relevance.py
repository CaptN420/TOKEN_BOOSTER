"""Relevance scoring for context selection."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("boster.relevance")


def score_message(
    message: Dict[str, Any],
    user_query: str = "",
    recency_weight: float = 0.6,
    relevance_weight: float = 0.4,
) -> float:
    """Score a message by recency + relevance to query.

    Args:
        message: OpenAI-format message dict.
        user_query: Current user query for relevance matching.
        recency_weight: Weight for position-based score (0-1).
        relevance_weight: Weight for query overlap score (0-1).

    Returns:
        Score between 0 and 1 (higher = more relevant).
    """
    idx = message.get("_idx", 0)
    role = message.get("role", "user")
    content = message.get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))

    # Recency score (later messages get higher score)
    recency = idx / max(idx + 1, 1)  # Normalize to 0-1

    # Relevance score (keyword overlap with query)
    relevance = 0.0
    if user_query and content:
        query_words = set(user_query.lower().split())
        content_words = set(re.sub(r"[^\w\s]", " ", content).lower().split())
        overlap = len(query_words & content_words)
        if overlap > 0:
            relevance = min(1.0, overlap / max(len(query_words), 3))

    # Role-based boost
    role_boost = 0.0
    if role == "system":
        role_boost = 1.0  # Always highest priority
    elif role == "user" and idx > 0:
        role_boost = 0.5  # User messages get a boost

    # Combine scores
    score = (recency * recency_weight) + (relevance * relevance_weight) + role_boost
    return min(1.0, max(0.0, score))


def score_text_fragment(
    text: str,
    query: str = "",
) -> float:
    """Score a text fragment by relevance to query."""
    if not query or not text:
        return 0.5  # Neutral score

    query_words = set(query.lower().split())
    text_words = set(re.sub(r"[^\w\s]", " ", text).lower().split())
    overlap = len(query_words & text_words)

    if overlap == 0:
        return 0.1
    return min(1.0, overlap / max(len(query_words), 3))


def rank_messages(
    messages: List[Dict[str, Any]],
    user_query: str = "",
) -> List[Tuple[float, int, Dict[str, Any]]]:
    """Rank messages by relevance score.

    Returns list of (score, original_index, message) tuples, sorted descending.
    """
    scored = []
    for i, msg in enumerate(messages):
        score = score_message(msg, user_query, recency_weight=0.6, relevance_weight=0.4)
        scored.append((score, i, msg))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return scored


__all__ = ["score_message", "score_text_fragment", "rank_messages"]
