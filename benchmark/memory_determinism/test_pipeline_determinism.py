"""Deterministic pipeline: identical input must yield byte-identical output.

The v2.2 consolidation pipeline claimed determinism while stamping every
artifact with ``time.time()`` (``created_at``/``updated_at``), so two runs on
the same conversation produced different JSON.  These tests pin the fix:

  * no wall clock anywhere in the consolidation output,
  * one deterministic stamp per run (newest source timestamp, else
    ``UNKNOWN_TIME``),
  * the same output across processes and across ``PYTHONHASHSEED`` values,
  * a save -> load -> save cycle that is byte-identical.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import boster.memory.consolidation as consolidation
from boster.memory.consolidation import (
    UNKNOWN_TIME,
    consolidate_exchanges,
    consolidate_to_episodes,
    pipeline_stamp,
)
from boster.memory.models import EpisodicMemory
from boster.memory.persistence import MemoryPersistence

REPO_ROOT = Path(__file__).resolve().parents[2]

CONVERSATION = [
    {"id": "m1", "role": "user", "content": "Project Orion uses SQLite."},
    {"id": "m2", "role": "user", "content": "Alex works on Orion."},
    {"id": "m3", "role": "user", "content": "We decided to use SQLite."},
    {"id": "m4", "role": "user", "content": "We rejected MongoDB for storage."},
]

DATED_CONVERSATION = [
    {"id": "d1", "role": "user", "content": "Project Orion uses SQLite.", "timestamp": 1700000100.0},
    {"id": "d2", "role": "user", "content": "Alex works on Orion.", "timestamp": 1700000050.0},
    {"id": "d3", "role": "user", "content": "We decided to use SQLite.", "timestamp": 1700000200.0},
]

TIMESTAMP_KEYS = ("created_at", "updated_at", "starts_at", "ends_at", "date", "saved_at")


def _dump(*memories) -> str:
    """Canonical JSON for a set of memory objects/states."""
    payload = []
    for mem in memories:
        if mem is None:
            payload.append(None)
        elif isinstance(mem, dict):
            payload.append(mem)
        else:
            payload.append(mem.to_dict())
    return json.dumps(payload, sort_keys=True, default=str)


def _leaves(node, key=None):
    """Yield every (key, value) pair in a nested structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v, key)
    else:
        yield key, node


def _all_timestamps(*states):
    for state in states:
        for key, value in _leaves(state):
            if key in TIMESTAMP_KEYS:
                yield key, value


# ── the stamp itself ────────────────────────────────────────────────────

def test_pipeline_stamp_is_the_newest_source_timestamp():
    assert pipeline_stamp(DATED_CONVERSATION) == 1700000200.0


def test_pipeline_stamp_is_unknown_without_any_source_date():
    assert pipeline_stamp(CONVERSATION) == UNKNOWN_TIME
    assert pipeline_stamp([]) == UNKNOWN_TIME


def test_pipeline_stamp_ignores_booleans_and_non_numbers():
    assert pipeline_stamp([{"content": "x", "timestamp": True}]) == UNKNOWN_TIME
    assert pipeline_stamp([{"content": "x", "timestamp": "2024-01-01"}]) == UNKNOWN_TIME
    assert pipeline_stamp([{"content": "x", "ts": 12.5, "time": None}]) == 12.5


def test_pipeline_stamp_is_stable_across_calls():
    assert len({pipeline_stamp(CONVERSATION) for _ in range(5)}) == 1


# ── no wall clock in the output ─────────────────────────────────────────

def test_consolidation_module_has_no_clock():
    """A wall-clock reference in this module is the bug we just fixed."""
    assert not hasattr(consolidation, "time")


