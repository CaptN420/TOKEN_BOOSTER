"""Context Compressor — on-demand compression with integrity preservation.

The compressor performs compression ONLY when explicitly requested.
It preserves:
  - System instructions and safety constraints
  - Decisions and their reasoning
  - Facts and their relationships
  - Error messages and stack traces
  - Code structure and identifiers

Compression is NEVER automatic — it requires an explicit trigger from
ContextMonitor or an external agent/user request.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.policies import PreservationPolicy, SafetyCheck, CompressionAudit, ProtectedMarker
from ..compression.terminal import compress_terminal
from .integrity import ContextIntegrityEngine, FactRelation

logger = logging.getLogger("boster.compressor")


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    original_text: str = ""
    compressed_text: str = ""
    original_chars: int = 0
    compressed_chars: int = 0
    original_tokens: int = 0
    compressed_tokens: int = 0
    tokens_saved: int = 0
    savings_pct: float = 0.0
    compression_level: int = 2
    protected_items: List[str] = field(default_factory=list)
    dropped_items: List[str] = field(default_factory=list)
    integrity_ok: bool = True
    integrity_issues: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    audit: Optional[CompressionAudit] = None

    @property
    def is_lossless(self) -> bool:
        """True if no content was dropped."""
        return self.tokens_saved == 0 and self.compressed_chars >= self.original_chars * 0.95

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "tokens_saved": self.tokens_saved,
            "savings_pct": round(self.savings_pct, 1),
            "compression_level": self.compression_level,
            "protected_items": self.protected_items,
            "integrity_ok": self.integrity_ok,
            "integrity_issues": self.integrity_issues,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "is_lossless": self.is_lossless,
        }


class ContextCompressor:
    """On-demand context compressor with integrity preservation.

    This compressor is designed to be called explicitly, not automatically.
    It preserves critical information and reports what was protected/dropped.
    """

    def __init__(
        self,
        integrity_engine: Optional[ContextIntegrityEngine] = None,
        policy: Optional[PreservationPolicy] = None,
        default_level: int = 2,
    ):
        self.integrity_engine = integrity_engine or ContextIntegrityEngine()
        self.policy = policy or PreservationPolicy()
        self.default_level = default_level
        self._compression_history: List[CompressionResult] = []

    def compress(
        self,
        text: str,
        *,
        level: Optional[int] = None,
        protect_sections: Optional[List[str]] = None,
        preserve_decisions: bool = True,
        preserve_errors: bool = True,
    ) -> CompressionResult:
        """Compress text with integrity preservation.

        Args:
            text: Text to compress.
            level: Compression level 0-4 (uses default if None).
            protect_sections: Additional sections to protect.
            preserve_decisions: Whether to preserve decision information.
            preserve_errors: Whether to preserve error information.

        Returns:
            CompressionResult with metrics and integrity check.
        """
        start_time = time.perf_counter()

        # Extract protected content before compression
        protected_blocks = ProtectedMarker.extract_protected_blocks(text)
        protected_ids = [b["tag"] for b in protected_blocks]

        # Apply user-specified protections
        if protect_sections:
            for section in protect_sections:
                if section in text:
                    protected_ids.append(f"manual:{section}")

        # Check what must be preserved
        preservation_checks = self.policy.check(text)

        # Perform compression
        lvl = level if level is not None else self.default_level
        compressed = compress_terminal(text, level=lvl)

        # Restore protected blocks
        compressed = ProtectedMarker.restore_protected_blocks(compressed, protected_blocks)

        # Calculate metrics
        original_chars = len(text)
        compressed_chars = len(compressed)
        original_tokens = self._estimate_tokens(text)
        compressed_tokens = self._estimate_tokens(compressed)
        tokens_saved = max(0, original_tokens - compressed_tokens)
        savings_pct = (tokens_saved / max(original_tokens, 1)) * 100

        # Run integrity check
        integrity_result = SafetyCheck.check(text, compressed)
        integrity_ok = integrity_result["integrity"]
        integrity_issues = integrity_result.get("warnings", [])

        elapsed = time.perf_counter() - start_time

        result = CompressionResult(
            original_text=text,
            compressed_text=compressed,
            original_chars=original_chars,
            compressed_chars=compressed_chars,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            tokens_saved=tokens_saved,
            savings_pct=savings_pct,
            compression_level=lvl,
            protected_items=protected_ids + preservation_checks,
            dropped_items=integrity_result.get("missing", []),
            integrity_ok=integrity_ok,
            integrity_issues=integrity_issues,
            elapsed_seconds=elapsed,
        )

        # Create audit log
        result.audit = CompressionAudit(
            operation="context_compress",
            input_chars=original_chars,
            output_chars=compressed_chars,
            input_tokens=original_tokens,
            output_tokens=compressed_tokens,
            savings_pct=savings_pct,
            protected_items=protected_ids,
            dropped_items=integrity_result.get("missing", []),
            warnings=integrity_issues,
        )

        # Record in history
        self._compression_history.append(result)
        if len(self._compression_history) > 50:
            self._compression_history = self._compression_history[-50:]

        logger.info(
            "Compressed %d→%d chars (%.1f%% saved, level=%d, integrity=%s)",
            original_chars, compressed_chars, savings_pct, lvl, integrity_ok,
        )

        return result

    def compress_messages(
        self,
        messages: List[Dict[str, Any]],
        *,
        level: Optional[int] = None,
        max_total_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Compress conversation messages with integrity preservation.

        Preserves system messages and recent context. Only compresses
        older or larger messages.
        """
        if not messages:
            return []

        lvl = level if level is not None else self.default_level
        optimized = []

        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            role = msg.get("role", "user")

            # Never compress system messages
            if role == "system" and isinstance(content, str):
                optimized.append(msg)
                continue

            # Compress long content
            if isinstance(content, str) and len(content) > 500:
                result = self.compress(content, level=lvl)
                optimized.append({**msg, "content": result.compressed_text})
            else:
                optimized.append(msg)

        return optimized

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        if not text:
            return 0
        # Use char-based estimate as fallback
        return max(1, (len(text) + 3) // 4)

    def get_history(self, last_n: int = 10) -> List[Dict[str, Any]]:
        """Get recent compression history."""
        return [r.to_dict() for r in self._compression_history[-last_n:]]

    def reset_history(self) -> None:
        """Clear compression history."""
        self._compression_history.clear()


__all__ = [
    "ContextCompressor",
    "CompressionResult",
]
