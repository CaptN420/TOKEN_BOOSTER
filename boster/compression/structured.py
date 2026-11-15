"""Structured data compression (CSV, SQL, diffs, etc.)."""
from __future__ import annotations

import re
from typing import List, Optional


def compress_csv(text: str, max_rows: int = 100) -> str:
    """Compress CSV: keep header + sample rows."""
    lines = text.splitlines()
    if len(lines) <= max_rows:
        return text

    # Keep header
    header = lines[0]
    # Sample evenly from data
    step = max(1, (len(lines) - 1) // (max_rows - 1))
    sampled = [header]
    for i in range(1, len(lines), step):
        sampled.append(lines[i])
    if len(sampled) < max_rows:
        sampled.append(lines[-1])

    return "\n".join(sampled)


def compress_sql(text: str) -> str:
    """Compress SQL: remove whitespace, comments, normalize."""
    if not text:
        return ""
    # Remove comments
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*;\s*", "; ", text)
    return text.strip()


def compress_diff(text: str, max_hunks: int = 10) -> str:
    """Compress git diff: keep headers and key changes."""
    lines = text.splitlines()
    result = []
    hunk_count = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("diff "):
            if hunk_count >= max_hunks:
                break
            result.append(stripped[:120])
            hunk_count += 1
            continue
        if stripped.startswith("@@"):
            result.append(stripped[:120])
            continue
        if stripped.startswith("+") and not stripped.startswith("+++"):
            result.append(line)
        elif stripped.startswith("-") and not stripped.startswith("---"):
            result.append(line)
        elif stripped.startswith(" ") and len(result) < 50:
            result.append(line)

    return "\n".join(result)


def compress_git_log(text: str, max_commits: int = 20) -> str:
    """Compress git log: keep commit hash + subject."""
    lines = text.splitlines()
    result = []
    commit_count = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("commit ") and len(stripped) > 40:
            if commit_count >= max_commits:
                break
            result.append(stripped[:80])
            commit_count += 1
        elif stripped.startswith(("Author:", "Date:")):
            continue
        elif not stripped:
            continue
        else:
            result.append(stripped)

    return "\n".join(result)


__all__ = ["compress_csv", "compress_sql", "compress_diff", "compress_git_log"]
