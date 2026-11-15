"""Stable provenance tests (Boster v2.2.1).

Covers the three hardened identities: durable message ids, conversation-
anchored episode ids, and position-independent decision ids — plus their
behaviour across windows, reload and reordering. Exact values only.
"""
from __future__ import annotations

import pytest

from boster.memory.consolidation import (
    consolidate_to_episodes,
    decision_id_for,
    message_id_for,
    resolve_decision_key,
)
from boster.memory.models import (
    Decision,
    Episode,
    EpisodicMemory,
    RecentContext,
    SemanticMemory,
)
from boster.memory.persistence import MemoryPersistence


def _episodes(em):
    return [e for e in em.episodes if isinstance(e, Episode)]


COHERENT = [
    {"role": "user", "content": "Alex owns Project Orion."},
    {"role": "assistant", "content": "Orion is a project."},
    {"role": "user", "content": "Orion uses SQLite because it is lightweight."},
]

WINDOW = [
    {"role": "user", "content": "Orion is a project."},
    {"role": "user", "content": "Orion uses SQLite."},
    {"role": "user", "content": "Orion runs on Windows."},
    {"role": "user", "content": "Orion supports macOS."},
]


# ── Messages ────────────────────────────────────────────────────────

# 1
def test_native_message_id_used_when_present():
    assert message_id_for({"id": "abc", "content": "x"}) == "mid_abc"
    assert message_id_for({"message_id": "n1", "content": "a"}) == "mid_n1"
    assert message_id_for({"msg_id": "n1", "content": "b"}) == "mid_n1"
    # native ids are authoritative: same native id -> same durable id
    assert message_id_for({"id": "abc", "content": "x"}) == message_id_for({"id": "abc", "content": "y"})


# 2
def test_identical_messages_get_distinct_ids():
    duplicate = {"role": "user", "content": "exactly the same text"}
    assert message_id_for(duplicate, occurrence=0) != message_id_for(duplicate, occurrence=1)
    # and at the consolidation level the two copies stay distinguishable
    _, em, sm = consolidate_to_episodes([duplicate, duplicate], keep_recent=0)
    sources = [item.source_ids[0] for item in sm.items]
    assert len(sources) == 2
    assert sources[0] != sources[1]


# 3
def test_prepending_a_message_keeps_previous_ids():
    base_ids = [message_id_for(m, conversation_id="C") for m in WINDOW[1:]]
    all_ids = [message_id_for(m, conversation_id="C") for m in WINDOW]
    assert all_ids[1:] == base_ids  # prepending shifted nothing


# 4
def test_windowed_consolidation_keeps_shared_provenance():
    _, em_a, _ = consolidate_to_episodes(WINDOW[:3], keep_recent=0, conversation_id="C")
    _, em_b, _ = consolidate_to_episodes(WINDOW[1:], keep_recent=0, conversation_id="C")
    ids_a = set(_episodes(em_a)[0].message_ids)
    ids_b = set(_episodes(em_b)[0].message_ids)
    shared = {message_id_for(WINDOW[1], conversation_id="C"),
              message_id_for(WINDOW[2], conversation_id="C")}
    assert shared <= ids_a
    assert shared <= ids_b


# 5
def test_derived_id_is_deterministic_and_documented_shape():
    message = {"role": "user", "content": "Orion uses SQLite."}
    first = message_id_for(message)
    assert first == message_id_for(message)          # deterministic
    assert first.startswith("msg_")
    assert len(first) == len("msg_") + 12            # 12 hex chars
    assert first != "msg_0"                          # never the legacy positional form


# 6
def test_malformed_entries_produce_no_fictitious_reference():
    malformed = [None, "notadict", {"role": "user", "content": "Orion uses SQLite."}, 42]
    _, em, sm = consolidate_to_episodes(malformed, keep_recent=0)
    real_id = message_id_for(malformed[2])
    eps = _episodes(em)
    assert len(eps) == 1
    assert eps[0].message_ids == [real_id]
    for item in sm.items:
        assert item.source_ids == [real_id]          # no id for a placeholder


# 7
def test_legacy_msg_index_ids_still_readable():
    ep = Episode.from_dict({"id": "ep_old", "summary": "old", "message_ids": ["msg_0", "msg_2"]})
    assert ep.message_ids == ["msg_0", "msg_2"]
    em = EpisodicMemory.from_dict(
        {"episodes": [{"id": "ep_old", "summary": "old", "message_ids": ["msg_0"]}]}
    )
    assert _episodes(em)[0].message_ids == ["msg_0"]


# ── Episodes ────────────────────────────────────────────────────────

# 8
def test_same_episode_consolidated_twice_no_duplicate():
    _, em, _ = consolidate_to_episodes(COHERENT, keep_recent=0, conversation_id="C")
    first_ids = [e.id for e in _episodes(em)]
    _, em, _ = consolidate_to_episodes(COHERENT, keep_recent=0, conversation_id="C", episodic=em)
    assert [e.id for e in _episodes(em)] == first_ids
    assert len(_episodes(em)) == 1


# 9
def test_same_starting_message_different_conversations_no_merge():
    messages = [
        {"role": "user", "content": "Start here."},
        {"role": "assistant", "content": "ok"},
    ]
    em = EpisodicMemory()
    _, em, _ = consolidate_to_episodes(messages, keep_recent=0, conversation_id="A", episodic=em)
    _, em, _ = consolidate_to_episodes(messages, keep_recent=0, conversation_id="B", episodic=em)
    eps = _episodes(em)
    assert len(eps) == 2                       # no incorrect merge
    assert eps[0].id != eps[1].id


