"""Memory modules."""
from .models import MemoryEntry, MemorySummary, MemoryQueryResult
from .manager import MemoryManager, classify_entry
from .summarizer import extract_summary, summarize_to_memory
from .consolidation import consolidate_similar, deduplicate_by_category
from .retrieval import lexical_search, bm25_style_search, hybrid_search
from .persistence import MemoryPersistence

__all__ = [
    "MemoryEntry",
    "MemorySummary",
    "MemoryQueryResult",
    "MemoryManager",
    "classify_entry",
    "extract_summary",
    "summarize_to_memory",
    "consolidate_similar",
    "deduplicate_by_category",
    "lexical_search",
    "bm25_style_search",
    "hybrid_search",
    "MemoryPersistence",
]
