"""CLI commands — extended with new subcommands."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

# Backwards-compatible imports
from .. import (
    BudgetConfig,
    ContextBudget,
    PipelineResult,
    adaptive_compress,
    compress_history,
    compress_output,
    estimate_provider_tokens,
    format_compressed_history,
    run_pipeline,
    savings_report,
    safe_compress,
)
from ..core.engine import Boster, OptimizeResult
from ..core.tokenizer import count_tokens
from ..core.token_metrics import cost_estimate, token_density
from ..context.assembler import ContextAssembler
from ..memory.manager import MemoryManager
from ..memory.retrieval import hybrid_search
from ..core.optimizer import optimize_prompt


def _read(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _load_messages(path):
    data = json.loads(_read(path))
    return data["messages"] if isinstance(data, dict) else data


# ── Existing commands ────────────────────────────────────────────────

def cmd_compress(a):
    raw = _read(a.file)
    out = compress_output(raw, fmt=a.format, max_lines=a.max_lines, max_chars=a.max_chars)
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if a.stats:
        print(json.dumps(savings_report(raw, out)), file=sys.stderr)


def cmd_adaptive(a):
    raw = _read(a.file)
    out, report = adaptive_compress(raw, max_chars=a.max_chars or 4000)
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    print(json.dumps(report, indent=2), file=sys.stderr)


def cmd_history(a):
    msgs = _load_messages(a.file)
    s = compress_history(msgs, max_exchanges=a.keep, preserve_last=min(a.keep, 3))
    print(format_compressed_history(s, msgs[-a.keep:], max_recent=min(a.keep, 3)))


def cmd_budget(a):
    msgs = _load_messages(a.file)
    cfg = BudgetConfig(total=a.total, history=a.total // 3)
    r = ContextBudget(model=a.model, config=cfg).select(messages=msgs, user_query=a.query or "", dry_run=True)
    if a.json:
        print(json.dumps(r.selected_messages, ensure_ascii=False))
    else:
        print(r.report(), file=sys.stderr)
        print(json.dumps(r.selected_messages, ensure_ascii=False, indent=1))


def cmd_count(a):
    text = _read(a.file)
    tokens = count_tokens(text, a.model)
    density = token_density(text)
    if a.json:
        print(json.dumps({"tokens": tokens, "density": round(density, 2), "model": a.model}, indent=2))
    else:
        print(f"Tokens: {tokens:,}")
        print(f"Density: {density:.2f} tokens/100chars")
        if a.model:
            print(f"Model: {a.model}")


def cmd_pipeline(a):
    data = json.loads(_read(a.file))
    messages = data["messages"] if isinstance(data, dict) else data
    system = data.get("system_prompt", "")
    r = run_pipeline(
        messages=messages,
        system_prompt=system,
        max_total_tokens=a.total,
        compress_output_msgs=not a.no_compress,
        summarize_history=not a.no_summarize,
        model=a.model,
    )
    if a.json:
        out = {
            "selected_messages": r.selected_messages,
            "compressed_system": r.compressed_system,
            "total_original_tokens": r.total_original_tokens,
            "total_selected_tokens": r.total_selected_tokens,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(r.report())


# ── New commands ─────────────────────────────────────────────────────

def cmd_optimize(a):
    """New: full optimization pipeline with Boster engine."""
    data = json.loads(_read(a.file))
    messages = data["messages"] if isinstance(data, dict) else data
    system = data.get("system_prompt", "")
    query = a.query or ""

    engine = Boster(
        model=a.model or "",
        max_context_tokens=a.total or 32000,
        compression_level=a.level if hasattr(a, 'level') and a.level is not None else 2,
    )

    result = engine.optimize(messages=messages, query=query)
    print(f"Tokens before: {result.tokens_before:,}")
    print(f"Tokens after:  {result.tokens_after:,}")
    print(f"Saved:         {result.tokens_saved:,} ({result.savings_percentage:.1f}%)")
    if result.cost_estimate_usd > 0:
        print(f"Est. cost:     ${result.cost_estimate_usd:.4f}")
    if a.json:
        print(json.dumps({
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "tokens_saved": result.tokens_saved,
            "savings_pct": result.savings_percentage,
            "optimized_messages": result.optimized_messages,
        }, indent=2, ensure_ascii=False))


def cmd_analyze(a):
    """New: analyze context composition."""
    data = json.loads(_read(a.file))
    messages = data["messages"] if isinstance(data, dict) else data
    system = data.get("system_prompt", "")

    assembler = ContextAssembler(max_total_tokens=a.total or 32000, model=a.model or "")
    context = assembler.assemble(
        system_prompt=system,
        messages=messages,
    )

    print(f"Total tokens: {context.total_tokens:,}")
    print(f"Messages:     {len(context.messages)}")
    for cat, tok in context.token_breakdown.items():
        pct = (tok / max(context.total_tokens, 1)) * 100
        print(f"  {cat:12s}: {tok:>6,d} ({pct:.1f}%)")
    if a.json:
        print(json.dumps(context.to_dict(), indent=2, ensure_ascii=False))


def cmd_memory(a):
    """New: memory operations."""
    mgr = MemoryManager()

    if a.memory_action == "add":
        content = _read(a.file) if a.file else sys.stdin.read()
        entry = mgr.add(content, category=a.category or "", importance=a.importance or 0.5)
        print(f"Added: {entry.id}")
    elif a.memory_action == "search":
        query = a.query or ""
        if not query:
            print("Usage: boster memory search --query 'keyword'", file=sys.stderr)
            return
        results = hybrid_search(query, mgr, max_results=a.max_results or 10)
        for entry in results.entries:
            print(f"[{entry.category}] {entry.content[:100]}...")
    elif a.memory_action == "summary":
        print(json.dumps(mgr.get_summary(), indent=2))
    elif a.memory_action == "list":
        for entry in mgr.get_all():
            print(f"  [{entry.category}] {entry.content[:80]}")
    else:
        print("Usage: boster memory <add|search|summary|list>", file=sys.stderr)


def cmd_prompt_optimize(a):
    """New: optimize a system prompt."""
    prompt = _read(a.file) if a.file else sys.stdin.read()
    optimized = optimize_prompt(prompt, remove_duplicates=not a.no_dedup)
    print(optimized)
    if a.stats:
        saved = len(prompt) - len(optimized)
        print(f"\nSavings: {saved} chars ({(saved/max(len(prompt),1))*100:.1f}%)", file=sys.stderr)


def cmd_stats(a):
    """New: show global statistics."""
    # Read from savings log if available
    import os
    log_path = os.path.join(os.environ.get("HERMES_HOME", str(Path.home())), "logs", "boster_savings.jsonl")
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            lines = f.readlines()
        print(f"Total events: {len(lines)}")
        if lines:
            total_saved = sum(
                json.loads(l).get("saved_pct", 0) * json.loads(l).get("before", 1) / 100
                for l in lines if json.loads(l).get("before", 0) > 0
            )
            print(f"Total tokens saved: ~{total_saved:,.0f}")
    else:
        print("No savings log found.")


from pathlib import Path


def main(argv=None):
    p = argparse.ArgumentParser(prog="boster", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    # Existing commands
    c = sub.add_parser("compress", help="strip noise from tool/CLI output")
    c.add_argument("file", nargs="?"); c.add_argument("-f", "--format")
    c.add_argument("--max-lines", type=int); c.add_argument("--max-chars", type=int)
    c.add_argument("--stats", action="store_true")
    c.set_defaults(func=cmd_compress)

    h = sub.add_parser("history", help="summarize old chat turns")
    h.add_argument("file", nargs="?"); h.add_argument("--keep", type=int, default=10)
    h.set_defaults(func=cmd_history)

    b = sub.add_parser("budget", help="select messages within token budget")
    b.add_argument("file", nargs="?"); b.add_argument("--total", type=int, default=30000)
    b.add_argument("--query"); b.add_argument("--model", default=""); b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_budget)

    n = sub.add_parser("count", help="estimate tokens of text")
    n.add_argument("file", nargs="?"); n.add_argument("--model", default="")
    n.add_argument("--json", action="store_true")
    n.set_defaults(func=cmd_count)

    a2 = sub.add_parser("adaptive", help="smart compression for long text")
    a2.add_argument("file", nargs="?")
    a2.add_argument("--max-chars", type=int, default=4000)
    a2.set_defaults(func=cmd_adaptive)

    p2 = sub.add_parser("pipeline", help="full pipeline: compress + summarize + budget")
    p2.add_argument("file", nargs="?")
    p2.add_argument("--total", type=int, default=20000)
    p2.add_argument("--model", default="")
    p2.add_argument("--no-compress", action="store_true")
    p2.add_argument("--no-summarize", action="store_true")
    p2.add_argument("--json", action="store_true")
    p2.set_defaults(func=cmd_pipeline)

    # New commands
    o = sub.add_parser("optimize", help="full optimization with Boster engine")
    o.add_argument("file", nargs="?")
    o.add_argument("--total", type=int, default=32000)
    o.add_argument("--model", default="")
    o.add_argument("--query", default="")
    o.add_argument("--level", type=int, default=2)
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_optimize)

    an = sub.add_parser("analyze", help="analyze context composition")
    an.add_argument("file", nargs="?")
    an.add_argument("--total", type=int, default=32000)
    an.add_argument("--model", default="")
    an.add_argument("--json", action="store_true")
    an.set_defaults(func=cmd_analyze)

    m = sub.add_parser("memory", help="memory operations")
    m.add_argument("action", choices=["add", "search", "summary", "list"])
    m.add_argument("file", nargs="?")
    m.add_argument("--query", default="")
    m.add_argument("--category", default="")
    m.add_argument("--importance", type=float, default=0.5)
    m.add_argument("--max-results", type=int, default=10)
    m.set_defaults(func=cmd_memory)

    po = sub.add_parser("prompt-optimize", help="optimize system prompt")
    po.add_argument("file", nargs="?")
    po.add_argument("--no-dedup", action="store_true")
    po.add_argument("--stats", action="store_true")
    po.set_defaults(func=cmd_prompt_optimize)

    st = sub.add_parser("stats", help="show global statistics")
    st.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    if hasattr(args, "func"):
        args.func(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
