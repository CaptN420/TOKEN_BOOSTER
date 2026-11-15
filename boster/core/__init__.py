"""Core engine: tokenizer abstraction, token metrics, budget, policies."""
from .tokenizer import (
    TokenizerAdapter,
    TiktokenAdapter,
    HuggingFaceTokenizerAdapter,
    CharEstimateAdapter,
    get_tokenizer,
    count_tokens,
)
from .token_metrics import (
    TokenMetrics,
    token_density,
    cost_estimate,
    savings_report as metrics_savings_report,
)
from .budget import (
    BudgetConfig,
    SelectionResult,
    estimate_messages_tokens,
    estimate_provider_tokens,
    estimate_tool_tokens,
)
from .policies import (
    PreservationPolicy,
    ProtectedMarker,
    CompressionAudit,
    SafetyCheck,
)

__all__ = [
    "TokenizerAdapter",
    "TiktokenAdapter",
    "HuggingFaceTokenizerAdapter",
    "CharEstimateAdapter",
    "get_tokenizer",
    "count_tokens",
    "TokenMetrics",
    "token_density",
    "cost_estimate",
    "metrics_savings_report",
    "BudgetConfig",
    "SelectionResult",
    "estimate_messages_tokens",
    "estimate_provider_tokens",
    "estimate_tool_tokens",
    "PreservationPolicy",
    "ProtectedMarker",
    "CompressionAudit",
    "SafetyCheck",
]
