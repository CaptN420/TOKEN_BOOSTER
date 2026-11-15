"""Terminal output compression (enhanced)."""
from __future__ import annotations

import re
from typing import List, Optional

# Re-export from original for backwards compat
from ..output_compressor import compress as _orig_compress, safe_compress as _orig_safe_compress


def compress_terminal(
    text: str,
    *,
    level: int = 2,
    max_lines: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Compress terminal output with configurable compression level.

    Level 0: No compression
    Level 1: Strip noise (progress bars, timestamps, banners)
    Level 2: Structural compression (dedupe, collapse blanks)
    Level 3: Context-aware reduction (keep errors, truncate long lines)
    Level 4: Aggressive reduction (keep only headers and errors)
    """
    if level == 0 or not text:
        return text

    # Apply base compression
    result = _orig_compress(text, max_lines=max_lines, max_chars=max_chars)

    if level >= 1:
        # Strip progress bars and repeated lines
        result = _strip_progress_bars(result)
        result = _dedupe_consecutive(result)

    if level >= 2:
        # Collapse blank lines
        result = re.sub(r"\n{3,}", "\n\n", result)

    if level >= 3:
        # Truncate long lines, preserve errors
        result = _truncate_long_lines(result, max_line_chars=200)

    if level >= 4:
        # Aggressive: keep only headers and errors
        result = _keep_essentials(result)

    return result


def _strip_progress_bars(text: str) -> str:
    """Remove progress bar lines."""
    progress_re = re.compile(
        r"^(?:\d+%|\d+/\d+|.*[\u2580-\u259f].*|"
        r"⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏|"
        r"[▰▱■□▪▫]+)"
    )
    lines = text.splitlines()
    return "\n".join(l for l in lines if not progress_re.match(l.strip()))


def _dedupe_consecutive(text: str) -> str:
    """Remove consecutive duplicate lines."""
    lines = text.splitlines()
    if not lines:
        return ""
    result = [lines[0]]
    for line in lines[1:]:
        if line != result[-1]:
            result.append(line)
    return "\n".join(result)


def _truncate_long_lines(text: str, max_line_chars: int = 200) -> str:
    """Truncate long lines, preserving errors."""
    error_re = re.compile(r"\b(ERROR|Exception|Traceback|FAILED)\b", re.IGNORECASE)
    lines = text.splitlines()
    result = []
    for line in lines:
        if len(line) > max_line_chars and not error_re.search(line):
            result.append(line[:max_line_chars // 2] + f" ... [{len(line) - max_line_chars} more] ..." + line[-max_line_chars // 4:])
        else:
            result.append(line)
    return "\n".join(result)


def _keep_essentials(text: str) -> str:
    """Keep only headers, errors, and summaries."""
    header_re = re.compile(r"^(#{1,3}\s|===+|--+\s|\*+\s)", re.MULTILINE)
    error_re = re.compile(r"\b(ERROR|FAILED|Exception|Traceback|FATAL|CRITICAL)\b", re.IGNORECASE)
    summary_re = re.compile(r"\b(summary|total|result|conclusion|final|average)\b", re.IGNORECASE)

    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if header_re.search(stripped) or error_re.search(stripped) or summary_re.search(stripped):
            result.append(stripped)
        elif len(result) < 20:
            result.append(stripped)
    return "\n".join(result)


__all__ = ["compress_terminal"]
