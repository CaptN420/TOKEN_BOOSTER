"""Boster — Universal Token Optimization Engine v1.0."""
from .core.engine import Boster, OptimizeResult
from .core.tokenizer import count_tokens, get_tokenizer
from .core.token_metrics import cost_estimate, token_density
from .output_compressor import savings_report as savings_report
from .core.optimizer import optimize_prompt
from .core.policies import PreservationPolicy, SafetyCheck, CompressionAudit, ProtectedMarker
from .core.budget import BudgetConfig, SelectionResult, estimate_messages_tokens, estimate_provider_tokens, estimate_tool_tokens
from .adaptive import adaptive_compress
from .compression.terminal import compress_terminal
from .compression.json import compress_json
from .compression.code import compress_code
from .compression.logs import compress_logs
from .compression.markdown import compress_markdown
from .compression.structured import compress_csv, compress_sql, compress_diff, compress_git_log
from .output_compressor import compress as compress_output, safe_compress
from .history_compressor import HistorySummary, compress_history, format_compressed_history
from .context_budget import BudgetConfig as OldBudgetConfig, ContextBudget, SelectionResult as OldSelectionResult, estimate_messages_tokens as old_estimate_messages_tokens, estimate_provider_tokens as old_estimate_provider_tokens
from .pipeline import PipelineResult, run_pipeline
from .memory.manager import MemoryManager
from .memory.models import MemoryEntry, MemorySummary, MemoryQueryResult
from .memory.summarizer import extract_summary, summarize_to_memory
from .memory.retrieval import hybrid_search, lexical_search, bm25_style_search
from .memory.persistence import MemoryPersistence
from .context.assembler import ContextAssembler, AssembledContext
from .context.allocator import TokenAllocator, AllocationPlan
from .context.relevance import score_message, rank_messages
from .context.deduplication import deduplicate_messages
from .adapters import ProviderAdapter, OpenAIAdapter, AnthropicAdapter, OllamaAdapter, get_adapter

# v2.0 Context Intelligence
from .intelligence.monitor import ContextMonitor, CompressionMode, ThresholdConfig
from .intelligence.compressor import ContextCompressor, CompressionResult
from .intelligence.integrity import ContextIntegrityEngine, FactRelation
from .intelligence.snapshot import Snapshot as ContextSnapshot, SnapshotManager
from .intelligence.cache_awareness import CacheAwareness, CacheImpactReport
from .adapters.kv_cache import KVCacheAdapter, CacheStats

__version__ = "1.0.0"

__all__ = [
    # Main engine
    "Boster",
    "OptimizeResult",
    # Tokenizer
    "count_tokens",
    "get_tokenizer",
    # Metrics
    "cost_estimate",
    "token_density",
    "savings_report",
    # Optimizer
    "optimize_prompt",
    # Policies
    "PreservationPolicy",
    "SafetyCheck",
    "CompressionAudit",
    "ProtectedMarker",
    # Budget
    "BudgetConfig",
    "SelectionResult",
    "estimate_messages_tokens",
    "estimate_provider_tokens",
    "estimate_tool_tokens",
    # Compression
    "adaptive_compress",
    "compress_terminal",
    "compress_json",
    "compress_code",
    "compress_logs",
    "compress_markdown",
    "compress_csv",
    "compress_sql",
    "compress_diff",
    "compress_git_log",
    # Legacy (backwards compat)
    "compress_output",
    "safe_compress",
    "HistorySummary",
    "compress_history",
    "format_compressed_history",
    "OldBudgetConfig",
    "ContextBudget",
    "OldSelectionResult",
    "old_estimate_messages_tokens",
    "old_estimate_provider_tokens",
    "PipelineResult",
    "run_pipeline",
    # Memory
    "MemoryManager",
    "MemoryEntry",
    "MemorySummary",
    "MemoryQueryResult",
    "extract_summary",
    "summarize_to_memory",
    "hybrid_search",
    "lexical_search",
    "bm25_style_search",
    "MemoryPersistence",
    # Context
    "ContextAssembler",
    "AssembledContext",
    "TokenAllocator",
    "AllocationPlan",
    "score_message",
    "rank_messages",
    "deduplicate_messages",
    # Adapters
    "ProviderAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "OllamaAdapter",
    "get_adapter",
]
