#!/usr/bin/env python3
"""
adaptive.py — Adaptive summarization for very long text.

When output exceeds a size threshold, applies tiered summarization:
  - Tier 1: Strip noise (same as compress)
  - Tier 2: Extract key lines (headers, errors, summaries, stats)
  - Tier 3: Paragraph-level compression (keep first/last N chars of long blocks)

Usage:
    from boster.adaptive import adaptive_compress

    result = adaptive_compress(raw, max_chars=4000)
"""
from __future__ import annotations

import re
from typing import List, Optional


# ── Tier patterns ────────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^(#{1,3}\s|===+\s|---+\s|\*+\s)", re.MULTILINE)
_ERROR_RE = re.compile(r"\b(ERROR|FAILED|Exception|Traceback|FATAL|CRITICAL)\b", re.IGNORECASE)
_SUMMARY_RE = re.compile(
    r"\b(summary|total|result|conclusion|outcome|final|average|median|max|min|count|sum)\b",
    re.IGNORECASE,
)
_STAT_LINE_RE = re.compile(r"\b\d+[\s,]*\d+\b.*\b(file|line|test|function|error|warning|passed|failed)\b", re.IGNORECASE)
_JSON_START_RE = re.compile(r"^\s*\{|\[")
_DIFF_START_RE = re.compile(r"^diff\s+|^---\s|\^\+\+\+\s|^\@\s", re.MULTILINE)
_DOCKER_RE = re.compile(r"^(Step|Successfully| --->|Removing|Untagged|Built|Pulling|Using)\b", re.IGNORECASE)
_TREE_RE = re.compile(r"^\w[\w./\\-]*\s*\|", re.MULTILINE)  # tree-like output


def _is_json(text: str) -> bool:
    """Check if text looks like JSON."""
    stripped = text.strip()
    return stripped.startswith(("{", "[")) and len(stripped) > 10


def _is_diff(text: str) -> bool:
    """Check if text looks like a git diff."""
    return bool(_DIFF_START_RE.search(text[:200]))


def _is_docker(text: str) -> bool:
    """Check if text looks like Docker output."""
    return bool(_DOCKER_RE.search(text[:500]))


def _is_tree(text: str) -> bool:
    """Check if text looks like tree/ls output."""
    lines = text.splitlines()[:20]
    tree_indicators = sum(1 for l in lines if "|" in l or "├" in l or "└" in l or "─" in l)
    return tree_indicators > len(lines) * 0.3


def _is_markdown(text: str) -> bool:
    """Check if text looks like markdown."""
    md_markers = ["#", "**", "***", "`", "```", "[", "!", "http"]
    return any(m in text[:200] for m in md_markers) and len(text) > 100


def _extract_key_lines(lines: List[str], max_keep: int = 30) -> List[str]:
    """Extract the most important lines from output."""
    scored: List[tuple] = []
    for i, line in enumerate(lines):
        score = 0.0
        if _ERROR_RE.search(line):
            score += 10.0
        if _HEADER_RE.search(line):
            score += 5.0
        if _SUMMARY_RE.search(line):
            score += 3.0
        if _STAT_LINE_RE.search(line):
            score += 2.0
        # Keep first and last lines always
        if i == 0 or i == len(lines) - 1:
            score += 1.0
        # Short lines are more likely to be important
        if 5 < len(line.strip()) < 100:
            score += 0.5
        scored.append((score, i, line))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    kept_indices = {i for _, i, _ in scored[:max_keep]}
    # Preserve order
    result = [line for i, line in enumerate(lines) if i in kept_indices]
    return result


