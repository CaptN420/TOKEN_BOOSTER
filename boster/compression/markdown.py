"""Markdown compression."""
from __future__ import annotations

import re
from typing import List


def compress_markdown(text: str, max_chars: int = 4000) -> str:
    """Compress markdown: remove excessive blank lines, collapse code blocks."""
    if not text:
        return ""

    lines = text.splitlines()
    result: List[str] = []
    in_code = False
    code_lines = 0
    consecutive_blanks = 0

    for line in lines:
        stripped = line.strip()

        # Handle code blocks
        if stripped.startswith("```"):
            if in_code:
                if code_lines > 20:
                    result.append(f"``` ... [{code_lines} lines] ...")
                else:
                    result.extend(result[-code_lines:] if code_lines > 0 else [])
            in_code = not in_code
            code_lines = 0
            result.append(stripped)
            consecutive_blanks = 0
            continue

        if in_code:
            code_lines += 1
            result.append(line)
            consecutive_blanks = 0
            continue

        # Collapse blank lines
        if not stripped:
            consecutive_blanks += 1
            if consecutive_blanks <= 2:
                result.append("")
            continue
        consecutive_blanks = 0
        result.append(stripped)

    out = "\n".join(result)
    if len(out) > max_chars:
        out = out[:max_chars // 2] + f"\n... [truncated]" + out[-max_chars // 4:]
    return out


__all__ = ["compress_markdown"]
