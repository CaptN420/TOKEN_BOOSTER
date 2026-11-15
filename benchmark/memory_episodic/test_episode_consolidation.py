"""Episodic consolidation tests (Boster v2.2, Phase 2.2).

Covers the additive ``consolidate_to_episodes``: segmentation, exact
provenance, stable ids, idempotence/upsert, resolvable SemanticMemory
references, and unchanged public contract of ``consolidate_exchanges``.
Assertions compare exact values, never mere list length.
"""
from __future__ import annotations

import inspect

import pytest

from boster.memory.consolidation import (
    consolidate_exchanges,
    consolidate_to_episodes,
    message_id_for,
    resolve_decision_key,
)
from boster.memory.models import (
    Episode,
    EpisodicMemory,
    RecentContext,
    SemanticMemory,
)

COHERENT = [
    {"role": "user", "content": "Alex owns Project Orion."},
    {"role": "assistant", "content": "Orion is a project."},
    {"role": "user", "content": "Orion uses SQLite because it is lightweight."},
]

MULTI_TOPIC = [
    {"role": "user", "content": "Let's talk about Orion storage."},
    {"role": "user", "content": "Orion uses SQLite."},
    {"role": "user", "content": "Moving on, let's discuss the deployment."},
    {"role": "user", "content": "We deploy Orion with Docker."},
]

CONTINUITY = [
    {"role": "user", "content": "The authentication service stores sessions."},
    {"role": "user", "content": "Tokens are signed with rotating keys."},
]

SHARED_ENTITY = [
    {"role": "user", "content": "Orion uses SQLite for storage."},
    {"role": "user", "content": "Now let's talk about Orion deployment."},
    {"role": "user", "content": "Orion runs on Windows."},
]

CONTRADICTION = [
    {"role": "user", "content": "We should use SQLite for Orion storage."},
    {"role": "assistant", "content": "OK."},
    {"role": "user", "content": "Actually we should use PostgreSQL for Orion storage."},
]


def _episodes(em) -> "list[Episode]":
    return [e for e in em.episodes if isinstance(e, Episode)]


def _mids(messages):
    """Durable ids of the given messages (public provenance API)."""
    return [message_id_for(m) for m in messages]


def _structure(em):
    """Deterministic structural view (ignores wall-clock timestamps)."""
    return [
        (e.id, e.summary, tuple(e.message_ids), tuple(e.entity_names),
         tuple(e.relation_keys), tuple(e.decision_keys), e.status)
        for e in _episodes(em)
    ]


# 1
def test_coherent_conversation_creates_episode():
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 1
    ep = eps[0]
    assert isinstance(ep, Episode)
    assert ep.message_ids == _mids(COHERENT)
    assert ep.summary == "Alex owns Project Orion."
    assert ep.status == "active"
    assert rc.messages == []


# 2
def test_short_conversation_documented_behaviour():
    # len(exchanges) <= keep_recent => nothing is consolidated, all kept recent.
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=5)
    assert _episodes(em) == []
    assert [m["content"] for m in rc.messages] == [c["content"] for c in COHERENT]
    assert sm.items == []


# 3
def test_empty_messages_produce_no_episode():
    rc, em, sm = consolidate_to_episodes(
        [{"role": "user", "content": ""}, {"role": "assistant", "content": "   "}],
        keep_recent=0,
    )
    assert _episodes(em) == []
    assert sm.items == []


