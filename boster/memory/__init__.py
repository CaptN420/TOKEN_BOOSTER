"""Memory modules."""
from .models import (
    MemoryItem, Entity, Relation, Decision, Contradiction,
    RecentContext, EpisodicMemory, SemanticMemory,
    MemoryEntry, MemorySummary, MemoryQueryResult,
)
from .manager import MemoryManager, classify_entry
from .summarizer import extract_summary, summarize_to_memory
from .consolidation import consolidate_exchanges, consolidate_similar, deduplicate_by_category
from .retrieval import lexical_search, bm25_style_search, hybrid_search
from .persistence import MemoryPersistence

__all__ = [
    "MemoryItem", "Entity", "Relation", "Decision", "Contradiction",
    "RecentContext", "EpisodicMemory", "SemanticMemory",
    "MemoryEntry", "MemorySummary", "MemoryQueryResult",
    "MemoryManager", "classify_entry",
    "extract_summary", "summarize_to_memory",
    "consolidate_exchanges", "consolidate_similar", "deduplicate_by_category",
    "lexical_search", "bm25_style_search", "hybrid_search",
    "MemoryPersistence",
]
