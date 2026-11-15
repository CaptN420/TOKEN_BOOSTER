"""Safety and information integrity policies.

Boster must never silently remove critical information. This module provides:
  - Protected content markers (sections that must not be compressed)
  - Preservation policies (what to keep vs what to drop)
  - Compression audit logs (track what was changed and why)
  - Safety checks (validate output integrity)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ── Protected content markers ────────────────────────────────────────

class ProtectedMarker:
    """Marks sections of text that must not be modified during compression."""

    # Known protected patterns (case-insensitive)
    PROTECTED_PATTERNS: List[re.Pattern] = [
        re.compile(r"^SYSTEM\s*INSTRUCTION", re.IGNORECASE),
        re.compile(r"^SAFETY\s*CONSTRAINT", re.IGNORECASE),
        re.compile(r"^SECURITY\s*POLICY", re.IGNORECASE),
        re.compile(r"^USER\s*REQUIREMENT", re.IGNORECASE),
        re.compile(r"^CRITICAL\s*DECISION", re.IGNORECASE),
        re.compile(r"^EMERGENCY\s*PROTOCOL", re.IGNORECASE),
        re.compile(r"^DO\s*NOT\s*REMOVE", re.IGNORECASE),
        re.compile(r"^\[PROTECTED\]", re.IGNORECASE),
        re.compile(r"^\[KEEP\]", re.IGNORECASE),
        re.compile(r"^\[MANDATORY\]", re.IGNORECASE),
    ]

    # Protected block markers (wrap entire sections)
    PROTECTED_BLOCKS: List[tuple] = [
        ("<protected>", "</protected>"),
        ("<keep>", "</keep>"),
        ("<mandatory>", "</mandatory>"),
        ("<system-instructions>", "</system-instructions>"),
        ("<safety-constraints>", "</safety-constraints>"),
    ]

    @classmethod
    def is_protected_line(cls, line: str) -> bool:
        """Check if a line matches a protected pattern."""
        return any(p.search(line) for p in cls.PROTECTED_PATTERNS)

    @classmethod
    def extract_protected_blocks(cls, text: str) -> List[Dict[str, str]]:
        """Extract all protected blocks from text, returning their content."""
        blocks = []
        for open_tag, close_tag in cls.PROTECTED_BLOCKS:
            pattern = re.compile(
                re.escape(open_tag) + r"(.*?) " + re.escape(close_tag),
                re.DOTALL | re.IGNORECASE,
            )
            for m in pattern.finditer(text):
                blocks.append({"tag": open_tag, "content": m.group(1).strip()})
        return blocks

    @classmethod
    def restore_protected_blocks(cls, text: str, blocks: List[Dict[str, str]]) -> str:
        """Restore protected blocks into compressed text."""
        for block in blocks:
            placeholder = f"__PROTECTED_{hash(block['content'])}__"
            text = text.replace(placeholder, block["content"])
        return text


# ── Preservation policy ──────────────────────────────────────────────

@dataclass
class PreservationPolicy:
    """Defines what content must be preserved during compression."""
    preserve_system_instructions: bool = True
    preserve_security_constraints: bool = True
    preserve_user_requirements: bool = True
    preserve_error_messages: bool = True
    preserve_stack_traces: bool = True
    preserve_pending_actions: bool = True
    preserve_relevant_references: bool = True
    preserve_code_identifiers: bool = True
    min_preservation_threshold: float = 0.8  # Never compress below this % of original

    def check(self, text: str) -> List[str]:
        """Return list of items that must be preserved in text."""
        checks = []
        if self.preserve_system_instructions and "system" in text.lower():
            checks.append("system_instruction")
        if self.preserve_error_messages:
            if re.search(r"\b(ERROR|FAILED|Exception|Traceback)\b", text, re.IGNORECASE):
                checks.append("error_message")
        if self.preserve_stack_traces:
            if re.search(r"Traceback.*most recent", text, re.DOTALL | re.IGNORECASE):
                checks.append("stack_trace")
        if self.preserve_pending_actions:
            if re.search(r"\b(pending|todo|active\s*task)\b", text, re.IGNORECASE):
                checks.append("pending_action")
        return checks


# ── Compression audit ────────────────────────────────────────────────

@dataclass
class CompressionAudit:
    """Audit log entry for a compression operation."""
    timestamp: float = field(default_factory=time.time)
    operation: str = ""
    input_chars: int = 0
    output_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    savings_pct: float = 0.0
    protected_items: List[str] = field(default_factory=list)
    dropped_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "savings_pct": self.savings_pct,
            "protected_items": self.protected_items,
            "dropped_items": self.dropped_items,
            "warnings": self.warnings,
        }

    def __str__(self) -> str:
        return (
            f"[{self.operation}] {self.input_chars}→{self.output_chars} chars "
            f"({self.savings_pct:.1f}% saved, {len(self.protected_items)} protected)"
        )


# ── Safety check ─────────────────────────────────────────────────────

class SafetyCheck:
    """Validate that compressed output hasn't lost critical information."""

    CRITICAL_PATTERNS: List[tuple] = [
        (r"\b(ERROR|FAILED|Exception|Traceback)\b", "error"),
        (r"Traceback.*most recent", "stack_trace"),
        (r"^\s*[-+]\s*\S", "diff_line"),  # diff +/- lines
        (r"^\s*import\s+", "import_statement"),
        (r"^\s*from\s+\S+\s+import", "from_import"),
        (r"^\s*def\s+\w+", "function_def"),
        (r"^\s*class\s+\w+", "class_def"),
        (r"^\s*if\s+", "conditional"),
        (r"^\s*return\s+", "return_statement"),
        (r"\bTODO\b", "todo_marker"),
        (r"\bFIXME\b", "fixme_marker"),
        (r"\bHACK\b", "hack_marker"),
        (r"^\s*#\s*(NOTE|WARNING|IMPORTANT)", "comment_marker"),
    ]

    @classmethod
    def check(cls, original: str, compressed: str) -> Dict[str, Any]:
        """Check compressed output for missing critical content.

        Returns dict with:
          - integrity: bool (True if all critical patterns preserved)
          - missing: List[str] (patterns found in original but not in compressed)
          - warnings: List[str] (suspicious changes)
        """
        missing = []
        warnings = []

        for pattern, label in cls.CRITICAL_PATTERNS:
            orig_match = re.search(pattern, original, re.MULTILINE | re.IGNORECASE)
            comp_match = re.search(pattern, compressed, re.MULTILINE | re.IGNORECASE)
            if orig_match and not comp_match:
                missing.append(label)

        # Check for suspicious truncation markers
        if "truncated" in compressed.lower() and "truncated" not in original.lower():
            warnings.append("compression introduced truncation marker")

        # Check for significant content loss
        orig_lines = set(original.splitlines())
        comp_lines = set(compressed.splitlines())
        lost_lines = orig_lines - comp_lines
        if len(lost_lines) > len(orig_lines) * 0.3:
            warnings.append(f"significant line loss: {len(lost_lines)}/{len(orig_lines)}")

        return {
            "integrity": len(missing) == 0,
            "missing": missing,
            "warnings": warnings,
        }

    @classmethod
    def assert_integrity(cls, original: str, compressed: str) -> None:
        """Raise ValueError if integrity check fails."""
        result = cls.check(original, compressed)
        if not result["integrity"]:
            raise ValueError(
                f"Integrity check failed. Missing: {result['missing']}"
            )
        for w in result["warnings"]:
            logger.warning("Safety warning: %s", w)


__all__ = [
    "ProtectedMarker",
    "PreservationPolicy",
    "CompressionAudit",
    "SafetyCheck",
]
