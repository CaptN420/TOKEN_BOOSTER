"""Real-engine probe for the KV-cache audit (Phase KV CACHE 1, sections 5 and 6).

Strictly non-intrusive with respect to the engine:

* read-only GETs to discover what the running server exposes;
* a handful of minimal completion requests (``max_tokens=1``) to obtain the
  engine's OWN token accounting and a wall-clock prefill latency.

No engine setting, preset, model parameter or server flag is touched. Nothing is
installed, nothing is written outside this package.

What this probe can and cannot establish
----------------------------------------
* CAN: the number of prompt tokens the engine reports for a request, and how long
  the request took, repeated on identical and on slightly-different prompts.
* CANNOT: how many tokens the engine *reused* from a previous request. This
  LM Studio build exposes no cache counter (``stats`` is empty, ``usage`` has no
  ``prompt_tokens_details``), so a faster repeat request is a *correlation*, not a
  confirmed cache hit. The report says so explicitly; no number here is presented
  as a cache hit.

Run:  PYTHONPATH="." uv run python -m benchmark.kv_cache.engine_probe
      PYTHONPATH="." uv run python -m benchmark.kv_cache.engine_probe --exchanges 100
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from boster.core.tokenizer import count_tokens
from boster.deterministic_pipeline import (
    CONTEXT_BLOCK_HEADER,
    PipelineConfig,
    run_deterministic_pipeline,
)

from .conversation import SYSTEM_PROMPT, as_messages, build_conversation, split_system
from .prefix_stability import lcp

DEFAULT_BASE_URL = "http://127.0.0.1:1234"
DEFAULT_MODEL = "jev-style-0.8b-decision-v3"
READ_ONLY_PATHS = ("/v1/models", "/api/v0/models", "/stats", "/props", "/health")
REQUEST_FIELDS = ("usage", "stats", "timings")


@dataclass
class Observation:
    label: str
    prompt_tokens_engine: Optional[int]
    latency_s: float
    reused_tokens_reported: Optional[int]
    keys: Tuple[str, ...]
    detail: Dict[str, Any] = field(default_factory=dict)


def get_json(base_url: str, path: str, timeout: int = 15) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=timeout) as response:
            return {"status": response.status, "body": json.loads(response.read().decode("utf-8", "replace"))}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "body": error.read().decode("utf-8", "replace")[:200]}
    except Exception as error:  # noqa: BLE001 - a missing server must not raise
        return {"status": None, "error": f"{type(error).__name__}: {error}"}


def complete(base_url: str, model: str, messages: Sequence[Dict[str, str]],
             max_tokens: int = 1, timeout: int = 300) -> Tuple[Dict[str, Any], float]:
    payload = {"model": model, "messages": list(messages), "max_tokens": max_tokens,
               "stream": False}
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    return body, time.perf_counter() - started


def discover(base_url: str) -> Dict[str, Any]:
    """What the running server exposes, without sending a single inference request."""
    return {path: get_json(base_url, path) for path in READ_ONLY_PATHS}


def _reused_tokens(body: Dict[str, Any]) -> Optional[int]:
    """Any engine-reported count of reused/prompt-cached tokens, or None."""
    usage = body.get("usage") or {}
    for key in ("cached_tokens", "prompt_cached_tokens", "reused_tokens", "cache_n"):
        if key in usage:
            return int(usage[key])
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and key in details:
            return int(details[key])
    timings = body.get("timings") or {}
    if isinstance(timings, dict) and "cache_n" in timings:
        return int(timings["cache_n"])
    return None


def observe(base_url: str, model: str, label: str,
            messages: Sequence[Dict[str, str]]) -> Observation:
    body, latency = complete(base_url, model, messages)
    usage = body.get("usage") or {}
    return Observation(
        label=label,
        prompt_tokens_engine=usage.get("prompt_tokens"),
        latency_s=round(latency, 3),
        reused_tokens_reported=_reused_tokens(body),
        keys=tuple(sorted(key for key in REQUEST_FIELDS if key in body)),
        detail={"stats": body.get("stats"), "timings": body.get("timings"),
                "usage": usage},
    )


def boster_requests(exchanges: int = 100, scenario: str = "B",
                    conversation_id: str = "kv-probe") -> List[Tuple[int, List[Dict[str, str]], str]]:
    """The exact request Boster produces at the last few turns of a conversation."""
    turns, _ = build_conversation(exchanges, scenario=scenario)
    built: List[Tuple[int, List[Dict[str, str]], str]] = []
    for turn in turns:
        if turn.index < exchanges - 1:
            continue
        messages = as_messages(turns[: turn.index])
        output = run_deterministic_pipeline(
            messages, system_prompt=SYSTEM_PROMPT, query=turn.question,
            config=PipelineConfig(max_tokens=2000, level="balanced",
                                  conversation_id=conversation_id,
                                  use_memory=True, reconstruct=True))
        system = output.system_prompt
        payload = [{"role": "system", "content": system}]
        for message in output.messages:
            payload.append({"role": str(message.get("role", "user")),
                            "content": str(message.get("content", ""))})
        built.append((turn.index, payload, system))
    return built


def main(argv: Sequence[str]) -> int:
    base_url = DEFAULT_BASE_URL
    exchanges = 100
    model = DEFAULT_MODEL
    if "--exchanges" in argv:
        exchanges = int(argv[argv.index("--exchanges") + 1])
    if "--base-url" in argv:
        base_url = argv[argv.index("--base-url") + 1]
    if "--model" in argv:
        model = argv[argv.index("--model") + 1]

    print(f"engine: {base_url}   modele: {model}")
    print("== decouverte (GET, aucune inference) ==")
    for path, result in discover(base_url).items():
        status = result.get("status")
        body = result.get("body")
        summary = ""
        if isinstance(body, dict) and "data" in body:
            summary = f"{len(body['data'])} modeles"
        elif isinstance(body, dict) and "error" in body:
            summary = str(body["error"])[:60]
        elif isinstance(body, str):
            summary = body[:60]
        print(f"  GET {path:<16} -> {status}  {summary}")

    print("\n== requetes Boster reelles (derniers tours) ==")
    requests = boster_requests(exchanges)
    if not requests:
        print("  aucune requete construite")
        return 0
    observations: List[Observation] = []
    for index, (turn, payload, system) in enumerate(requests):
        instructions, block = split_system(system, CONTEXT_BLOCK_HEADER)
        prompt_text = system + "".join(m["content"] for m in payload[1:])
        print(f"  tour {turn}: systeme={len(system)}c (bloc={len(block)}c) "
              f"messages={len(payload) - 1} prompt_est={count_tokens(prompt_text, '')} tokens")
        for repetition in (1, 2):
            observation = observe(base_url, model, f"tour{turn}#{repetition}", payload)
            observations.append(observation)
            print(f"      #{repetition} prompt_tokens(moteur)={observation.prompt_tokens_engine} "
                  f"latence={observation.latency_s}s reuse_reporte={observation.reused_tokens_reported} "
                  f"champs={observation.keys}")

    if len(requests) >= 2:
        first_system = requests[0][2]
        second_system = requests[1][2]
        common = lcp(first_system, second_system)
        print(f"\n  LCP des deux systemes = {common}c "
              f"({round(100.0 * common / max(len(first_system), 1), 1)}% du tour precedent)")
        print(f"  LCP des deux systemes, bloc inclus : "
              f"{lcp(requests[0][2], requests[1][2])}c")

    print("\n== lecture ==")
    reported = [o.reused_tokens_reported for o in observations]
    if all(value is None for value in reported):
        print("  Le moteur ne rapporte AUCUN compteur de tokens reutilises :")
        print("  -> reutilisation du KV cache : NON VERIFIEE (aucune preuve moteur).")
        print("  Les latences ci-dessus sont une correlation, jamais un cache hit confirme.")
        if len(observations) >= 2:
            first, second = observations[0], observations[1]
            ratio = round(second.latency_s / max(first.latency_s, 1e-6), 3)
            print(f"  Observation (indicative): requete identique rejouee -> {ratio}x la latence "
                  f"initiale, prompt_tokens={second.prompt_tokens_engine}")
    else:
        print(f"  Compteurs de reutilisation rapportes par le moteur : {reported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
