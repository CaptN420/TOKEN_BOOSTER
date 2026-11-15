"""Phase-4 campaign driver.

Borrows the whole measurement rig from phase 3 — the engine adapters, the cache
reset, the JSONL format, the warm-before-measure protocol — so a phase-4 number
and a phase-3 number come from the same instrumentation.  Only the payloads
differ.

Modes
-----
``sequence``  one real request per turn, for every configuration.
``events``    one isolated transition per event (question identical, question
              different, fact added, fact corrected, new decision, decision
              changed, relation, constraint, tight budget, consolidation), each
              measured on top of a warm turn.

The cache is reset before every configuration: a run that starts on someone
else's warm prefix is not a measurement.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..phase3.engines import (DEFAULT_LLAMACPP_URL, DEFAULT_LMSTUDIO_URL,
                              CompletionResult, LlamaCppEngine, LMStudioEngine)
from ..phase3.run_phase3 import engine_metrics, make_engine, reset_cache, warm_cache, write_jsonl
from .payloads import CONFIGS, build_append_run, build_sequence

RESULTS_DIR = Path(__file__).parent / "results"

#: event -> (scenario, forced question or None, warm kind)
EVENTS: Dict[str, Tuple[str, Optional[str], str]] = {
    "question_identique": ("B", None, "same"),
    "question_differente": ("F", None, "previous"),
    "fait_ajoute": ("B", None, "previous"),
    "fait_corrige": ("E", None, "previous"),
    "decision_nouvelle": ("D", None, "previous"),
    "decision_modifiee": ("F", None, "previous"),
    "relation": ("B", None, "previous"),
    "contrainte": ("B", None, "previous"),
    "budget_serre": ("H", None, "previous"),
}


def complete(engine: Any, engine_name: str, prompt: str, n_predict: int = 1) -> CompletionResult:
    if engine_name == "lmstudio":
        return engine.complete(prompt, max_tokens=n_predict)
    return engine.complete(prompt, n_predict=n_predict)


def run_sequence(engine: Any, engine_name: str, payloads: Sequence[Any],
                 n_predict: int = 1, verbose: bool = True) -> List[Dict[str, Any]]:
    """One request per turn; the payload's own phase-4 evidence is kept."""
    records: List[Dict[str, Any]] = []
    for position, payload in enumerate(payloads, start=1):
        result = complete(engine, engine_name, payload.prompt, n_predict)
        record = {
            "engine": engine_name, "config": payload.config,
            "scenario": payload.scenario, "turn": payload.turn,
            "prompt_chars": payload.prompt_chars, "block_chars": payload.block_chars,
            "block_frozen": payload.block_frozen, "result": result.to_dict(),
            "meta": payload.meta,
        }
        records.append(record)
        if verbose and (position % 20 == 0 or position == len(payloads)):
            print(f"    tour {position:>3}/{len(payloads)}  prompt={result.prompt_chars}c  "
                  f"eval={result.tokens_evaluated}  cache={result.tokens_cached}  "
                  f"prefill={result.prefill_ms_engine}ms  wall={result.wall_s}s", flush=True)
    return records


def run_events(engine: Any, engine_name: str, config: str, exchanges: int,
               append_budget_total: int = 0, n_predict: int = 1,
               verbose: bool = True) -> List[Dict[str, Any]]:
    """One isolated transition per event, always warm-then-measured."""
    from ..prefix_stability import lcp

    records: List[Dict[str, Any]] = []
    cache: Dict[Tuple[str, int], List[Any]] = {}
    for event, (scenario, forced_question, warm_kind) in EVENTS.items():
        key = (scenario, 0)
        if key not in cache:
            cache[key] = build_sequence(config, scenario=scenario, exchanges=exchanges,
                                        append_budget_total=append_budget_total)
        sequence = cache[key]
        if not sequence:
            continue
        event_payload = sequence[-1]
        warm_payload = event_payload if warm_kind == "same" else (
            sequence[-2] if len(sequence) > 1 else sequence[-1])
        warm_result = warm_cache(engine, engine_name, warm_payload, n_predict)
        result = complete(engine, engine_name, event_payload.prompt, n_predict)
        text_lcp = lcp(warm_payload.prompt, event_payload.prompt)
        records.append({
            "engine": engine_name, "config": config, "event": event,
            "scenario": scenario, "exchanges": exchanges, "warm_kind": warm_kind,
            "warm": warm_result, "event_result": result.to_dict(),
            "text_lcp_chars": text_lcp,
            "text_lcp_pct": round(100.0 * text_lcp / max(len(warm_payload.prompt), 1), 2),
            "event_prompt_chars": event_payload.prompt_chars,
            "event_block_chars": event_payload.block_chars,
            "meta": event_payload.meta,
        })
        if verbose:
            print(f"    {event:<20} texte LCP={records[-1]['text_lcp_pct']:>6.2f}%  "
                  f"eval={result.tokens_evaluated}  cache={result.tokens_cached}  "
                  f"prefill={result.prefill_ms_engine}ms", flush=True)
    return records


