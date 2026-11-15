"""Code compression: preserve structure, remove noise."""
from __future__ import annotations

import re
from typing import List


def compress_code(text: str, max_lines: int = 200) -> str:
    """Compress code: remove blank lines, collapse comments, keep structure."""
    if not text:
        return ""

    lines = text.splitlines()
    result: List[str] = []
    consecutive_blanks = 0
    in_comment_block = False

    for line in lines:
        stripped = line.strip()

        # Skip excessive blank lines
        if not stripped:
            consecutive_blanks += 1
            if consecutive_blanks <= 1:
                result.append("")
            continue
        consecutive_blanks = 0

        # Collapse single-line comments (keep multi-line block markers)
        if stripped.startswith("#") and not stripped.startswith("#!") and len(stripped) > 50:
            result.append(stripped[:50] + "...")
            continue
        if stripped.startswith("//") and len(stripped) > 50:
            result.append(stripped[:50] + "...")
            continue

        # Keep everything else
        result.append(line)

    out = "\n".join(result)

    # Hard cap
    if max_lines and len(out.splitlines()) > max_lines:
        lines = out.splitlines()
        half = max_lines // 2
        out = "\n".join(lines[:half] + [f"... ({len(lines) - max_lines} more lines) ..."] + lines[-half:])

    return out


def extract_important_code(text: str) -> str:
    """Extract only the important parts of code (classes, functions, imports)."""
    patterns = [
        re.compile(r"^\s*(class\s+\w+|def\s+\w+|import\s+|from\s+\S+\s+import)", re.MULTILINE),
        re.compile(r"^\s*(async\s+def\s+\w+|async\s+class\s+\w+)", re.MULTILINE),
    ]

    result_lines = []
    for line in text.splitlines():
        if any(p.search(line) for p in patterns):
            result_lines.append(line)

    if not result_lines:
        return text[:500]  # Fallback: first 500 chars
    return "\n".join(result_lines)


__all__ = ["compress_code", "extract_important_code"]