def test_no_output_timestamp_looks_like_now():
    before = time.time()
    rc, em, sm = consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C")
    after = time.time()
    seen = 0
    for key, value in _all_timestamps(rc.to_dict(), em.to_dict(), sm.to_dict()):
        seen += 1
        assert value is None or value == UNKNOWN_TIME or not (before - 1 <= value <= after + 1), (
            f"{key}={value} looks like the wall clock"
        )
    assert seen > 0, "the walker found no timestamps at all"


def test_every_artifact_shares_the_same_stamp():
    rc, em, sm = consolidate_to_episodes(DATED_CONVERSATION, keep_recent=0, conversation_id="C")
    stamp = 1700000200.0
    for item in sm.items:
        assert (item.created_at, item.updated_at) == (stamp, stamp)
    for entity in sm.entities.values():
        assert entity.created_at == stamp
    for relation in sm.relations:
        assert relation.created_at == stamp
    for contradiction in sm.contradictions:
        assert contradiction.created_at == stamp
    for episode in em.episodes:
        assert episode.created_at == stamp
        assert episode.updated_at is None          # never touched on creation
    # a decision keeps its own message date, one per source message
    assert {d.date for d in sm.decisions} == {1700000200.0}


def test_undated_run_uses_the_unknown_stamp():
    rc, em, sm = consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C")
    assert sm.items and sm.decisions
    assert all(item.created_at == UNKNOWN_TIME for item in sm.items)
    assert all(d.date is None for d in sm.decisions)


def test_consolidate_exchanges_is_equally_deterministic():
    rc, sm = consolidate_exchanges(CONVERSATION, keep_recent=0)
    assert all(item.created_at == UNKNOWN_TIME for item in sm.items)
    assert all(entity.created_at == UNKNOWN_TIME for entity in sm.entities.values())
    assert _dump(consolidate_exchanges(CONVERSATION, keep_recent=0)[1]) == _dump(sm)


# ── byte-identical across runs, processes and hash seeds ────────────────

