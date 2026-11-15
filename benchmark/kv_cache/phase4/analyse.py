"""Phase-4 aggregation.

Every cache figure here comes from the engine's own counters
(``timings.cache_n`` / ``timings.prompt_n`` and the cumulative
``llamacpp:prompt_tokens_cached_total``).  Nothing is derived from a textual
similarity: the text LCP column is kept **next to** the real reuse column
precisely so the gap between them stays visible.

A single 200-turn run per configuration yields every size window (25/50/100/200):
the payload at turn *t* is identical whether the campaign stops at 25 or at 200
turns, so the windows are slices of the same measurement, not separate runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

RESULTS_DIR = Path(__file__).parent / "results"
WINDOWS: Sequence[int] = (25, 50, 100, 200)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reuse_pct(evaluated: Optional[int], cached: Optional[int]) -> Optional[float]:
    if evaluated is None or cached is None:
        return None
    total = evaluated + cached
    return round(100.0 * cached / total, 1) if total else None


def window_totals(records: Sequence[Dict[str, Any]], upto: Optional[int] = None) -> Dict[str, Any]:
    """Engine-reported totals over the turns up to ``upto`` (all of them if None)."""
    kept = [r for r in records if upto is None or r.get("turn", 1e9) <= upto]
    if not kept:
        return {}
    results = [r["result"] for r in kept]
    evaluated = [r.get("tokens_evaluated") for r in results]
    cached = [r.get("tokens_cached") for r in results]
    have_cache = all(c is not None for c in cached)
    prefill = [r.get("prefill_ms_engine") for r in results]

    # the first turn of a configuration pays the whole prompt: reported apart,
    # because including it in a reuse rate hides the steady state
    steady = kept[1:] if len(kept) > 1 else []
    steady_eval = sum((r["result"].get("tokens_evaluated") or 0) for r in steady)
    steady_cache = sum((r["result"].get("tokens_cached") or 0) for r in steady)

    return {
        "config": kept[0].get("config"),
        "turns": len(kept),
        "prompt_chars_last": kept[-1].get("prompt_chars"),
        "prompt_chars_first": kept[0].get("prompt_chars"),
        "block_chars_last": kept[-1].get("block_chars"),
        "prompt_n_sum": sum(v or 0 for v in evaluated),
        "cache_n_sum": sum(cached) if have_cache else None,
        "reuse_pct": reuse_pct(sum(v or 0 for v in evaluated),
                               sum(cached) if have_cache else None),
        "reuse_pct_steady": (reuse_pct(steady_eval, steady_cache)
                             if have_cache and steady else None),
        "prefill_ms_sum": round(sum(v or 0 for v in prefill), 1) if any(prefill) else None,
        "wall_s_sum": round(sum(r.get("wall_s") or 0 for r in results), 3),
        "prompt_tokens_engine_sum": sum(
            (r.get("prompt_tokens_engine") or 0) for r in results) or None,
        "first_turn_cached": results[0].get("tokens_cached"),
        "engine": kept[0].get("engine"),
    }


def sequence_table(records: Sequence[Dict[str, Any]],
                   windows: Sequence[int] = WINDOWS) -> List[Dict[str, Any]]:
    """One row per (configuration, window)."""
    rows: List[Dict[str, Any]] = []
    for upto in windows:
        row = window_totals(records, upto)
        if row:
            row["window"] = upto
            rows.append(row)
    return rows


def format_sequence_table(rows: Sequence[Dict[str, Any]]) -> str:
    lines = ["| config | tours | tokens envoyes | reevalues (moteur) | reutilises (moteur) | "
             "reutilisation | reutilisation (hors 1er tour) | prefill reel |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row['config']} | {row['turns']} | {row['prompt_tokens_engine_sum'] or 'n/d'} | "
            f"{row['prompt_n_sum']} | {row['cache_n_sum'] if row['cache_n_sum'] is not None else 'n/d'} | "
            f"{row['reuse_pct'] if row['reuse_pct'] is not None else 'n/d'} % | "
            f"{row['reuse_pct_steady'] if row['reuse_pct_steady'] is not None else 'n/d'} % | "
            f"{row['prefill_ms_sum']} ms |")
    return "\n".join(lines)


def growth_table(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prompt size, journal size and entry count per window (payload side)."""
    rows: List[Dict[str, Any]] = []
    for upto in WINDOWS:
        kept = [r for r in records if r.get("turn", 1e9) <= upto]
        if not kept:
            continue
        last = kept[-1]
        memory = (last.get("meta") or {}).get("memory") or {}
        rows.append({
            "config": last.get("config"), "window": upto,
            "prompt_chars": last.get("prompt_chars"),
            "block_chars": last.get("block_chars"),
            "journal_chars": memory.get("journal_chars"),
            "entries": memory.get("entries"),
            "entries_archived": memory.get("entries_archived"),
            "subjects": memory.get("subjects"),
            "superseded": memory.get("superseded"),
            "generation": memory.get("generation"),
            "block_tokens": memory.get("block_tokens"),
            "token_kind": memory.get("token_kind"),
        })
    return rows


