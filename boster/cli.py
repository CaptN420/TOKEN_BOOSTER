"""boster CLI: compress | history | budget | count"""
from __future__ import annotations
import argparse, json, sys

from . import (BudgetConfig, ContextBudget, compress_history, compress_output,
               estimate_provider_tokens, format_compressed_history, savings_report)


def _read(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _load_messages(path):
    data = json.loads(_read(path))
    return data["messages"] if isinstance(data, dict) else data


def cmd_compress(a):
    raw = _read(a.file)
    out = compress_output(raw, fmt=a.format, max_lines=a.max_lines, max_chars=a.max_chars)
    sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if a.stats:
        print(json.dumps(savings_report(raw, out)), file=sys.stderr)


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
    print(estimate_provider_tokens(_read(a.file), a.model))


def main(argv=None):
    p = argparse.ArgumentParser(prog="boster", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compress", help="strip noise from tool/CLI output (stdin or file)")
    c.add_argument("file", nargs="?"); c.add_argument("-f", "--format")
    c.add_argument("--max-lines", type=int); c.add_argument("--max-chars", type=int)
    c.add_argument("--stats", action="store_true", help="savings JSON on stderr")
    c.set_defaults(func=cmd_compress)
    h = sub.add_parser("history", help="summarize old chat turns (JSON list of {role,content})")
    h.add_argument("file", nargs="?"); h.add_argument("--keep", type=int, default=10)
    h.set_defaults(func=cmd_history)
    b = sub.add_parser("budget", help="select messages within a token budget")
    b.add_argument("file", nargs="?"); b.add_argument("--total", type=int, default=30000)
    b.add_argument("--query"); b.add_argument("--model", default=""); b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_budget)
    n = sub.add_parser("count", help="estimate tokens of text")
    n.add_argument("file", nargs="?"); n.add_argument("--model", default="")
    n.set_defaults(func=cmd_count)
    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
