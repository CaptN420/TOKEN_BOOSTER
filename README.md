# Boster

Deterministic, stdlib-only token savers extracted from `Life_mean_realm_STOP`
(`code_lab/compression`, originally CaptN-BRAIN). No LLM call, no network.

## Commands

| Command | What it does |
|---|---|
| `boster compress [file] [-f fmt] [--max-lines N] [--max-chars N] [--stats]` | strips progress bars, banners, duplicate/cached lines from tool output (stdin or file) |
| `boster adaptive [file] [--max-chars N]` | smart auto-detect compression for very long text (JSON, diff, Docker, tree, markdown, generic) |
| `boster history [file.json] [--keep N]` | summarizes old chat turns into facts/pending/dead-ends |
| `boster budget [file.json] --total N [--query Q]` | keeps the most relevant messages within a token budget |
| `boster pipeline [file.json] [--total N] [--no-compress] [--no-summarize] [--json]` | full pipeline: compress outputs + summarize history + apply budget (JSON input) |
| `boster count [file]` | token estimate |

## New in v0.2.0

- **`adaptive`** — auto-detects format (JSON, git diff, Docker, tree, markdown) and applies specialized compression
- **`pipeline`** — chains compress → history summary → context budget into one operation for full conversation JSON
- **Error preservation** — error/traceback lines are never dropped, even under aggressive truncation
- **8 format-specific compressors** — git-log, pytest, pip, cyclomatic, LOC, depgraph, codebase-map, plus new: JSON, diff, Docker, tree, markdown

## Install

```bash
uv pip install -e .
# then
boster compress myoutput.txt --stats
boster adaptive verylongoutput.txt --max-chars 4000
boster pipeline conversation.json --total 20000
```

## Test

```bash
uv run --no-project --with pytest python -m pytest tests -q
```

Note: the original `captn_runtime.CaptNRuntime.run()` returns a *simulated* response
and does not call any LLM; only the compression modules were real, so only they were extracted.

Hermes integration (plugin hooking `transform_terminal_output` + `transform_tool_result`): see `docs/HERMES_INTEGRATION.md`.