def format_growth_table(rows: Sequence[Dict[str, Any]]) -> str:
    lines = ["| config | tours | prompt (c) | bloc (c) | journal (c) | entrees | sujets | "
             "renversees | generation |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(
            f"| {row['config']} | {row['window']} | {row['prompt_chars']} | {row['block_chars']} | "
            f"{row['journal_chars'] if row['journal_chars'] is not None else 'n/d'} | "
            f"{row['entries'] if row['entries'] is not None else 'n/d'} | "
            f"{row['subjects'] if row['subjects'] is not None else 'n/d'} | "
            f"{row['superseded'] if row['superseded'] is not None else 'n/d'} | "
            f"{row['generation'] if row['generation'] is not None else 'n/d'} |")
    return "\n".join(lines)


def events_table(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Text similarity next to the engine's real reuse, per event."""
    rows: List[Dict[str, Any]] = []
    for record in records:
        if record.get("event") == "consolidation":
            continue
        result = record.get("event_result") or {}
        warm = record.get("warm") or {}
        evaluated, cached = result.get("tokens_evaluated"), result.get("tokens_cached")
        rows.append({
            "config": record.get("config"), "event": record.get("event"),
            "text_lcp_pct": record.get("text_lcp_pct"),
            "reuse_pct": reuse_pct(evaluated, cached),
            "prompt_n": evaluated, "cache_n": cached,
            "warm_cache_n": warm.get("tokens_cached"),
            "prefill_ms": result.get("prefill_ms_engine"),
            "prompt_chars": record.get("event_prompt_chars"),
        })
    return rows


def format_events_table(rows: Sequence[Dict[str, Any]]) -> str:
    lines = ["| config | evenement | texte LCP | reutilisation reelle | ecart | cache_n | prefill |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        gap = (round(row["text_lcp_pct"] - row["reuse_pct"], 1)
               if row["text_lcp_pct"] is not None and row["reuse_pct"] is not None else None)
        lines.append(
            f"| {row['config']} | {row['event']} | "
            f"{row['text_lcp_pct'] if row['text_lcp_pct'] is not None else 'n/d'} % | "
            f"{row['reuse_pct'] if row['reuse_pct'] is not None else 'n/d'} % | "
            f"{gap if gap is not None else 'n/d'} | "
            f"{row['cache_n'] if row['cache_n'] is not None else 'n/d'} | "
            f"{row['prefill_ms'] if row['prefill_ms'] is not None else 'n/d'} ms |")
    return "\n".join(lines)


def consolidation_rows(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for record in records:
        if record.get("event") != "consolidation":
            continue
        result = record.get("event_result") or {}
        warm = record.get("warm") or {}
        rows.append({
            "config": record.get("config"), "done": record.get("done"),
            "turn": record.get("turn"),
            "prefix_grew_chars": (record.get("consolidation") or {}).get("prefix_grew_chars"),
            "archived_entries": (record.get("consolidation") or {}).get("archived_entries"),
            "before_prompt_chars": record.get("before_prompt_chars"),
            "after_prompt_chars": record.get("after_prompt_chars"),
            "prompt_n": result.get("tokens_evaluated"),
            "cache_n": result.get("tokens_cached"),
            "reuse_pct": reuse_pct(result.get("tokens_evaluated"), result.get("tokens_cached")),
            "warm_cache_n": warm.get("tokens_cached"),
            "prefill_ms": result.get("prefill_ms_engine"),
        })
    return rows


def check_metric_cross_check(records: Sequence[Dict[str, Any]],
                             delta: Dict[str, Any]) -> Dict[str, Any]:
    """The cumulative /metrics delta must equal the sum of the per-request counters."""
    row = window_totals(records, None)
    if not row:
        return {}
    reported_cached = delta.get("prompt_tokens_cached_total")
    reported_prompt = delta.get("prompt_tokens_total")
    return {
        "metrics_prompt_tokens": reported_prompt,
        "sum_prompt_n": row["prompt_n_sum"],
        "metrics_cached": reported_cached,
        "sum_cache_n": row["cache_n_sum"],
        "prompt_match": (None if reported_prompt is None else reported_prompt == row["prompt_n_sum"]),
        "cached_match": (None if reported_cached is None else reported_cached == row["cache_n_sum"]),
    }


def summarise_tag(tag: str, configs: Sequence[str], results_dir: Path = RESULTS_DIR,
                  mode: str = "sequence") -> Dict[str, Any]:
    summary: Dict[str, Any] = {"tag": tag, "mode": mode, "configs": {}}
    for config in configs:
        path = results_dir / f"{tag}_{mode}_{config}.jsonl"
        records = load_jsonl(path)
        if not records:
            continue
        entry: Dict[str, Any] = {"records": len(records)}
        if mode == "sequence":
            entry["windows"] = sequence_table(records)
            entry["growth"] = growth_table(records)
        else:
            entry["events"] = events_table(records)
            entry["consolidation"] = consolidation_rows(records)
        summary["configs"][config] = entry
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 aggregation")
    parser.add_argument("--tag", default="main")
    parser.add_argument("--configs", default="raw,boster_phase2,boster_append,boster_append_consolidated")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    run_summary = {}
    summary_path = RESULTS_DIR / f"summary_{args.tag}.json"
    if summary_path.exists():
        run_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    deltas = {c: (run_summary.get("configs", {}).get(c, {}) or {}).get("engine_metrics_delta", {})
              for c in configs}

    print(f"### Campagne « {args.tag} » — moteur {run_summary.get('engine', '?')}\n")
    out: Dict[str, Any] = {"tag": args.tag, "engine": run_summary.get("engine"),
                           "configs": {}, "cross_check": {}, "windows": list(WINDOWS)}

    for config in configs:
        path = RESULTS_DIR / f"{args.tag}_sequence_{config}.jsonl"
        records = load_jsonl(path)
        if not records:
            continue
        rows = sequence_table(records)
        growth = growth_table(records)
        out["configs"][config] = {"windows": rows, "growth": growth}
        print(f"#### {config} — sequences\n")
        print(format_sequence_table(rows))
        print()
        print(format_growth_table(growth))
        print()
        check = check_metric_cross_check(records, deltas.get(config, {}))
        if check:
            out["cross_check"][config] = check
            print(f"recoupement /metrics pour {config} : "
                  f"prompt {check['metrics_prompt_tokens']} vs somme {check['sum_prompt_n']} "
                  f"(egal : {check['prompt_match']}) ; "
                  f"cache {check['metrics_cached']} vs somme {check['sum_cache_n']} "
                  f"(egal : {check['cached_match']})\n")

        events_path = RESULTS_DIR / f"{args.tag}_events_{config}.jsonl"
        event_records = load_jsonl(events_path)
        if event_records:
            event_rows = events_table(event_records)
            consolidation = consolidation_rows(event_records)
            out["configs"][config]["events"] = event_rows
            out["configs"][config]["consolidation"] = consolidation
            print(f"#### {config} — evenements\n")
            print(format_events_table(event_rows))
            if consolidation:
                print()
                print(json.dumps(consolidation, indent=2, ensure_ascii=False))
            print()

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"agregation ecrite : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
