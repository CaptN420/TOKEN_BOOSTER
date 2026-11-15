"""Prompt optimization: remove duplicates, normalize, reduce."""
from __future__ import annotations

import re
from typing import List, Optional


def optimize_prompt(
    prompt: str,
    *,
    remove_duplicates: bool = True,
    normalize_whitespace: bool = True,
    max_length: Optional[int] = None,
) -> str:
    """Optimize a prompt by removing duplicates and normalizing.

    NEVER rewrites or removes instructions — only removes redundancy.
    """
    if not prompt:
        return ""

    lines = prompt.splitlines()

    # Remove duplicate consecutive lines
    if remove_duplicates:
        deduped = [lines[0]] if lines else []
        for line in lines[1:]:
            if line != deduped[-1]:
                deduped.append(line)
        lines = deduped

    # Normalize whitespace
    if normalize_whitespace:
        lines = [line.rstrip() for line in lines]
        # Collapse multiple blank lines
        result = []
        blank_count = 0
        for line in lines:
            if not line.strip():
                blank_count += 1
                if blank_count <= 1:
                    result.append("")
            else:
                blank_count = 0
                result.append(line)
        lines = result

    text = "\n".join(lines)

    # Hard length cap
    if max_length and len(text) > max_length:
        # Preserve first and last parts
        half = max_length // 2
        text = text[:half] + f"\n... [truncated, {len(text) - max_length} chars dropped] ..." + text[-half:]

    return text


def detect_redundant_sections(prompt: str) -> List[str]:
    """Detect potentially redundant sections in a prompt."""
    warnings = []

    # Check for repeated instructions
    instruction_patterns = [
        re.compile(r"^\s*(do\s+not|never|always)\s+", re.IGNORECASE),
        re.compile(r"^\s*(remember|note|important)\s+", re.IGNORECASE),
    ]

    seen_instructions: set = set()
    for line in prompt.splitlines():
        for pattern in instruction_patterns:
            if pattern.match(line):
                normalized = line.strip().lower()[:50]
                if normalized in seen_instructions:
                    warnings.append(f"Possible duplicate instruction: {line.strip()[:80]}")
                seen_instructions.add(normalized)

    # Check for repeated context blocks
    block_re = re.compile(r"\[.*?\]", re.DOTALL)
    blocks = block_re.findall(prompt)
    block_counts: dict = {}
    for block in blocks:
        block_counts[block[:50]] = block_counts.get(block[:50], 0) + 1
    for block, count in block_counts.items():
        if count > 2:
            warnings.append(f"Context block repeated {count} times")

    return warnings


__all__ = ["optimize_prompt", "detect_redundant_sections"]
