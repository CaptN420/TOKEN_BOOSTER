"""Tests for the Episode data model (Boster v2.2, Phase 2.1).

Verifies construction, validation, serialisation, and backward
compatibility with the legacy free-form episode dicts, using exact value
comparisons rather than existence checks.
"""
from __future__ import annotations

import json

import pytest

from boster.memory.models import EpisodicMemory, Episode, EPISODE_STATUSES


def _full_episode() -> Episode:
    return Episode(
        id="ep_1",
        summary="Chose SQLite for Orion",
        message_ids=["msg_0", "msg_1", "msg_2"],
        starts_at=100.0,
        ends_at=200.0,
        created_at=150.0,
        updated_at=180.0,
        entity_names=["Orion", "SQLite"],
        relation_keys=["Orion|uses|SQLite"],
        decision_keys=["decision:sqlite"],
        status="resolved",
        confidence=0.8,
        linked_episode_ids=["ep_0"],
    )


# 1
def test_construct_valid_episode():
    ep = _full_episode()
    assert ep.id == "ep_1"
    assert ep.summary == "Chose SQLite for Orion"
    assert ep.message_ids == ["msg_0", "msg_1", "msg_2"]
    assert ep.starts_at == 100.0 and ep.ends_at == 200.0
    assert ep.entity_names == ["Orion", "SQLite"]
    assert ep.relation_keys == ["Orion|uses|SQLite"]
    assert ep.decision_keys == ["decision:sqlite"]
    assert ep.status == "resolved"
    assert ep.confidence == 0.8
    assert ep.linked_episode_ids == ["ep_0"]


# 2
def test_empty_id_rejected():
    with pytest.raises(ValueError, match="id"):
        Episode(id="", summary="ok")
    with pytest.raises(ValueError, match="id"):
        Episode(id="   ", summary="ok")


# 3
def test_empty_summary_rejected():
    with pytest.raises(ValueError, match="summary"):
        Episode(id="e1", summary="")
    with pytest.raises(ValueError, match="summary"):
        Episode(id="e1", summary="   ")


# 4
def test_negative_confidence_rejected():
    with pytest.raises(ValueError, match="confidence"):
        Episode(id="e1", summary="s", confidence=-0.1)


# 5
def test_confidence_above_one_rejected():
    with pytest.raises(ValueError, match="confidence"):
        Episode(id="e1", summary="s", confidence=1.5)


def test_confidence_bounds_accepted():
    assert Episode(id="e1", summary="s", confidence=0.0).confidence == 0.0
    assert Episode(id="e1", summary="s", confidence=1.0).confidence == 1.0


# 6
def test_invalid_time_interval_rejected():
    with pytest.raises(ValueError, match="starts_at"):
        Episode(id="e1", summary="s", starts_at=200.0, ends_at=100.0)


# 7
def test_optional_timestamps_accepted():
    ep = Episode(id="e1", summary="s")
    assert ep.starts_at is None
    assert ep.ends_at is None
    assert ep.updated_at is None
    # created_at always has a deterministic default
    assert isinstance(ep.created_at, float)


def test_equal_timestamps_accepted():
    ep = Episode(id="e1", summary="s", starts_at=50.0, ends_at=50.0)
    assert ep.starts_at == ep.ends_at == 50.0


# 8
def test_optional_lists_initialised_independently():
    a = Episode(id="a", summary="A")
    b = Episode(id="b", summary="B")
    a.message_ids.append("msg_1")
    a.entity_names.append("X")
    a.linked_episode_ids.append("z")
    assert b.message_ids == []
    assert b.entity_names == []
    assert b.linked_episode_ids == []


def test_duplicate_list_entries_removed():
    ep = Episode(id="e1", summary="s", message_ids=["m", "m", "n"],
                 entity_names=["A", "A"])
    assert ep.message_ids == ["m", "n"]
    assert ep.entity_names == ["A"]


def test_invalid_status_rejected():
    with pytest.raises(ValueError, match="status"):
        Episode(id="e1", summary="s", status="bogus")
    assert EPISODE_STATUSES == frozenset({"active", "open", "resolved", "interrupted"})


# 9
def test_full_serialisation():
    data = _full_episode().to_dict()
    assert data == {
        "id": "ep_1",
        "summary": "Chose SQLite for Orion",
        "message_ids": ["msg_0", "msg_1", "msg_2"],
        "starts_at": 100.0,
        "ends_at": 200.0,
        "created_at": 150.0,
        "updated_at": 180.0,
        "entity_names": ["Orion", "SQLite"],
        "relation_keys": ["Orion|uses|SQLite"],
        "decision_keys": ["decision:sqlite"],
        "status": "resolved",
        "confidence": 0.8,
        "linked_episode_ids": ["ep_0"],
    }
    json.dumps(data)  # JSON-compatible


