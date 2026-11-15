"""Memory retrieval: semantic and lexical search."""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from ..core.tokenizer import count_tokens
from .models import MemoryEntry, MemoryQueryResult
from .manager import MemoryManager

logger = logging.getLogger("boster.retrieval")


def lexical_search(
    query: str,
    manager: MemoryManager,
    max_results: int = 10,
    threshold: float = 0.1,
) -> MemoryQueryResult:
    """Simple keyword-based memory retrieval."""
    if not query or not manager.count:
        return MemoryQueryResult(query=query)

    query_words = set(query.lower().split())
    scored: List[tuple] = []

    for entry in manager.get_all():
        entry_words = set(re.sub(r"[^\w\s]", " ", entry.content).lower().split())
        overlap = len(query_words & entry_words)
        if overlap == 0:
            continue

        # Score: overlap + recency bonus + importance bonus
        time_bonus = min(1.0, (entry.updated_at - entry.created_at) / 86400)
        score = (overlap / max(len(query_words), 3)) * 0.6 + time_bonus * 0.2 + entry.importance * 0.2
        scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    entries = [e for s, e in scored[:max_results] if s >= threshold]

    return MemoryQueryResult(
        entries=entries,
        query=query,
        total_candidates=len(scored),
    )


def bm25_style_search(
    query: str,
    manager: MemoryManager,
    max_results: int = 10,
) -> MemoryQueryResult:
    """Simple BM25-inspired retrieval (no external dependencies)."""
    if not query or not manager.count:
        return MemoryQueryResult(query=query)

    # Build inverted index
    doc_freq: Dict[str, int] = {}
    doc_terms: Dict[str, List[str]] = {}
    total_docs = manager.count

    for entry in manager.get_all():
        terms = re.findall(r"\w+", entry.content.lower())
        unique_terms = set(terms)
        doc_terms[entry.id] = terms
        for term in unique_terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    # Score query terms
    query_terms = re.findall(r"\w+", query.lower())
    scored: List[tuple] = []

    for entry in manager.get_all():
        terms = doc_terms.get(entry.id, [])
        if not terms:
            continue

        score = 0.0
        for term in query_terms:
            tf = terms.count(term) / max(len(terms), 1)  # term frequency
            df = doc_freq.get(term, 0)
            idf = max(0.0, (total_docs - df + 0.5) / (df + 0.5))  # inverse document frequency
            score += tf * idf

        # Boost by importance and recency
        time_age = max(0, 1.0 - (entry.created_at / 86400))  # newer = higher
        score *= (1.0 + entry.importance * 0.5) * (1.0 + time_age * 0.3)

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    entries = [e for _, e in scored[:max_results]]

    return MemoryQueryResult(
        entries=entries,
        query=query,
        total_candidates=len(scored),
    )


def hybrid_search(
    query: str,
    manager: MemoryManager,
    max_results: int = 10,
) -> MemoryQueryResult:
    """Combine lexical and BM25-style search results."""
    lexical = lexical_search(query, manager, max_results=max_results)
    bm25 = bm25_style_search(query, manager, max_results=max_results)

    # Merge results by entry ID, keeping highest score
    merged: Dict[str, MemoryEntry] = {}
    for entry in lexical.entries + bm25.entries:
        if entry.id not in merged:
            merged[entry.id] = entry

    return MemoryQueryResult(
        entries=list(merged.values()),
        query=query,
        total_candidates=len(lexical.entries) + len(bm25.entries),
    )


__all__ = ["lexical_search", "bm25_style_search", "hybrid_search"]
