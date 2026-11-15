"""Cache Awareness — KV cache compatibility layer.

This module provides awareness of KV cache behavior for different
LLM servers (LM Studio, llama.cpp, etc.). It helps Boster make
compression decisions that minimize cache invalidation.

Key principles:
  - Do NOT modify already-processed prefixes
  - Detect when compression will invalidate cache
  - Prefer appending over replacing
  - Report cache impact of compression decisions
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("boster.cache_awareness")


@dataclass
class CacheSegment:
    """A segment of context that may be cached."""
    prefix: str  # The text prefix
    hash: str = ""  # Content hash for change detection
    token_count: int = 0
    is_prefix: bool = False  # Whether this is a prefix (start of context)

    def __post_init__(self):
        if not self.hash:
            self.hash = hashlib.md5(self.prefix.encode()).hexdigest()[:12]


@dataclass
class CacheImpactReport:
    """Report on the cache impact of a proposed compression."""
    original_prefix_hash: str = ""
    compressed_prefix_hash: str = ""
    prefix_changed: bool = False
    cache_lines_invalidated: int = 0
    estimated_recompute_tokens: int = 0
    recommendation: str = ""  # "proceed", "caution", "avoid"
    details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_prefix_hash": self.original_prefix_hash,
            "compressed_prefix_hash": self.compressed_prefix_hash,
            "prefix_changed": self.prefix_changed,
            "cache_lines_invalidated": self.cache_lines_invalidated,
            "estimated_recompute_tokens": self.estimated_recompute_tokens,
            "recommendation": self.recommendation,
            "details": self.details,
        }


class CacheAwareness:
    """Analyzes compression impact on KV cache reuse.

    This class does NOT control the KV cache directly — it analyzes
    what WOULD happen if compression is applied, based on the
    observable behavior of prefix-based caching.

    For servers that expose cache state (e.g., via API), those would
    be integrated as separate adapters. This class works with any
    server by analyzing text changes.
    """

    # Patterns that indicate system/prefix content (should not be compressed)
    PREFIX_PATTERNS = [
        re.compile(r"^SYSTEM\s+INSTRUCTION", re.IGNORECASE),
        re.compile(r"^You are", re.IGNORECASE),
        re.compile(r"^<system>", re.IGNORECASE),
        re.compile(r"^## System", re.IGNORECASE),
        re.compile(r"^Task:", re.IGNORECASE),
        re.compile(r"^Instructions:", re.IGNORECASE),
    ]

    # Patterns that indicate conversation structure
    CONVERSATION_PATTERNS = [
        re.compile(r"^\{.*\"role\":\s*\"(system|user|assistant)\"", re.IGNORECASE),
        re.compile(r"^\[System\]", re.IGNORECASE),
        re.compile(r"^\[User\]", re.IGNORECASE),
        re.compile(r"^\[Assistant\]", re.IGNORECASE),
    ]

    def analyze_prefix_change(
        self,
        original_text: str,
        compressed_text: str,
    ) -> CacheImpactReport:
        """Analyze how compression affects the context prefix.

        The KV cache is most sensitive to changes in the prefix (start)
        of the context. If the prefix changes, cached computations for
        those tokens are lost.

        Args:
            original_text: Context before compression.
            compressed_text: Context after compression.

        Returns:
            CacheImpactReport with analysis and recommendations.
        """
        report = CacheImpactReport()

        # Extract prefixes (first N chars or first lines)
        prefix_len = min(500, len(original_text))
        original_prefix = original_text[:prefix_len]
        compressed_prefix = compressed_text[:prefix_len]

        report.original_prefix_hash = hashlib.md5(original_prefix.encode()).hexdigest()[:12]
        report.compressed_prefix_hash = hashlib.md5(compressed_prefix.encode()).hexdigest()[:12]
        report.prefix_changed = original_prefix != compressed_prefix

        # Count lines changed in prefix
        orig_lines = original_prefix.splitlines()
        comp_lines = compressed_prefix.splitlines()
        report.cache_lines_invalidated = sum(
            1 for i, (ol, cl) in enumerate(zip(orig_lines, comp_lines))
            if ol != cl
        ) + max(0, len(orig_lines) - len(comp_lines))

        # Estimate recompute cost (rough: 1 token per changed line in prefix)
        report.estimated_recompute_tokens = report.cache_lines_invalidated * 10

        # Generate recommendation
        if report.prefix_changed:
            if report.cache_lines_invalidated > 5:
                report.recommendation = "caution"
                report.details.append(
                    f"Prefix changed: {report.cache_lines_invalidated} lines modified"
                )
            else:
                report.recommendation = "proceed"
                report.details.append(
                    f"Minor prefix change: {report.cache_lines_invalidated} lines"
                )
        else:
            report.recommendation = "proceed"
            report.details.append("Prefix unchanged — cache fully reusable")

        return report

    def should_compress_for_cache(
        self,
        context_text: str,
        compression_result: Any,
    ) -> bool:
        """Determine if compression is safe for cache reuse.

        Args:
            context_text: Current context text.
            compression_result: Result from ContextCompressor.

        Returns:
            True if compression is safe, False otherwise.
        """
        if not compression_result:
            return False

        # Check if prefix was preserved
        impact = self.analyze_prefix_change(context_text, compression_result.compressed_text)

        # If prefix changed significantly, be cautious
        if impact.prefix_changed and impact.cache_lines_invalidated > 10:
            logger.warning(
                "Compression would invalidate %d cache lines. Consider lighter compression.",
                impact.cache_lines_invalidated,
            )
            return False

        return True

    def suggest_compression_level(
        self,
        context_text: str,
        current_level: int = 2,
    ) -> int:
        """Suggest a compression level that minimizes cache invalidation.

        Lower levels preserve more of the original text structure,
        which helps maintain prefix stability.
        """
        # Check if system/prefix content exists at start
        has_system_prefix = any(p.search(context_text) for p in self.PREFIX_PATTERNS)

        if has_system_prefix:
            # Preserve prefix by using lower compression
            return max(0, current_level - 1)

        return current_level

    def detect_cache_risk_events(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Detect events that would invalidate KV cache.

        These are structural changes that any compression must avoid
        modifying in the prefix.
        """
        risks = []

        # Check for system prompt changes
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            first_system = system_msgs[0].get("content", "")
            if len(first_system) > 1000:
                risks.append(f"Large system prompt ({len(first_system)} chars)")

        # Check for tool schema changes
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if len(tool_msgs) > 5:
            risks.append(f"Many tool messages ({len(tool_msgs)}) — potential schema drift")

        # Check for abrupt context shifts
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 10000:
                risks.append(f"Message {i} has very long content ({len(content)} chars)")

        return risks


__all__ = [
    "CacheAwareness",
    "CacheSegment",
    "CacheImpactReport",
]
