import json, subprocess, sys
from boster import (BudgetConfig, ContextBudget, PipelineResult, adaptive_compress,
                    compress_history, compress_output, format_compressed_history,
                    savings_report, safe_compress, estimate_provider_tokens, run_pipeline)


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


# ── New tests for v0.2.0 ─────────────────────────────────────────────

def test_adaptive_compress_json():
    json_text = json.dumps({"a": list(range(50)), "b": "x" * 200}, indent=2)
    out, report = adaptive_compress(json_text, max_chars=200)
    assert report["original"] == len(json_text)
    assert report["savings_pct"] > 0


def test_adaptive_compress_long_generic():
    long_text = "\n".join(f"line {i} with some content" for i in range(200))
    out, report = adaptive_compress(long_text, max_chars=500)
    assert len(out) <= len(long_text)
    assert report["savings_pct"] > 0


def test_adaptive_compress_preserves_errors():
    text = "\n".join([f"normal line {i}" for i in range(50)] + ["ERROR: something broke", "Traceback: ..."])
    out, _ = adaptive_compress(text, max_chars=300)
    assert "ERROR" in out


def test_pipeline_basic():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    r = run_pipeline(messages=messages, max_total_tokens=5000)
    assert len(r.selected_messages) > 0
    assert r.total_selected_tokens < r.total_original_tokens or r.total_original_tokens == 0


def test_pipeline_with_long_history():
    messages = [{"role": "user", "content": f"message {i} with lots of padding " * 20} for i in range(30)]
    messages += [{"role": "assistant", "content": f"reply {i}"} for i in range(30)]
    r = run_pipeline(messages=messages, max_total_tokens=2000, summarize_history=True)
    assert len(r.selected_messages) < len(messages)


def test_adaptive_compress_docker():
    docker_out = "\n".join([
        "Step 1/5 : FROM ubuntu",
        " ---> abc123",
        "Step 2/5 : RUN apt-get update",
        " ---> Using cache",
        "Step 3/5 : COPY . /app",
        "Successfully built abc123",
    ] * 5)
    out, report = adaptive_compress(docker_out, max_chars=200)
    assert "Step" in out or "Successfully" in out


def test_adaptive_compress_diff():
    diff_text = (
        "diff --git a/file.py b/file.py\n"
        "index 123..456 100644\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old line\n"
        "+new line\n"
        " context\n"
    ) * 10
    out, report = adaptive_compress(diff_text, max_chars=200)
    assert "diff" in out or "@@" in out

