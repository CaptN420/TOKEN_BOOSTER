"""Phase KV CACHE 4 — append-only memory architecture.

`AUDIT.md`        — the mandatory audit produced before any modification.
`payloads.py`     — the four compared configurations (raw, phase 2, append-only,
                    append-only + consolidation) as real request payloads.
`run_phase4.py`   — the measurement campaign against a real engine.
`analyse.py`      — aggregation, with engine counters kept separate from
                    estimates.
`quality.py`      — memory-retention checks (never trade information for cache).
`test_phase4.py`  — the append-only contract and the journal edge cases.

The append-only implementation itself lives in `boster/memory/append_only.py`.
"""