def test_to_dict_does_not_alias_internal_lists():
    ep = _full_episode()
    data = ep.to_dict()
    data["message_ids"].append("ghost")
    assert ep.message_ids == ["msg_0", "msg_1", "msg_2"]


# 10
def test_full_deserialisation():
    ep = Episode.from_dict(_full_episode().to_dict())
    assert ep.id == "ep_1"
    assert ep.summary == "Chose SQLite for Orion"
    assert ep.message_ids == ["msg_0", "msg_1", "msg_2"]
    assert ep.starts_at == 100.0 and ep.ends_at == 200.0
    assert ep.created_at == 150.0 and ep.updated_at == 180.0
    assert ep.entity_names == ["Orion", "SQLite"]
    assert ep.decision_keys == ["decision:sqlite"]
    assert ep.status == "resolved"
    assert ep.confidence == 0.8
    assert ep.linked_episode_ids == ["ep_0"]


# 11
def test_round_trip_is_exact():
    ep = _full_episode()
    assert Episode.from_dict(ep.to_dict()).to_dict() == ep.to_dict()


def test_round_trip_with_optional_fields_absent():
    ep = Episode(id="e1", summary="s")
    restored = Episode.from_dict(ep.to_dict())
    assert restored.to_dict() == ep.to_dict()
    assert restored.starts_at is None and restored.updated_at is None


# 12
def test_legacy_dict_accepted():
    legacy = {"type": "decision", "content": "Chose SQLite"}
    snapshot = dict(legacy)
    ep = Episode.from_dict(legacy)
    assert ep.summary == "Chose SQLite"
    assert ep.id and ep.id.startswith("ep_")
    assert ep.status == "active"
    assert ep.confidence == 1.0
    assert ep.message_ids == []
    assert legacy == snapshot  # source never mutated


def test_legacy_id_derivation_is_deterministic():
    legacy = {"type": "decision", "content": "Chose SQLite"}
    assert Episode.from_dict(legacy).id == Episode.from_dict(legacy).id


def test_legacy_dict_without_any_text_is_not_rejected():
    ep = Episode.from_dict({"type": "event"})
    assert ep.summary  # non-empty fallback
    assert ep.id


# 13
def test_episode_in_episodic_memory():
    em = EpisodicMemory()
    ep = Episode(id="e1", summary="Did a thing", message_ids=["msg_0"])
    em.add_episode(ep)
    assert em.episodes[0] is ep
    # round-trip preserves the Episode type
    restored = EpisodicMemory.from_dict(em.to_dict())
    assert isinstance(restored.episodes[0], Episode)
    assert restored.episodes[0].summary == "Did a thing"
    assert restored.episodes[0].message_ids == ["msg_0"]


# 14
def test_episodic_memory_legacy_dict_compatibility():
    em = EpisodicMemory()
    em.add_episode({"type": "decision", "content": "Chose SQLite"})
    data = em.to_dict()
    assert data["episodes"] == [{"type": "decision", "content": "Chose SQLite"}]
    em2 = EpisodicMemory.from_dict(data)
    # legacy dict stays a dict -> old subscript access still works
    assert em2.episodes[0]["content"] == "Chose SQLite"


# 15
def test_episodic_memory_mixed_episodes_serialisation():
    em = EpisodicMemory()
    em.add_episode(Episode(id="e1", summary="New-style"))
    em.add_episode({"type": "legacy", "content": "Old-style"})

    data = em.to_dict()
    assert data["episodes"][0]["summary"] == "New-style"
    assert data["episodes"][1] == {"type": "legacy", "content": "Old-style"}
    json.dumps(data)  # whole structure is JSON-compatible

    em2 = EpisodicMemory.from_dict(data)
    assert isinstance(em2.episodes[0], Episode)
    assert em2.episodes[0].summary == "New-style"
    assert em2.episodes[1]["content"] == "Old-style"
    assert len(em2.episodes) == 2


def test_episodic_memory_add_episode_still_appends():
    em = EpisodicMemory()
    entry = {"custom": "dict"}
    em.add_episode(entry)
    assert em.episodes == [entry]  # unchanged public behaviour


# 16
def test_public_imports():
    from boster.memory.models import Episode as M
    from boster.memory import Episode as P
    import boster

    assert M is Episode
    assert P is Episode
    assert boster.Episode is Episode
    # and it is genuinely constructible through each path
    assert P(id="x", summary="y").id == "x"
    assert boster.Episode(id="x", summary="y").id == "x"
