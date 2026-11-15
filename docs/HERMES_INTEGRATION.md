# Boster × Hermes — documentation

## 1. Origin and findings
- Extracted from `Life_mean_realm_STOP` (`code_lab/compression`, originally CaptN-BRAIN).
- A/B test (3 runs with the folder in the workspace, 3 without): prompt tokens 12 412 vs 12 414,
  speed 18–32 tok/s in both cases (noise). **The folder itself does not make Hermes faster.**
- `captn_runtime.CaptNRuntime.run()` returns a *simulated* response (no LLM call), so only the
  compression modules were kept.

## 2. Layout
```
Boster/
  boster/                 stdlib-only library + CLI (python -m boster / `boster`)
    output_compressor.py  strip noise from tool output
    history_compressor.py summarize old chat turns
    context_budget.py     relevance-based selection within a token budget
    cli.py                compress | history | budget | count
  hermes_plugin/          source copy of the Hermes plugin
  tests/test_boster.py    5 tests
  docs/HERMES_INTEGRATION.md
```

## 3. What the compressor removes / keeps
Removed: progress bars (`50%|###`), pure separator lines (`-----`, `=====`), `INFO/DEBUG/OK/SUCCESS`
prefixed lines, timestamps, consecutive duplicates, blank runs, pip "already satisfied/Using cached"
lines (replaced by a count marker).
Kept: `ERROR` / `WARNING` lines, tracebacks, rule lines carrying text (`=== 3 passed ===`), everything else.
Fixes vs the original: it used to drop `ERROR`/`WARNING` lines and pytest summaries.

## 4. Hermes plugin
Location: `%LOCALAPPDATA%\hermes\profiles\def\plugins\boster\` (profile `def`), enabled with
`hermes plugins enable boster`. `hermes_plugin/` in this repo is the source copy.

- Hook `transform_terminal_output` — runs on the output of every `terminal` call *before* it enters
  the model context (uses the lossy `compress`, which may drop noise lines).
- Hook `transform_tool_result` — runs on **every** tool result. Uses `safe_compress`
  (ANSI strip + blank-run collapse only; head/tail cap is opt-in via `BOSTER_MAX_LINES`/`BOSTER_MAX_CHARS`,
  default off). It NEVER dedupes consecutive lines or auto-detects format, so source files
  (`read_file`), search results (`search_files`/grep) and browser dumps are not corrupted.
- Both hooks: first string returned wins; any exception returns `None` (fail-open). Only outputs longer
  than `BOSTER_MIN_CHARS` (1500) are processed, and only replaced if at least `BOSTER_MIN_SAVE` % (10) smaller.
  The tool-result hook also skips non-`str` results (e.g. dict payloads).

| Env var | Default | Role |
|---|---|---|
| `BOSTER_PATH` | `C:/Users/celes/Documents/Hermes-Workspace/Boster` | where the `boster` package lives |
| `BOSTER_DISABLE` | unset | `1` = no-op |
| `BOSTER_MIN_CHARS` | 1500 | size threshold |
| `BOSTER_MIN_SAVE` | 10 | minimum saving % to replace |
| `BOSTER_MAX_LINES` | 0 (off) | tool-result head/tail cap (lines) |
| `BOSTER_MAX_CHARS` | 0 (off) | tool-result hard char cap |

Moving the Boster folder requires setting `BOSTER_PATH`.

## 5. Verify / measure
End-to-end test: a one-shot session ran `python -c` printing 80 "Requirement already satisfied" lines
plus `ERROR boom`; the model still reported `ERROR boom` as the last line, and the log recorded
3842 -> 55 chars (98.6 %).

Savings log: `%LOCALAPPDATA%\hermes\profiles\def\logs\boster_savings.jsonl`
(`ts, cmd, rc, before, after, saved_pct`).
Tests: `uv run --no-project --with pytest python -m pytest tests -q`.

## 6. Disable / remove
`hermes plugins disable boster` (or `BOSTER_DISABLE=1`); `hermes plugins remove boster` to uninstall.
Sessions already running keep their old plugin set until restarted.

## 7. Known limits
- Savings are large only on noisy output (installs, test runs, logs); on clean text about 0.
- Lossy by design: to get raw output, run with `BOSTER_DISABLE=1`.
- `history` and `budget` are CLI-only; not hooked into Hermes (Hermes has its own context compression).
