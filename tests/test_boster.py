import json, subprocess, sys
from boster import (BudgetConfig, ContextBudget, compress_history, compress_output,
                    format_compressed_history, savings_report, safe_compress,
                    estimate_provider_tokens)


def test_compress_strips_noise_keeps_errors():
    raw = "\n".join(["Requirement already satisfied: p%d" % i for i in range(30)]
                    + ["50%|####", "===== 3 passed in 0.1s =====", "ERROR boom", "-----"])
    out = compress_output(raw)
    assert "boom" in out and "3 passed" in out
    assert "p7" not in out and "30 " in out
    assert savings_report(raw, out)["savings_pct"] > 50


def test_history_summary():
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"edit src/f{i}.py step {i}"} for i in range(30)]
    s = compress_history(msgs, max_exchanges=5)
    txt = format_compressed_history(s, msgs[-5:], max_recent=3)
    assert txt and len(txt) < sum(len(m["content"]) for m in msgs)


def test_budget_reduces():
    msgs = [{"role": "user", "content": "lorem ipsum dolor " * 200 + str(i)} for i in range(60)]
    r = ContextBudget(config=BudgetConfig(total=3000, history=1000)).select(
        messages=msgs, user_query="lorem", dry_run=True)
    assert r.selected_total_tokens < r.original_total_tokens
    assert len(r.selected_messages) < len(msgs)


def test_safe_compress_keeps_source_code_unharmed():
    # Source with legitimately repeated lines and separator-looking content.
    code = "def f():\n    x = 1\n    x = 1\n    # ----\n    return x\n"
    assert safe_compress(code) == code


def test_safe_compress_strips_ansi_collapses_blank_runs():
    raw = "\x1b[31mred\x1b[0m\na\n\n\n\n\nb\n"
    out = safe_compress(raw)
    assert "\x1b" not in out            # ANSI gone
    assert "\n\n" in out and "\n\n\n\n" not in out  # blank run collapsed


def test_safe_compress_head_tail_cap_marks_truncation():
    body = "\n".join("line %d" % i for i in range(100))
    out = safe_compress(body, max_lines=20)
    assert "line 0" in out and "line 99" in out
    assert "more lines" in out          # honest marker, nothing silent
    assert out.count("\n") + 1 <= 21


def test_budget_keeps_conversation_order():
    # Regression: selected messages must stay in chronological order.
    msgs = []
    for i in range(12):
        msgs += [{"role": "user", "content": f"turn {i} hello world"},
                 {"role": "assistant", "content": f"reply {i}"}]
    msgs.insert(0, {"role": "system", "content": "sys"})
    r = ContextBudget(config=BudgetConfig(total=3000, history=600)).select(
        messages=msgs, user_query="hello", dry_run=True)
    turns = [int(m["content"].split()[1]) for m in r.selected_messages
             if m["content"].startswith("turn ")]
    assert turns == sorted(turns), f"history out of order: {turns}"
    assert r.selected_messages[0]["role"] == "system"


def test_history_truncation_counts_dropped_chars():
    # Regression: marker must report the real number of chars dropped.
    from boster import format_compressed_history
    import re as _re
    s = compress_history([{"role": "user", "content": "x"}], max_exchanges=1)
    long_msg = "abcdef" * 134  # 804 chars
    out = format_compressed_history(s, [{"role": "user", "content": long_msg}], max_recent=1)
    m = _re.search(r"\[(\d+) more chars\]", out)
    assert m and int(m.group(1)) == 304  # 804 - 500


def test_count():
    assert estimate_provider_tokens("hello world") > 0


def test_cli_roundtrip():
    p = subprocess.run([sys.executable, "-m", "boster", "compress", "--stats"],
                       input="x\n" + "Using cached a\n" * 20, capture_output=True, text=True)
    assert p.returncode == 0 and "savings_pct" in p.stderr
