"""Log compression: strip timestamps, levels, keep errors."""
from __future__ import annotations

import re
from typing import List


# Common log patterns
_LOG_PATTERN = re.compile(
    r"^\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(:\d{2})?"
    r"(\s*[+-]\d{2}:?\d{2})?"
    r"\s*\|?\s*(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s*\|?\s*",
    re.IGNORECASE,
)

_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def compress_logs(text: str, keep_errors: bool = True) -> str:
    """Compress log output: strip timestamps/levels, preserve errors."""
    if not text:
        return ""

    lines = text.splitlines()
    result: List[str] = []
    error_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Strip ANSI codes
        clean = _ANSI_PATTERN.sub("", stripped)

        # Check if error
        is_error = bool(re.search(r"\b(ERROR|FAILED|Exception|Traceback|FATAL|CRITICAL)\b", clean, re.IGNORECASE))

        if is_error and keep_errors:
            result.append(clean)
            error_count += 1
            continue

        # Strip timestamp and level
        cleaned = _LOG_PATTERN.sub("", clean)
        if cleaned:
            result.append(cleaned)

    # If we kept too many lines, truncate
    if len(result) > 100:
        errors = [l for l in result if re.search(r"\b(ERROR|Exception|Traceback)\b", l, re.IGNORECASE)]
        others = [l for l in result if l not in errors]
        result = others[:50] + errors

    return "\n".join(result)


__all__ = ["compress_logs"]
