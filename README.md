# Boster

Deterministic, stdlib-only token savers extracted from `Life_mean_realm_STOP`
(`code_lab/compression`, originally CaptN-BRAIN). No LLM call, no network.

| Command | What it does |
|---|---|
| `boster compress [file] [-f fmt] [--max-lines N] [--max-chars N] [--stats]` | strips progress bars, banners, duplicate/cached lines from tool output (stdin or file) |
| `boster history [file.json] [--keep N]` | summarizes old chat turns into facts/pending/dead-ends |
| `boster budget [file.json] --total N [--query Q]` | keeps the most relevant messages within a token budget |
| `boster count [file]` | token estimate |

Install: `uv pip install -e .` then `boster ...`, or `python -m boster ...`.
Test: `uv run --no-project --with pytest python -m pytest tests -q`.

Note: the original `captn_runtime.CaptNRuntime.run()` returns a *simulated* response
and does not call any LLM; only the compression modules were real, so only they were extracted.

Hermes integration (plugin hooking `transform_terminal_output` + `transform_tool_result`): see `docs/HERMES_INTEGRATION.md`.
