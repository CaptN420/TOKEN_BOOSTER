"""Boster — deterministic token savers for LLM contexts (stdlib only)."""
from .context_budget import (BudgetConfig, ContextBudget, SelectionResult,
                             estimate_messages_tokens, estimate_provider_tokens)
from .history_compressor import HistorySummary, compress_history, format_compressed_history
from .output_compressor import compress as compress_output, safe_compress, savings_report

__version__ = "0.1.0"