def run_consolidation_event(engine: Any, engine_name: str, exchanges: int,
                            append_budget_total: int, n_predict: int = 1
                            ) -> List[Dict[str, Any]]:
    """The cost of a consolidation, measured: the turn before and the turn after."""
    run = build_append_run("boster_append_consolidated", scenario="B",
                           exchanges=exchanges, append_budget_total=append_budget_total)
    if not run.consolidations:
        return [{"engine": engine_name, "config": "boster_append_consolidated",
                 "event": "consolidation", "done": False,
                 "note": "aucune consolidation declenchee sur cette plage"}]
    at_turn = int(run.consolidations[0]["turn"])
    before = next((p for p in run.payloads if p.turn == max(1, at_turn - 1)), None)
    after = next((p for p in run.payloads if p.turn == at_turn), None)
    if before is None or after is None:
        return []
    warm = warm_cache(engine, engine_name, before, n_predict)
    result = complete(engine, engine_name, after.prompt, n_predict)
    return [{
        "engine": engine_name, "config": "boster_append_consolidated",
        "event": "consolidation", "done": True, "turn": at_turn,
        "consolidation": run.consolidations[0],
        "warm": warm, "event_result": result.to_dict(),
        "before_prompt_chars": before.prompt_chars,
        "after_prompt_chars": after.prompt_chars,
        "meta": after.meta,
    }]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 4 (append-only) campaign")
    parser.add_argument("--engine", default="llamacpp", choices=["llamacpp", "lmstudio"])
    parser.add_argument("--url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--tag", default="main")
    parser.add_argument("--mode", default="sequence", choices=["sequence", "events"])
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--exchanges", type=int, default=200)
    parser.add_argument("--scenario", default="B")
    parser.add_argument("--append-budget-total", type=int, default=0,
                        help="token budget of the append-only configs (0 = unbounded)")
    parser.add_argument("--n-predict", type=int, default=1)
    parser.add_argument("--no-reset", action="store_true",
                        help="do NOT reset the cache before a configuration")
    args = parser.parse_args(argv)

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    engine = make_engine(args.engine, args.url, args.model)
    report = engine.probe()
    print(f"=== moteur : {args.engine} ===")
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False)[:600])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries: Dict[str, Any] = {}

    if args.mode == "sequence":
        for config in configs:
            if not args.no_reset:
                reset = reset_cache(engine, args.engine, args.n_predict)
                cold = reset.get("tokens_cached")
                print(f"  remise a froid : cache_n={cold}")
            print(f"== sequence : {config} (scenario {args.scenario}, "
                  f"{args.exchanges} tours) ==", flush=True)
            before = engine_metrics(engine)
            payloads = build_sequence(config, scenario=args.scenario,
                                      exchanges=args.exchanges,
                                      append_budget_total=args.append_budget_total)
            records = run_sequence(engine, args.engine, payloads, args.n_predict)
            after = engine_metrics(engine)
            if records:
                first = records[0]["result"].get("tokens_cached")
                if first not in (0, None):
                    print(f"    ATTENTION : le premier tour a cache_n={first} "
                          f"(cache non vide au depart)")
            path = RESULTS_DIR / f"{args.tag}_sequence_{config}.jsonl"
            write_jsonl(path, records)
            summaries[config] = {
                "records": len(records), "file": path.name,
                "engine_metrics_delta": {
                    key: (after.get(key, 0.0) - before.get(key, 0.0))
                    for key in ("prompt_tokens_total", "prompt_tokens_cached_total")
                },
                "bytes_first_turn_cached": records[0]["result"].get("tokens_cached") if records else None,
            }
    else:
        for config in configs:
            if not args.no_reset:
                reset_cache(engine, args.engine, args.n_predict)
            print(f"== evenements : {config} ==", flush=True)
            records = run_events(engine, args.engine, config, args.exchanges,
                                 args.append_budget_total, args.n_predict)
            if config == "boster_append_consolidated":
                records.extend(run_consolidation_event(engine, args.engine,
                                                       args.exchanges,
                                                       args.append_budget_total,
                                                       args.n_predict))
            path = RESULTS_DIR / f"{args.tag}_events_{config}.jsonl"
            write_jsonl(path, records)
            summaries[config] = {"records": len(records), "file": path.name}

    summary_path = RESULTS_DIR / f"summary_{args.tag}.json"
    existing: Dict[str, Any] = {}
    if summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    merged_configs = dict(existing.get("configs") or {})
    merged_configs.update(summaries)
    payload = {
        "engine": args.engine, "mode": args.mode, "exchanges": args.exchanges,
        "scenario": args.scenario, "append_budget_total": args.append_budget_total,
        "cache_reset": not args.no_reset,
        "engine_report": report.to_dict(),
        "configs": merged_configs,
        "runs": (existing.get("runs") or []) + [{
            "mode": args.mode, "configs": configs, "exchanges": args.exchanges,
            "scenario": args.scenario, "append_budget_total": args.append_budget_total,
        }],
    }
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nsynthese ecrite : {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