# 10
def test_distinct_episodes_sharing_entity_stay_distinct():
    shared = [
        {"role": "user", "content": "Orion uses SQLite for storage."},
        {"role": "user", "content": "Now let's talk about Orion deployment."},
        {"role": "user", "content": "Orion runs on Windows."},
    ]
    _, em, _ = consolidate_to_episodes(shared, keep_recent=0, conversation_id="C")
    eps = _episodes(em)
    assert len(eps) == 2
    assert eps[0].id != eps[1].id
    assert any("Orion" in s for s in eps[0].entity_names)


# 11
def test_legitimate_extension_keeps_identity():
    _, em, _ = consolidate_to_episodes(WINDOW[:2], keep_recent=0, conversation_id="C")
    original = _episodes(em)[0].id
    _, em, _ = consolidate_to_episodes(WINDOW[:3], keep_recent=0, conversation_id="C", episodic=em)
    eps = _episodes(em)
    assert len(eps) == 1
    assert eps[0].id == original               # identity preserved on extension
    assert eps[0].message_ids == [message_id_for(m, conversation_id="C") for m in WINDOW[:3]]


# 12
def test_different_sources_yield_different_episode_identity():
    a = [{"role": "user", "content": "Alpha topic discussion."}]
    b = [{"role": "user", "content": "Completely different beta topic."}]
    _, em_a, _ = consolidate_to_episodes(a, keep_recent=0, conversation_id="C")
    _, em_b, _ = consolidate_to_episodes(b, keep_recent=0, conversation_id="C")
    assert _episodes(em_a)[0].id != _episodes(em_b)[0].id


# 13
def test_episode_identity_survives_save_reload(tmp_path):
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0, conversation_id="C")
    path = tmp_path / "state.json"
    assert MemoryPersistence().save_state(rc, sm, em, path=path) is True
    _, em2, _ = MemoryPersistence().load_state(path=path)
    assert [e.id for e in _episodes(em2)] == [e.id for e in _episodes(em)]
    assert _episodes(em2)[0].message_ids == _episodes(em)[0].message_ids


# ── Decisions ───────────────────────────────────────────────────────

# 14
def test_decision_reference_survives_reordering():
    # COHERENT alone carries no *firm* decision, so add one explicitly
    conversation = COHERENT + [{"role": "user", "content": "We decided to use SQLite."}]
    _, em, sm = consolidate_to_episodes(conversation, keep_recent=0)
    ep = _episodes(em)[0]
    keys = list(ep.decision_keys)
    assert keys
    sm.decisions.reverse()                     # move the decision in the list
    for key in keys:
        decision = resolve_decision_key(sm, key)
        assert decision is not None            # still resolvable, position-free


# 15
def test_decision_reference_survives_reload(tmp_path):
    conversation = COHERENT + [{"role": "user", "content": "We decided to use SQLite."}]
    rc, em, sm = consolidate_to_episodes(conversation, keep_recent=0)
    ep = _episodes(em)[0]
    keys = list(ep.decision_keys)
    path = tmp_path / "state.json"
    MemoryPersistence().save_state(rc, sm, em, path=path)
    _, _, sm2 = MemoryPersistence().load_state(path=path)
    sm2.decisions.reverse()                    # reordered copy after reload
    for key in keys:
        assert resolve_decision_key(sm2, key) is not None


# 16
def test_identical_decision_values_different_sources_not_merged():
    assert decision_id_for("Use X", source_id="mid_a") != decision_id_for("Use X", source_id="mid_b")
    conv = [
        {"role": "user", "content": "We chose SQLite for storage."},
        {"role": "user", "content": "Also we chose SQLite for caching."},
    ]
    _, _, sm = consolidate_to_episodes(conv, keep_recent=0)
    sqlite = [d for d in sm.decisions if d.value == "SQLite"]
    assert len(sqlite) == 2                    # not abused-merged
    assert sqlite[0].id != sqlite[1].id
    assert sqlite[0].source_ids != sqlite[1].source_ids


# 17
def test_revised_decision_keeps_history():
    conv = [
        {"role": "user", "content": "We chose PostgreSQL for the database."},
        {"role": "user", "content": "We chose SQLite for the database."},
    ]
    _, _, sm = consolidate_to_episodes(conv, keep_recent=0)
    values = [d.value for d in sm.decisions]
    assert "PostgreSQL" in values              # the earlier decision is never deleted
    assert "SQLite" in values
    assert len([d for d in sm.decisions if d.value == "PostgreSQL"]) == 1


# 18
def test_decision_without_provenance_has_no_false_reference():
    decision = Decision(value="Use X")
    assert decision.source_ids == []           # no fabricated source
    assert decision.id == ""
    # an empty id resolves to nothing rather than a bogus position
    assert resolve_decision_key(SemanticMemory(), f"decision:{decision.id}") is None
    assert resolve_decision_key(SemanticMemory(), "decision:notanindex") is None


# ── Phase E: full disk cycle with reorder ───────────────────────────

def test_full_persistence_cycle_with_reorder(tmp_path):
    rc, em, sm = consolidate_to_episodes(
        WINDOW, keep_recent=0, conversation_id="session-1"
    )
    ep_before = _episodes(em)[0]
    path = tmp_path / "memory.json"

    assert MemoryPersistence().save_state(rc, sm, em, path=path) is True
    rc2, em2, sm2 = MemoryPersistence().load_state(path=path)

    ep_after = _episodes(em2)[0]
    # exact identity across the disk round-trip
    assert ep_after.id == ep_before.id
    assert ep_after.message_ids == ep_before.message_ids
    # reorder decisions in the reloaded copy, references stay resolvable
    sm2.decisions.reverse()
    for key in ep_after.decision_keys:
        assert resolve_decision_key(sm2, key) is not None
    # references still point at real messages
    real_ids = {message_id_for(m, conversation_id="session-1") for m in WINDOW}
    for mid in ep_after.message_ids:
        assert mid in real_ids