def test_two_runs_in_one_process_are_identical():
    first = _dump(*consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C"))
    time.sleep(0.02)                                  # a wall clock would differ
    second = _dump(*consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C"))
    assert first == second


CHILD_SCRIPT = """
import json, sys
from boster.memory.consolidation import consolidate_to_episodes
conversation = json.loads(sys.argv[1])
rc, em, sm = consolidate_to_episodes(conversation, keep_recent=0, conversation_id="C")
print(json.dumps({"e": em.to_dict(), "s": sm.to_dict()}, sort_keys=True, default=str))
"""


def _run_child(seed: str, extra_env: dict) -> str:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONHASHSEED": seed, **extra_env}
    proc = subprocess.run(
        [sys.executable, "-c", CHILD_SCRIPT, json.dumps(CONVERSATION)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_output_is_identical_across_processes_and_hash_seeds():
    outputs = {_run_child(seed, {}) for seed in ("0", "1", "42", "random")}
    assert len(outputs) == 1, "the pipeline output depends on the process/hash seed"


def test_output_does_not_depend_on_the_system_timezone():
    outputs = {
        _run_child("0", {"TZ": tz})
        for tz in ("UTC", "America/New_York", "Asia/Tokyo")
    }
    assert len(outputs) == 1


# ── persistence round trips ─────────────────────────────────────────────

def test_save_load_save_is_byte_identical(tmp_path):
    rc, em, sm = consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C")
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    MemoryPersistence().save_state(rc, sm, em, path=first_path, saved_at=999.0)
    rc2, em2, sm2 = MemoryPersistence().load_state(path=first_path)
    MemoryPersistence().save_state(rc2, sm2, em2, path=second_path, saved_at=999.0)

    assert first_path.read_text(encoding="utf-8") == second_path.read_text(encoding="utf-8")


def test_saved_state_is_stable_across_repeated_saves(tmp_path):
    rc, em, sm = consolidate_to_episodes(DATED_CONVERSATION, keep_recent=0, conversation_id="C")
    blobs = []
    for index in range(3):
        path = tmp_path / f"s{index}.json"
        MemoryPersistence().save_state(rc, sm, em, path=path, saved_at=1.0)
        blobs.append(path.read_text(encoding="utf-8"))
    assert len(set(blobs)) == 1


def test_saved_at_defaults_to_the_wall_clock(tmp_path):
    path = tmp_path / "s.json"
    MemoryPersistence().save_state(path=path)
    assert json.loads(path.read_text(encoding="utf-8"))["saved_at"] > 0


def test_loading_a_timestamp_less_document_does_not_invent_one(tmp_path):
    """Legacy documents with no timestamps must load deterministically."""
    document = {
        "version": 2,
        "recent": {"messages": []},
        "episodic": {"episodes": [{"id": "ep_old", "summary": "old", "message_ids": ["mid_x"]}]},
        "semantic": {
            "items": [{"id": "item_1", "content": "x"}],
            "entities": {"orion": {"name": "Orion", "kind": "project", "source_ids": ["mid_x"]}},
            "relations": [{"subject": "Orion", "predicate": "uses", "object": "SQLite"}],
            "decisions": [{"value": "SQLite", "status": "active"}],
            "contradictions": [{"id": "contra_1", "claim_a": "a", "claim_b": "b"}],
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    rc, em, sm = MemoryPersistence().load_state(path=path)
    assert sm.items[0].created_at == UNKNOWN_TIME
    assert sm.entities["orion"].created_at == UNKNOWN_TIME
    assert sm.relations[0].created_at == UNKNOWN_TIME
    assert sm.contradictions[0].created_at == UNKNOWN_TIME
    assert em.episodes[0].created_at == UNKNOWN_TIME

    rc2, em2, sm2 = MemoryPersistence().load_state(path=path)
    assert _dump(em, sm) == _dump(em2, sm2)


# ── idempotent re-consolidation ─────────────────────────────────────────

def test_reconsolidating_the_same_run_converges_and_uses_the_stamp():
    first = consolidate_to_episodes(CONVERSATION, keep_recent=0, conversation_id="C")
    em = first[1]
    second = consolidate_to_episodes(CONVERSATION, keep_recent=0, episodic=em, conversation_id="C")
    assert em.episodes[0].updated_at == UNKNOWN_TIME     # the stamp, not now()
    assert len(em.episodes) == 1                         # merged, not duplicated
    third = consolidate_to_episodes(CONVERSATION, keep_recent=0, episodic=em, conversation_id="C")
    assert _dump(*second) == _dump(*third), "the merged state must be stable"


def test_extending_an_episode_uses_the_stamp_not_the_clock():
    em = EpisodicMemory()
    first = [
        {"id": "e1", "role": "user", "content": "Project Orion uses SQLite.", "timestamp": 100.0},
        {"id": "e2", "role": "user", "content": "We decided to use SQLite.", "timestamp": 110.0},
    ]
    _, em, _ = consolidate_to_episodes(first, keep_recent=0, episodic=em, conversation_id="C")
    extended = first + [
        {"id": "e3", "role": "user", "content": "Alex works on Orion.", "timestamp": 120.0},
    ]
    _, em, _ = consolidate_to_episodes(extended, keep_recent=0, episodic=em, conversation_id="C")
    assert len(em.episodes) == 1
    assert em.episodes[0].updated_at == 120.0            # the newest source, not now()


# ── retrieval ranking is reproducible on demand ─────────────────────────

def test_retrieval_is_reproducible_with_an_explicit_clock():
    from boster.memory.manager import MemoryManager

    manager = MemoryManager()
    manager.add("Project Orion uses SQLite for storage")
    manager.add("Alex works on the Orion backend")
    first = manager.retrieve("orion", now=1800000000.0)
    second = manager.retrieve("orion", now=1800000000.0)
    assert [e.id for e in first.entries] == [e.id for e in second.entries]
    assert first.total_candidates == second.total_candidates
    assert first.entries, "the fixture must actually retrieve something"