# 4
def test_explicit_topics_segment_coherently():
    rc, em, sm = consolidate_to_episodes(MULTI_TOPIC, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 2
    assert eps[0].message_ids == _mids(MULTI_TOPIC)[:2]
    assert eps[1].message_ids == _mids(MULTI_TOPIC)[2:]


# 5
def test_continuity_preserved_with_different_vocabulary():
    rc, em, sm = consolidate_to_episodes(CONTINUITY, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 1
    assert eps[0].message_ids == _mids(CONTINUITY)


# 6
def test_shared_entity_does_not_merge_topics():
    rc, em, sm = consolidate_to_episodes(SHARED_ENTITY, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 2
    assert eps[0].id != eps[1].id
    # both reference the shared entity, yet remain distinct episodes
    assert any("Orion" in s for s in eps[0].entity_names)
    assert any("Orion" in s for s in eps[1].entity_names)


# 7
def test_exact_message_provenance():
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    ep = _episodes(em)[0]
    expected = _mids(COHERENT)
    assert ep.message_ids == expected
    # every reference maps to a real message present in the input
    for mid in ep.message_ids:
        assert mid in expected
    # and never the legacy positional form
    assert not any(mid == f"msg_{i}" for i, mid in enumerate(ep.message_ids))


# 8
def test_episode_ids_stable_across_runs():
    _, em1, _ = consolidate_to_episodes(COHERENT, keep_recent=0)
    _, em2, _ = consolidate_to_episodes(COHERENT, keep_recent=0)
    assert [e.id for e in _episodes(em1)] == [e.id for e in _episodes(em2)]
    assert _episodes(em1)[0].id.startswith("ep_")


# 9
def test_identical_consolidation_no_duplicates():
    _, em, _ = consolidate_to_episodes(COHERENT, keep_recent=0)
    _, em, _ = consolidate_to_episodes(COHERENT, keep_recent=0, episodic=em)
    eps = _episodes(em)
    assert len(eps) == 1
    assert eps[0].message_ids == _mids(COHERENT)


# 10
def test_new_messages_extend_existing_episode():
    _, em, _ = consolidate_to_episodes(COHERENT[:2], keep_recent=0)
    original_id = _episodes(em)[0].id
    _, em, _ = consolidate_to_episodes(COHERENT, keep_recent=0, episodic=em)
    eps = _episodes(em)
    assert len(eps) == 1                       # extended, not duplicated
    assert eps[0].id == original_id            # stable id kept
    assert eps[0].message_ids == _mids(COHERENT)


def test_distinct_episodes_same_project_not_merged():
    _, em, _ = consolidate_to_episodes(SHARED_ENTITY, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 2
    assert eps[0].message_ids != eps[1].message_ids


# 11
def test_entities_linked_and_resolvable():
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    ep = _episodes(em)[0]
    assert ep.entity_names, "expected at least one resolvable entity"
    for name in ep.entity_names:
        assert name.lower() in sm.entities                     # resolvable
        entity = sm.entities[name.lower()]
        assert any(s in ep.message_ids for s in entity.source_ids)


# 12
def test_relations_linked_and_resolvable():
    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    ep = _episodes(em)[0]
    assert ep.relation_keys
    for key in ep.relation_keys:
        subject, predicate, obj = key.split("|")
        match = [
            r for r in sm.relations
            if (r.subject, r.predicate, r.object) == (subject, predicate, obj)
        ]
        assert match, f"unresolvable relation key {key}"
        assert match[0].source_id in ep.message_ids            # source matches episode


# 13
def test_decisions_linked_and_resolvable():
    # a firm decision must be present for the episode to reference one
    conversation = COHERENT + [
        {"role": "user", "content": "We decided to use SQLite."},
    ]
    rc, em, sm = consolidate_to_episodes(conversation, keep_recent=0)
    ep = _episodes(em)[0]
    assert ep.decision_keys
    for key in ep.decision_keys:
        assert key.startswith("decision:")
        decision = resolve_decision_key(sm, key)
        assert decision is not None                          # position-independent
        assert decision.id and key == f"decision:{decision.id}"


# 14
def test_contradictions_and_sources_preserved():
    rc, em, sm = consolidate_to_episodes(CONTRADICTION, keep_recent=0)
    assert len(sm.contradictions) == 1
    contra = sm.contradictions[0]
    assert contra.claim_a.startswith("We should use SQLite")
    assert contra.claim_b.startswith("Actually")
    # both sources point at real messages and are kept distinct
    assert contra.source_a == _mids(CONTRADICTION)[0]
    assert contra.source_b == _mids(CONTRADICTION)[2]
    assert contra.source_a != contra.source_b
    assert contra.resolved is False


# 15
def test_recent_messages_kept_in_recent_context():
    five = COHERENT + [
        {"role": "user", "content": "Deploy Orion with Docker."},
        {"role": "assistant", "content": "Understood."},
    ]
    rc, em, sm = consolidate_to_episodes(five, keep_recent=2)
    assert [m["content"] for m in rc.messages] == ["Deploy Orion with Docker.", "Understood."]


# 16
def test_no_overlap_between_recent_and_episodes():
    five = COHERENT + ["a", "b"]  # placeholder replaced below
    five = COHERENT + [
        {"role": "user", "content": "Deploy Orion with Docker."},
        {"role": "assistant", "content": "Understood."},
    ]
    keep = 2
    rc, em, sm = consolidate_to_episodes(five, keep_recent=keep)
    all_ids = _mids(five)
    older_ids = set(all_ids[:len(five) - keep])
    recent_ids = set(all_ids[len(five) - keep:])
    for ep in _episodes(em):
        for mid in ep.message_ids:
            assert mid in older_ids
            assert mid not in recent_ids


# 17
def test_consolidate_exchanges_signature_unchanged():
    sig = inspect.signature(consolidate_exchanges)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["exchanges", "keep_recent"]
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default == 5


# 18
def test_consolidate_exchanges_returns_two_elements():
    result = consolidate_exchanges(COHERENT, keep_recent=0)
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], RecentContext)
    assert isinstance(result[1], SemanticMemory)


# 19
def test_malformed_entries_handled_gracefully():
    malformed = [None, {"role": "user", "content": "Orion uses SQLite."}, "notadict", 123]
    rc, em, sm = consolidate_to_episodes(malformed, keep_recent=0)
    eps = _episodes(em)
    assert len(eps) == 1
    # provenance refers to the one real message, never to a placeholder
    assert eps[0].message_ids == [message_id_for(malformed[1])]


# 20
def test_results_are_deterministic():
    _, em1, sm1 = consolidate_to_episodes(MULTI_TOPIC, keep_recent=0)
    _, em2, sm2 = consolidate_to_episodes(MULTI_TOPIC, keep_recent=0)
    assert _structure(em1) == _structure(em2)
    assert sorted(sm1.entities) == sorted(sm2.entities)
    assert [(r.subject, r.predicate, r.object, r.source_id) for r in sm1.relations] == \
           [(r.subject, r.predicate, r.object, r.source_id) for r in sm2.relations]
    assert [d.value for d in sm1.decisions] == [d.value for d in sm2.decisions]


# extra: persistence-ready round-trip through the model layer
def test_episode_references_survive_model_roundtrip():
    _, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    restored = EpisodicMemory.from_dict(em.to_dict())
    back = _episodes(restored)
    assert len(back) == 1
    ep = back[0]
    assert ep.id == _episodes(em)[0].id
    assert ep.message_ids == _mids(COHERENT)
    for name in ep.entity_names:
        assert name.lower() in sm.entities


# Phase H: real disk persistence of a consolidated conversation.
def test_consolidated_state_persists_to_disk(tmp_path):
    from boster.memory.persistence import MemoryPersistence

    rc, em, sm = consolidate_to_episodes(COHERENT, keep_recent=0)
    before_eps = _episodes(em)
    before_ep = before_eps[0]
    path = tmp_path / "state.json"

    assert MemoryPersistence().save_state(rc, sm, em, path=path) is True
    # reload from a brand-new persistence object
    rc2, em2, sm2 = MemoryPersistence().load_state(path=path)

    back = _episodes(em2)
    assert len(back) == 1
    ep2 = back[0]
    # exact value comparison across the disk round-trip
    assert ep2.id == before_ep.id
    assert ep2.summary == before_ep.summary
    assert ep2.message_ids == before_ep.message_ids
    assert ep2.entity_names == before_ep.entity_names
    assert ep2.relation_keys == before_ep.relation_keys
    assert ep2.decision_keys == before_ep.decision_keys
    assert ep2.status == before_ep.status

    # references remain resolvable against the reloaded SemanticMemory
    for name in ep2.entity_names:
        assert name.lower() in sm2.entities
    for key in ep2.decision_keys:
        assert resolve_decision_key(sm2, key) is not None
    for key in ep2.relation_keys:
        subject, predicate, obj = key.split("|")
        assert any(
            (r.subject, r.predicate, r.object) == (subject, predicate, obj)
            for r in sm2.relations
        )

