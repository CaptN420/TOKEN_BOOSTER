"""Context Monitor — threshold-based surveillance for compression triggering.

The monitor tracks context size over time and decides WHEN to compress.
It NEVER compresses on its own — it only signals when compression is warranted.

Three modes:
  - Passive: watch only, never compress
  - Threshold: compress when configured thresholds are reached
  - Task Boundary: compress at detected task transitions
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CompressionMode(str, Enum):
    """Compression triggering mode."""
    PASSIVE = "passive"       # Watch only, never compress
    THRESHOLD = "threshold"    # Compress when thresholds reached
    TASK_BOUNDARY = "task_boundary"  # Compress at task transitions


@dataclass
class ThresholdConfig:
    """Threshold configuration for compression triggering.

    All values are percentages (0.0–1.0) of max_context_tokens.
    """
    warning_pct: float = 0.70      # 70% — log warning
    prep_pct: float = 0.80         # 80% — prepare for compression
    compress_pct: float = 0.90     # 90% — trigger compression
    emergency_pct: float = 0.95    # 95% — emergency compression

    @classmethod
    def from_env(cls) -> "ThresholdConfig":
        """Load thresholds from environment variables."""
        import os
        return cls(
            warning_pct=float(os.environ.get("BOSTER_WARN_PCT", "0.70")),
            prep_pct=float(os.environ.get("BOSTER_PREP_PCT", "0.80")),
            compress_pct=float(os.environ.get("BOSTER_COMPRESS_PCT", "0.90")),
            emergency_pct=float(os.environ.get("BOSTER_EMERG_PCT", "0.95")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "warning_pct": self.warning_pct,
            "prep_pct": self.prep_pct,
            "compress_pct": self.compress_pct,
            "emergency_pct": self.emergency_pct,
        }


@dataclass
class ContextState:
    """Current state of the context monitor."""
    tokens_used: int = 0
    tokens_max: int = 32000
    usage_pct: float = 0.0
    last_compression_time: float = 0.0
    compressions_since_last: int = 0
    min_time_between_compress_seconds: float = 30.0  # Prevent rapid compression
    last_compression_age_seconds: float = 0.0  # Computed, not stored

    @property
    def utilization(self) -> float:
        """Current context utilization (0.0–1.0)."""
        if self.tokens_max <= 0:
            return 0.0
        return self.tokens_used / self.tokens_max

    @property
    def remaining_tokens(self) -> int:
        """Tokens remaining before limit."""
        return max(0, self.tokens_max - self.tokens_used)

    def can_compress(self, mode: CompressionMode, threshold_cfg: ThresholdConfig) -> bool:
        """Check if compression is warranted given current state and mode."""
        if mode == CompressionMode.PASSIVE:
            return False

        # Prevent rapid repeated compression
        now = time.time()
        if (now - self.last_compression_time) < self.min_time_between_compress_seconds:
            return False

        if mode == CompressionMode.THRESHOLD:
            return self.utilization >= threshold_cfg.compress_pct

        if mode == CompressionMode.TASK_BOUNDARY:
            # Task boundary is signaled externally; here we just check threshold
            return self.utilization >= threshold_cfg.prep_pct

        return False

    def should_warn(self, threshold_cfg: ThresholdConfig) -> bool:
        """Check if a warning should be logged."""
        return self.utilization >= threshold_cfg.warning_pct

    def should_prep(self, threshold_cfg: ThresholdConfig) -> bool:
        """Check if compression preparation should start."""
        return self.utilization >= threshold_cfg.prep_pct

    def is_emergency(self, threshold_cfg: ThresholdConfig) -> bool:
        """Check if emergency compression is needed."""
        return self.utilization >= threshold_cfg.emergency_pct


@dataclass
class MonitorReport:
    """Report from the context monitor."""
    mode: CompressionMode
    utilization: float
    tokens_used: int
    tokens_max: int
    remaining_tokens: int
    should_compress: bool
    action_required: str  # "none", "warn", "prep", "compress", "emergency"
    last_compression_age_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "utilization": round(self.utilization, 3),
            "tokens_used": self.tokens_used,
            "tokens_max": self.tokens_max,
            "remaining_tokens": self.remaining_tokens,
            "should_compress": self.should_compress,
            "action_required": self.action_required,
            "last_compression_age_seconds": round(self.last_compression_age_seconds, 1),
        }


class ContextMonitor:
    """Monitors context size and decides when compression is warranted.

    The monitor is stateless about the actual content — it only tracks
    token counts and timing. Compression logic lives in ContextCompressor.
    """

    def __init__(
        self,
        max_context_tokens: int = 32000,
        mode: CompressionMode = CompressionMode.THRESHOLD,
        thresholds: Optional[ThresholdConfig] = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.mode = mode
        self.thresholds = thresholds or ThresholdConfig()
        self._state = ContextState(tokens_max=max_context_tokens)
        self._compression_count: int = 0
        self._history: List[Dict[str, Any]] = []

    def update(self, tokens_used: int) -> MonitorReport:
        """Update monitor with current token count.

        Returns a report indicating current state and any action required.
        """
        self._state.tokens_used = tokens_used
        self._state.usage_pct = self._state.utilization

        # Calculate age since last compression (do NOT update last_compression_time here)
        now = time.time()
        self._state.last_compression_age_seconds = (
            now - self._state.last_compression_time
        )

        # Determine action
        action = "none"
        if self._state.is_emergency(self.thresholds):
            action = "emergency"
        elif self._state.should_warn(self.thresholds):
            action = "warn"
        elif self._state.should_prep(self.thresholds):
            action = "prep"

        should_compress = self._state.can_compress(self.mode, self.thresholds)
        # Upgrade action from warn/prep to compress if warranted
        # But never overwrite 'emergency' with 'compress'
        if should_compress and action in ("warn", "prep"):
            action = "compress"

        report = MonitorReport(
            mode=self.mode,
            utilization=self._state.utilization,
            tokens_used=tokens_used,
            tokens_max=self.max_context_tokens,
            remaining_tokens=self._state.remaining_tokens,
            should_compress=should_compress,
            action_required=action,
            last_compression_age_seconds=self._state.last_compression_age_seconds,
        )

        # Record in history
        self._history.append(report.to_dict())
        # Keep last 100 entries
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return report

    def record_compression(self) -> None:
        """Record that a compression event occurred."""
        self._state.last_compression_time = time.time()
        self._compression_count += 1
        self._state.compressions_since_last = 0

    @property
    def state(self) -> ContextState:
        return self._state

    @property
    def compression_count(self) -> int:
        return self._compression_count

    def get_history(self, last_n: int = 10) -> List[Dict[str, Any]]:
        """Get recent monitor history."""
        return self._history[-last_n:]

    def reset(self) -> None:
        """Reset monitor state."""
        self._state = ContextState(tokens_max=self.max_context_tokens)
        self._compression_count = 0
        self._history.clear()


__all__ = [
    "ContextMonitor",
    "CompressionMode",
    "ThresholdConfig",
    "ContextState",
    "MonitorReport",
]