def _compress_json(text: str, max_chars: int = 4000) -> str:
    """Compress JSON by removing whitespace and comments."""
    # Remove comments (// and /* */)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Compact: remove whitespace between tokens
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*:\s*", ":", text)
    # Truncate if still too long
    if len(text) > max_chars:
        text = text[:max_chars // 2] + f" ... [truncated, {len(text) - max_chars} chars dropped]" + text[-max_chars // 4:]
    return text


def _compress_diff(text: str, max_chars: int = 4000) -> str:
    """Compress diff output: keep headers and key changes, drop context."""
    lines = text.splitlines()
    result = []
    in_hunk = False
    hunk_header = ""
    added = 0
    removed = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("diff "):
            # New diff section — keep header
            if result and (added > 0 or removed > 0):
                result.append(f"@@ ... {removed} removed, {added} added @@")
            result.append(stripped[:120])
            in_hunk = False
            added = 0
            removed = 0
            continue
        if stripped.startswith("@@"):
            in_hunk = True
            hunk_header = stripped
            result.append(stripped[:120])
            continue
        if not in_hunk:
            continue
        if stripped.startswith("+") and not stripped.startswith("+++"):
            result.append(line)
            added += 1
        elif stripped.startswith("-") and not stripped.startswith("---"):
            result.append(line)
            removed += 1
        elif stripped.startswith(" "):
            # Keep at most 3 context lines per hunk
            if added + removed < 6:
                result.append(line)

    if result and (added > 0 or removed > 0):
        result.append(f"@@ ... {removed} removed, {added} added @@")

    out = "\n".join(result)
    if len(out) > max_chars:
        out = out[:max_chars // 2] + f"\n... [truncated, {len(out) - max_chars} chars dropped]" + out[-max_chars // 4:]
    return out


def _compress_docker(text: str, max_chars: int = 4000) -> str:
    """Compress Docker output: keep step summaries and final result."""
    lines = text.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^Step\s+\d+", stripped, re.IGNORECASE):
            result.append(stripped)
        elif "Successfully" in stripped or " ---> " in stripped:
            result.append(stripped[:120])
        elif "Error" in stripped or "FAILED" in stripped:
            result.append(stripped)
        elif "Finished" in stripped or "done" in stripped.lower():
            result.append(stripped)
    return "\n".join(result) if result else text


def _compress_tree(text: str, max_chars: int = 4000) -> str:
    """Compress tree output: keep top-level and first few children per dir."""
    lines = text.splitlines()
    result = []
    depth_counts: dict = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Count leading spaces to determine depth
        depth = len(line) - len(line.lstrip())
        key = depth // 2  # rough depth estimate

        if key not in depth_counts:
            depth_counts[key] = 0
        depth_counts[key] += 1

        # Keep root and first N items per depth
        if depth == 0 or depth_counts[key] <= 15:
            result.append(stripped)
        elif depth_counts[key] == 16:
            result.append(f"... ({depth_counts[key]} more items at this level)")

    out = "\n".join(result)
    if len(out) > max_chars:
        out = out[:max_chars // 2] + f"\n... [truncated]" + out[-max_chars // 4:]
    return out


def _compress_markdown(text: str, max_chars: int = 4000) -> str:
    """Compress markdown: remove excessive blank lines, collapse code blocks."""
    lines = text.splitlines()
    result = []
    in_code = False
    code_lines = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                result.append(f"```\n... [{code_lines} lines] ...")
            in_code = not in_code
            code_lines = 0
            result.append(stripped)
            continue
        if in_code:
            code_lines += 1
            if code_lines <= 20 or code_lines > max(20, len(lines) - 40):
                result.append(line)
            continue
        if not stripped:
            # Collapse blank lines
            if result and result[-1] != "":
                result.append("")
            continue
        result.append(stripped)

    out = "\n".join(result)
    if len(out) > max_chars:
        out = out[:max_chars // 2] + f"\n... [truncated]" + out[-max_chars // 4:]
    return out


def _compress_generic(text: str, max_chars: int = 4000) -> str:
    """Generic compression: extract key lines, truncate long blocks."""
    lines = text.splitlines()
    if len(lines) <= 50:
        return text

    # Extract key lines (errors get highest priority)
    key_lines = _extract_key_lines(lines, max_keep=40)

    # Ensure error/traceback lines are always present
    error_lines = [l for l in lines if _ERROR_RE.search(l)]
    for el in error_lines:
        if el not in key_lines:
            key_lines.append(el)

    # For remaining long lines, truncate to first/last portion
    result = []
    for line in lines:
        if len(line) > 200:
            result.append(line[:100] + f" ... [{len(line) - 200} more chars] ..." + line[-100:])
        else:
            result.append(line)

    # Merge key lines with truncated result, preserving order
    all_lines = list(dict.fromkeys(key_lines + result))  # dedupe, preserve order

    # Always ensure error/traceback lines are at the end (survive truncation)
    error_preserve = [l for l in all_lines if _ERROR_RE.search(l)]
    all_lines = [l for l in all_lines if not _ERROR_RE.search(l)]
    all_lines = all_lines + error_preserve

    out = "\n".join(all_lines)

    if len(out) > max_chars:
        # Truncate but preserve trailing error lines
        err_text = "\n".join(error_preserve)
        budget = max_chars - len(err_text) - 1
        if budget > 0:
            out = out[:budget] + f"\n... [truncated, {len(out) - budget} chars dropped]" + err_text
        else:
            out = err_text
    return out


# ── Public API ────────────────────────────────────────────────────────

def adaptive_compress(
    text: str,
    *,
    max_chars: int = 4000,
    min_save_pct: float = 10.0,
) -> tuple[str, dict]:
    """Apply adaptive compression to very long text.

    Args:
        text: Raw text to compress.
        max_chars: Hard cap on output characters.
        min_save_pct: Only return compressed if savings exceed this %.

    Returns:
        Tuple of (compressed_text, report_dict).
    """
    if not text:
        return "", {"original": 0, "compressed": 0, "savings_pct": 0.0}

    original_len = len(text)
    original_lines = text.count("\n") + 1

    # Detect format and apply specialized compressor
    if _is_json(text):
        compressed = _compress_json(text, max_chars)
    elif _is_diff(text):
        compressed = _compress_diff(text, max_chars)
    elif _is_docker(text):
        compressed = _compress_docker(text, max_chars)
    elif _is_tree(text):
        compressed = _compress_tree(text, max_chars)
    elif _is_markdown(text):
        compressed = _compress_markdown(text, max_chars)
    else:
        compressed = _compress_generic(text, max_chars)

    compressed_len = len(compressed)
    savings_pct = ((original_len - compressed_len) / max(original_len, 1)) * 100

    report = {
        "original": original_len,
        "compressed": compressed_len,
        "savings_pct": round(savings_pct, 1),
        "original_lines": original_lines,
        "compressed_lines": compressed.count("\n") + 1,
        "method": "adaptive",
    }

    if savings_pct < min_save_pct:
        return text, report

    return compressed, report


__all__ = ["adaptive_compress"]
