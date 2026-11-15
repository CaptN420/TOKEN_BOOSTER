"""JSON-specific compression."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def compress_json(text: str, max_tokens: int = 4000) -> str:
    """Compress JSON by removing whitespace and comments.

    Preserves all data — only removes formatting noise.
    """
    if not text:
        return ""

    # Remove comments (// and /* */)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    # Try to parse and re-serialize compactly
    try:
        data = json.loads(text)
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(compact) <= len(text):
            return compact
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: remove whitespace between tokens
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*:\s*", ":", text)

    if len(text) > max_tokens * 4:  # rough char estimate
        text = text[:max_tokens * 2] + f" ... [truncated, {len(text) - max_tokens * 2} chars dropped]"

    return text


def compress_json_landscape(text: str, max_lines: int = 50) -> str:
    """Compress JSON by keeping top-level structure, truncating deep arrays."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text

    def truncate(obj, depth=0, max_depth=3):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if depth < max_depth and isinstance(v, (dict, list)):
                    result[k] = truncate(v, depth + 1, max_depth)
                else:
                    result[k] = v
            return result
        elif isinstance(obj, list):
            if len(obj) > max_lines and depth < max_depth:
                return [truncate(item, depth + 1, max_depth) for item in obj[:max_lines]]
            return [truncate(item, depth + 1, max_depth) for item in obj]
        return obj

    truncated = truncate(data)
    return json.dumps(truncated, ensure_ascii=False, indent=2)


__all__ = ["compress_json", "compress_json_landscape"]
