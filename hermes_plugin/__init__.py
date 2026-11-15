"""Boster plugin: shrink terminal output and tool results before they reach the model.

Hooks (both fail-open, first string result wins):
  transform_terminal_output  noisy CLI output (compress)
  transform_tool_result      content-bearing results (code/prose/JSON); uses the
                             *safe* compressor (ANSI strip + blank collapse + head/tail
                             cap) — NEVER drops content lines or dedupes, so source
                             files and search results are not corrupted.
Env:
  BOSTER_PATH       dir containing the `boster` package
                    (default C:/Users/celes/Documents/Hermes-Workspace/Boster)
  BOSTER_DISABLE=1  turn the plugin into a no-op
  BOSTER_MIN_CHARS  only touch outputs longer than this (default 1500)
  BOSTER_MIN_SAVE   only replace if >= this % smaller (default 10)
  BOSTER_MAX_LINES  tool-result head/tail cap in lines (0 = off, default)
  BOSTER_MAX_CHARS  tool-result hard char cap (0 = off, default)
Stats (kind=terminal|tool_result): $HERMES_HOME/logs/boster_savings.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_DEFAULT_PATH = "C:/Users/celes/Documents/Hermes-Workspace/Boster"
_compress = None
_safe_compress = None


def _load():
    """Import the `boster` package once; cache both compressors."""
    global _compress, _safe_compress
    if _compress is None:
        p = os.environ.get("BOSTER_PATH", _DEFAULT_PATH)
        if p not in sys.path:
            sys.path.insert(0, p)
        from boster import compress_output, safe_compress
        _compress = compress_output
        _safe_compress = safe_compress
    return _compress, _safe_compress


def _log(rec: dict) -> None:
    try:
        from hermes_constants import get_hermes_home
        f = Path(get_hermes_home()) / "logs" / "boster_savings.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _on_transform_terminal_output(command="", output="", returncode=0, **_kw):
    try:
        if os.environ.get("BOSTER_DISABLE") == "1" or not isinstance(output, str):
            return None
        min_chars = int(os.environ.get("BOSTER_MIN_CHARS", "1500"))
        if len(output) < min_chars:
            return None
        compressor, _ = _load()
        out = compressor(output)
        saved = 100.0 * (len(output) - len(out)) / max(len(output), 1)
        if saved < float(os.environ.get("BOSTER_MIN_SAVE", "10")):
            return None
        _log({"ts": time.time(), "kind": "terminal", "cmd": str(command)[:120], "rc": returncode,
              "before": len(output), "after": len(out), "saved_pct": round(saved, 1)})
        return out
    except Exception as e:  # fail-open: never break a tool call
        logger.debug("boster plugin error: %s", e)
        return None


def _on_transform_tool_result(tool_name="", result=None, **_kw):
    try:
        if os.environ.get("BOSTER_DISABLE") == "1" or not isinstance(result, str):
            return None
        min_chars = int(os.environ.get("BOSTER_MIN_CHARS", "1500"))
        if len(result) < min_chars:
            return None
        _, safe = _load()
        max_lines = int(os.environ.get("BOSTER_MAX_LINES", "0"))
        max_chars = int(os.environ.get("BOSTER_MAX_CHARS", "0"))
        # Safe transform only: no content-line dropping, no dedup, no format guess.
        out = safe(result, max_lines=max_lines, max_chars=max_chars)
        if out == result:
            return None
        saved = 100.0 * (len(result) - len(out)) / max(len(result), 1)
        if saved < float(os.environ.get("BOSTER_MIN_SAVE", "10")):
            return None
        _log({"ts": time.time(), "kind": "tool_result", "tool": str(tool_name)[:60],
              "before": len(result), "after": len(out), "saved_pct": round(saved, 1)})
        return out
    except Exception as e:  # fail-open: never break a tool call
        logger.debug("boster plugin error: %s", e)
        return None


def register(ctx) -> None:
    ctx.register_hook("transform_terminal_output", _on_transform_terminal_output)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
