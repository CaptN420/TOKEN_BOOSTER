"""Adversarial memory validation (Boster v2.2.2).

Design rules for this suite:
* ground truth comes from ``scenarios.py`` — never from the extractor;
* no ``assert ... or True`` and no permissive assertions;
* behaviour that is genuinely missing is marked ``xfail(strict=True)``;
* observed defects are pinned by ``test_LIMITATION_*`` tests that assert the
  defect *exists*, so a future fix breaks them loudly (never silently).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from boster.memory.consolidation import (
    DECISION_STATUSES,
    _extract_decisions,
    classify_decision,
    consolidate_to_episodes,
    resolve_decision_key,
)
from boster.memory.models import (
    Decision,
    Episode,
    EpisodicMemory,
    MemoryItem,
    SemanticMemory,
)
from boster.memory.persistence import MemoryPersistence

from benchmark.memory_adversarial import evaluator as E
from benchmark.memory_adversarial import scenarios as S


def cons(gt, *, use_conversation_id=True):
    return E.consolidate(gt, use_conversation_id=use_conversation_id)


def episodes(em):
    return [e for e in em.episodes if isinstance(e, Episode)]


# ════════════════════════════════════════════════════════════════════
# Scenario 1 — confusion between conversations
# ════════════════════════════════════════════════════════════════════

def test_cross_conversation_isolation():
    _, _, sem_a = cons(S.CONV_A)
    _, _, sem_b = cons(S.CONV_B)
    contents_a = {i.content.lower() for i in sem_a.items}
    contents_b = {i.content.lower() for i in sem_b.items}
    assert contents_a == {m["content"].lower() for m in S.CONV_A.messages}
    assert contents_b == {m["content"].lower() for m in S.CONV_B.messages}
    assert contents_a.isdisjoint(contents_b)          # no leakage
    assert E.cross_conversation_leakage(S.CONV_A, S.CONV_B).value == 0.0


def test_cross_conversation_episodes_distinct():
    _, em_a, _ = cons(S.CONV_A)
    _, em_b, _ = cons(S.CONV_B)
    assert len(episodes(em_a)) == 1
    assert len(episodes(em_b)) == 1
    assert episodes(em_a)[0].id != episodes(em_b)[0].id


def test_cross_conversation_sources_are_correct():
    _, _, sem_a = cons(S.CONV_A)
    allowed = set(S.CONV_A.message_ids)
    for item in sem_a.items:
        assert item.source_ids
        assert set(item.source_ids) <= allowed          # never a foreign source


def test_without_conversation_id_identical_openers_do_not_merge():
    """Two conversations opening with the SAME first message must not be
    conflated: with no conversation identity the merge is refused as soon as
    the incoming sources are not an extension of the stored ones."""
    opener = "We are starting the project now."
    conv_a = [{"role": "user", "content": opener}, {"role": "user", "content": "Alpha uses SQLite."}]
    conv_b = [{"role": "user", "content": opener}, {"role": "user", "content": "Beta uses PostgreSQL."}]
    em = EpisodicMemory()
    _, em, _ = consolidate_to_episodes(conv_a, keep_recent=0, episodic=em)
    _, em, _ = consolidate_to_episodes(conv_b, keep_recent=0, episodic=em)
    eps = episodes(em)
    assert len(eps) == 2
    assert eps[0].id != eps[1].id
    # stable identity: re-consolidating the same input adds nothing
    _, em, _ = consolidate_to_episodes(conv_b, keep_recent=0, episodic=em)
    assert len(episodes(em)) == 2


def test_conversation_id_separates_identical_openers():
    opener = "We are starting the project now."
    conv_a = [{"role": "user", "content": opener}, {"role": "user", "content": "Alpha uses SQLite."}]
    conv_b = [{"role": "user", "content": opener}, {"role": "user", "content": "Beta uses PostgreSQL."}]
    em = EpisodicMemory()
    _, em, _ = consolidate_to_episodes(conv_a, keep_recent=0, conversation_id="A", episodic=em)
    _, em, _ = consolidate_to_episodes(conv_b, keep_recent=0, conversation_id="B", episodic=em)
    ids = [e.id for e in episodes(em)]
    assert len(ids) == 2
    assert ids[0] != ids[1]


# ════════════════════════════════════════════════════════════════════
# Scenario 2 — contradictory information
# ════════════════════════════════════════════════════════════════════

def test_both_claims_are_preserved_with_exact_sources():
    _, _, sem = cons(S.CONTRADICTION)
    by_source = {i.source_ids[0]: i.content for i in sem.items}
    assert by_source["mid_c1"] == "Project Orion uses SQLite."
    assert by_source["mid_c2"] == "Project Orion uses PostgreSQL."


def test_revision_does_not_delete_the_earlier_claim():
    _, _, sem = cons(S.CONTRADICTION)
    contents = [i.content for i in sem.items]
    assert any("uses SQLite" in c for c in contents)
    assert any("uses PostgreSQL" in c for c in contents)
    assert E.historical_integrity(sem, S.CONTRADICTION).value == 1.0


@pytest.mark.xfail(strict=True, reason="contradiction detection is keyword-gated "
                                       "(needs 'actually'/'instead'/'should use'/... in the text)")
def test_neutral_contradiction_is_detected():
    _, _, sem = cons(S.CONTRADICTION)
    assert len(sem.contradictions) >= 1


def test_signalled_contradiction_is_detected_with_durable_sources():
    _, _, sem = cons(S.CONTRADICTION_SIGNALLED)
    assert len(sem.contradictions) >= 1
    for c in sem.contradictions:
        assert c.source_a in S.CONTRADICTION_SIGNALLED.message_ids
        assert c.source_b in S.CONTRADICTION_SIGNALLED.message_ids


def test_contradiction_is_never_marked_resolved_without_proof():
    _, _, sem = cons(S.CONTRADICTION_SIGNALLED)
    assert sem.contradictions
    assert all(c.resolved is False for c in sem.contradictions)


# ════════════════════════════════════════════════════════════════════
# Scenario 3 — hypotheses vs facts
# ════════════════════════════════════════════════════════════════════

def test_modal_phrases_are_retained_verbatim():
    _, _, sem = cons(S.HYPOTHESES)
    contents = [i.content for i in sem.items]
    for m in S.HYPOTHESES.messages:
        assert m["content"] in contents


def test_possibility_is_not_recorded_as_a_decision():
    _, _, sem = cons(S.HYPOTHESES)
    assert {d.value for d in sem.decisions} == {"SQLite"}


def test_only_the_firm_decision_is_stored():
    """A possibility is never stored as a decision: only h4 is recorded."""
    _, _, sem = cons(S.HYPOTHESES)
    assert [d.value for d in sem.decisions] == ["SQLite"]
    assert sem.decisions[0].status == "active"
    assert E.decision_recall(sem, S.HYPOTHESES).value == 1.0
    assert E.decision_accuracy(sem, S.HYPOTHESES).value == 1.0


# ════════════════════════════════════════════════════════════════════
# Scenario 4 — invented relations
# ════════════════════════════════════════════════════════════════════

def test_no_relation_is_invented():
    _, _, sem = cons(S.RELATIONS)
    assert E.forbidden_relations_found(sem, S.RELATIONS).value == 0.0


def test_no_relation_is_invented_in_provenance_scenario():
    _, _, sem = cons(S.PROVENANCE)
    pairs = {(r.subject.lower(), r.object.lower()) for r in sem.relations}
    assert ("orion", "postgresql") in pairs or ("orion", "sqlite") in pairs  # sanity
    assert ("atlas", "postgresql") not in pairs          # never stated


def test_declared_relations_are_extracted():
    _, _, sem = cons(S.RELATIONS)
    assert E.relationship_recall(sem, S.RELATIONS).value == 1.0


def test_passive_and_active_relations_share_the_canonical_direction():
    """'SQLite is used by Orion' must yield the same triple as 'Orion uses SQLite'."""
    _, _, sem = cons(S.RELATIONS)
    triples = {(r.subject.lower(), r.predicate.lower(), r.object.lower())
               for r in sem.relations}
    assert ("orion", "uses", "sqlite") in triples
    assert ("sqlite", "used-by", "orion") not in triples    # never inverted


def test_relation_object_is_not_garbled():
    _, _, sem = cons(S.GARBLED)
    assert [(r.subject, r.object) for r in sem.relations] == [("Orion", "SQLite")]


def test_relation_object_never_swallows_a_connective():
    _, _, sem = cons(S.GARBLED)
    for r in sem.relations:
        assert r.object.lower() not in {"because", "since", "while", "although", "but", "and"}
        assert " because" not in r.object.lower()
    assert E.relationship_precision(sem, S.GARBLED).value == 1.0
    assert E.unsupported_claim_rate(sem, S.GARBLED).value == 0.0


# ════════════════════════════════════════════════════════════════════
# Scenario 5 — revised decisions
# ════════════════════════════════════════════════════════════════════

def test_revision_history_is_fully_retained():
    _, _, sem = cons(S.REVISIONS)
    contents = [i.content for i in sem.items]
    assert "We decided to use SQLite." in contents
    assert "We decided to use PostgreSQL." in contents
    assert E.historical_integrity(sem, S.REVISIONS).value == 1.0


def test_durable_references_survive_the_revision():
    _, em, sem = cons(S.REVISIONS)
    ep = episodes(em)[0]
    assert ep.decision_keys
    for key in ep.decision_keys:
        assert resolve_decision_key(sem, key) is not None


def test_decision_sources_are_not_invented():
    _, _, sem = cons(S.REVISIONS)
    allowed = set(S.REVISIONS.message_ids)
    assert sem.decisions
    for d in sem.decisions:
        assert d.source_ids
        assert set(d.source_ids) <= allowed


def test_decision_dates_are_not_invented():
    _, _, sem = cons(S.REVISIONS)
    assert sem.decisions
    assert all(d.date is None for d in sem.decisions)


def test_decision_date_comes_from_the_source():
    """A dated message yields that date; an undated one yields None."""
    dated = [{"role": "user", "content": "We decided to use SQLite.",
              "timestamp": 1700000000.0}]
    _, _, sem_dated = consolidate_to_episodes(dated, keep_recent=0)
    assert [d.date for d in sem_dated.decisions] == [1700000000.0]

    _, _, sem_undated = cons(S.REVISIONS)
    assert all(d.date is None for d in sem_undated.decisions)


def test_final_decision_is_identifiable():
    _, _, sem = cons(S.REVISIONS)
    assert sem.decisions[-1].value in S.REVISIONS.expected_decisions


def test_no_false_supersession_without_an_explicit_marker():
    """REVISIONS replaces SQLite by PostgreSQL with no explicit marker: nothing
    may be marked superseded on a mere wording resemblance."""
    _, _, sem = cons(S.REVISIONS)
    assert [d.value for d in sem.decisions] == ["SQLite", "PostgreSQL"]
    assert all(d.status == "active" for d in sem.decisions)


def test_explicit_supersession_marks_the_old_decision_only():
    _, _, sem = cons(S.SUPERSESSION)
    by_value = {d.value: d for d in sem.decisions}
    assert set(by_value) == {"SQLite", "PostgreSQL"}
    assert by_value["SQLite"].status == "superseded"     # replaced...
    assert by_value["PostgreSQL"].status == "active"     # ...by the new one
    assert E.historical_integrity(sem, S.SUPERSESSION).value == 1.0   # nothing deleted
    # the superseded decision is still resolvable from its durable reference
    assert by_value["SQLite"].source_ids == ["mid_e1"]
    assert by_value["PostgreSQL"].source_ids == ["mid_e2"]


# ════════════════════════════════════════════════════════════════════
# Scenario 6 — unknown information
# ════════════════════════════════════════════════════════════════════

def test_unknown_terms_are_refused():
    _, _, sem = cons(S.UNKNOWN)
    assert E.unknown_answer_accuracy(sem, S.UNKNOWN).value == 1.0
    for term in S.UNKNOWN.unknown_terms:
        ans = E.answer_question(sem, term)
        assert ans.status == E.UNKNOWN
        assert ans.evidence == []                        # no invented evidence


def test_plausible_term_absent_from_memory_is_not_supported():
    _, _, sem = cons(S.UNKNOWN)
    # 'PostgreSQL' is a plausible technology but was never mentioned
    assert E.answer_question(sem, "PostgreSQL").status == E.UNKNOWN


def test_known_term_is_supported():
    _, _, sem = cons(S.UNKNOWN)
    assert E.answer_question(sem, "sqlite").status == E.DIRECTLY_SUPPORTED


# ════════════════════════════════════════════════════════════════════
# Scenario 7 — provenance
# ════════════════════════════════════════════════════════════════════

def test_provenance_points_at_existing_messages():
    _, _, sem = cons(S.PROVENANCE)
    assert E.provenance_accuracy(sem, S.PROVENANCE).value == 1.0


def test_relation_provenance_is_exact():
    _, _, sem = cons(S.PROVENANCE)
    by_source = {(r.subject.lower(), r.object.lower()): r.source_id for r in sem.relations}
    assert by_source[("orion", "sqlite")] == "mid_p1"
    assert by_source[("atlas", "sqlite")] == "mid_p2"
    assert by_source[("orion", "postgresql")] == "mid_p3"


def test_same_value_different_subject_is_not_confused():
    _, _, sem = cons(S.PROVENANCE)
    sqlite_sources = sorted(r.source_id for r in sem.relations if r.object.lower() == "sqlite")
    assert sqlite_sources == ["mid_p1", "mid_p2"]        # Orion and Atlas kept apart


def test_entity_sources_accumulate():
    _, _, sem = cons(S.PROVENANCE)
    assert E.entity_source_completeness(sem, S.PROVENANCE).value == 1.0


def test_entity_sources_are_complete_and_deduplicated():
    _, _, sem = cons(S.PROVENANCE)
    orion = sem.entities["orion"]
    assert orion.source_ids == ["mid_p1", "mid_p3"]      # both messages kept
    assert len(orion.source_ids) == len(set(orion.source_ids))
    # repeated consolidation must not duplicate the sources either
    again = consolidate_to_episodes(list(S.PROVENANCE.messages), keep_recent=0)[-1]
    assert again.entities["orion"].source_ids == ["mid_p1", "mid_p3"]


def test_entity_sources_merge_across_messages():
    conv = [
        {"id": "m1", "role": "user", "content": "Orion uses SQLite."},
        {"id": "m2", "role": "user", "content": "Orion is a Python project."},
        {"id": "m3", "role": "user", "content": "Orion belongs to Alex."},
    ]
    sem = consolidate_to_episodes(conv, keep_recent=0)[-1]
    assert sem.entities["orion"].source_ids == ["mid_m1", "mid_m2", "mid_m3"]
    # provenance is not spread onto unrelated entities
    assert "mid_m3" not in sem.entities["sqlite"].source_ids


def test_entity_sources_do_not_leak_across_conversations():
    _, _, sem_a = cons(S.CONV_A)
    _, _, sem_b = cons(S.CONV_B)
    assert sem_a.entities and sem_b.entities
    for name, entity in sem_a.entities.items():
        assert "orion" in name or "alex" in name or "sqlite" in name
        assert set(entity.source_ids) <= set(S.CONV_A.message_ids)
    for name, entity in sem_b.entities.items():
        assert set(entity.source_ids) <= set(S.CONV_B.message_ids)


def test_native_ids_are_authoritative():
    _, _, sem = cons(S.PROVENANCE)
    for item in sem.items:
        assert item.source_ids[0].startswith("mid_")


def test_prepended_message_keeps_previous_ids():
    base = [{"id": "q1", "role": "user", "content": "Orion uses SQLite."}]
    prepended = [{"id": "q0", "role": "user", "content": "Unrelated opener."}] + base
    _, _, sem_base = consolidate_to_episodes(base, keep_recent=0)
    _, _, sem_all = consolidate_to_episodes(prepended, keep_recent=0)
    assert sem_base.items[0].source_ids == ["mid_q1"]
    assert "mid_q1" in [s for i in sem_all.items for s in i.source_ids]


def test_windowed_consolidation_keeps_shared_provenance():
    window = S.PROVENANCE.messages
    _, em_a, _ = consolidate_to_episodes(list(window[:2]), keep_recent=0, conversation_id="W")
    _, em_b, _ = consolidate_to_episodes(list(window[1:]), keep_recent=0, conversation_id="W")
    ids_a = set(episodes(em_a)[0].message_ids)
    ids_b = set(episodes(em_b)[0].message_ids)
    assert "mid_p2" in ids_a and "mid_p2" in ids_b


def test_reload_preserves_provenance(tmp_path):
    _, em, sem = cons(S.PROVENANCE)
    rc, _, _ = consolidate_to_episodes([], keep_recent=0)   # empty recent context
    path = tmp_path / "state.json"
    assert MemoryPersistence().save_state(rc, sem, em, path=path) is True
    _, em2, sem2 = MemoryPersistence().load_state(path=path)
    assert [e.id for e in episodes(em2)] == [e.id for e in episodes(em)]
    assert [i.source_ids for i in sem2.items] == [i.source_ids for i in sem.items]


# ════════════════════════════════════════════════════════════════════
# Scenario 8 — deleted / obsolete information
# ════════════════════════════════════════════════════════════════════

def test_nothing_is_silently_deleted():
    _, _, sem = cons(S.REVISIONS)
    assert len(sem.items) == len(S.REVISIONS.messages)


def test_replaced_information_never_becomes_the_only_truth():
    _, _, sem = cons(S.CONTRADICTION)
    # the OLD claim is still stored next to the new one
    assert sum(1 for i in sem.items if "SQLite" in i.content) == 1
    assert sum(1 for i in sem.items if "PostgreSQL" in i.content) == 2


def test_unknown_stays_unknown_across_consolidations():
    _, _, sem = cons(S.UNKNOWN)
    _, _, other = cons(S.CONV_A)
    assert E.answer_question(sem, "Zephyr").status == E.UNKNOWN
    assert E.answer_question(other, "Zephyr").status == E.UNKNOWN


# ════════════════════════════════════════════════════════════════════
# Phase E — question/answer classification (data-level, NO LLM)
# ════════════════════════════════════════════════════════════════════

def test_answer_classifier_covers_all_six_statuses():
    # 1. directly supported
    _, _, sem = cons(S.UNKNOWN)
    assert E.answer_question(sem, "sqlite").status == E.DIRECTLY_SUPPORTED
    # 2. partially supported (term present, no matching entity)
    assert E.answer_question(sem, "uses").status == E.PARTIALLY_SUPPORTED
    # 3. contradictory (two stored statements about the same term)
    _, _, sem_c = cons(S.CONTRADICTION)
    assert E.answer_question(sem_c, "orion").status == E.CONTRADICTORY
    # 4. unknown
    assert E.answer_question(sem, "Zephyr").status == E.UNKNOWN
    # 5. obsolete (decision superseded by a later one) — hand-built data
    built = SemanticMemory()
    built.add_item(MemoryItem(id="i1", content="We use SQLite.", source_ids=["mid_z1"]))
    built.add_decision(Decision(value="SQLite", source_ids=["mid_z1"]))
    built.add_decision(Decision(value="PostgreSQL", source_ids=["mid_z2"]))
    assert E.answer_question(built, "sqlite").status == E.OBSOLETE
    # 6. ambiguous (only a decision mentions the term, nothing else)
    only_decision = SemanticMemory()
    only_decision.add_decision(Decision(value="Redis", source_ids=["mid_z3"]))
    assert E.answer_question(only_decision, "redis").status == E.AMBIGUOUS


def test_answer_carries_evidence():
    _, _, sem = cons(S.UNKNOWN)
    ans = E.answer_question(sem, "sqlite")
    assert ans.evidence
    assert set(ans.evidence) <= set(S.UNKNOWN.message_ids)


# ════════════════════════════════════════════════════════════════════
# Phase F — resilience
# ════════════════════════════════════════════════════════════════════

def test_incomplete_json_does_not_raise(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"version": 2, "recent": {"messages": [', encoding="utf-8")
    rc, em, sem = MemoryPersistence().load_state(path=path)
    assert rc.messages == []
    assert em.episodes == []
    assert sem.items == []


def test_message_without_content_creates_no_fact():
    sem = consolidate_to_episodes(
        [{"role": "user"}, {"role": "user", "content": ""},
         {"id": "x", "role": "user", "content": "Real fact."}],
        keep_recent=0,
    )[-1]
    assert [i.content for i in sem.items] == ["Real fact."]


def test_non_dict_entries_are_skipped_safely():
    sem = consolidate_to_episodes(
        [None, 42, "text", {"id": "ok", "role": "user", "content": "Koan uses Rust."}],
        keep_recent=0,
    )[-1]
    assert [i.content for i in sem.items] == ["Koan uses Rust."]


def test_invalid_timestamps_are_not_invented():
    _, em, sem = consolidate_to_episodes(
        [{"role": "user", "content": "A uses SQLite.", "timestamp": "not-a-number"},
         {"role": "assistant", "content": "B uses PostgreSQL.", "timestamp": None}],
        keep_recent=0,
    )
    for ep in episodes(em):
        assert ep.starts_at is None and ep.ends_at is None   # no fabricated dates


def test_duplicate_native_ids_collapse_provenance():
    """Documented limit: native ids are authoritative and duplicates are not
    disambiguated, so two distinct messages share one id."""
    sem = consolidate_to_episodes(
        [{"id": "dup", "role": "user", "content": "First message."},
         {"id": "dup", "role": "user", "content": "Second message."}],
        keep_recent=0,
    )[-1]
    assert len(sem.items) == 2
    assert sem.items[0].source_ids == sem.items[1].source_ids == ["mid_dup"]


def test_conversation_without_id_is_deterministic():
    conv = [{"role": "user", "content": "Orion uses SQLite."}]
    _, _, sem1 = consolidate_to_episodes(conv, keep_recent=0)
    _, _, sem2 = consolidate_to_episodes(conv, keep_recent=0)
    assert sem1.items[0].source_ids == sem2.items[0].source_ids


def test_decision_without_source_is_flagged_unsupported():
    sem = SemanticMemory()
    sem.add_decision(Decision(value="SQLite"))
    metric = E.unsupported_claim_rate(sem, S.UNKNOWN)
    assert metric.numerator >= 1                         # no source -> unsupported


def test_repeated_consolidation_is_idempotent():
    _, em, _ = cons(S.PROVENANCE)
    before = [e.id for e in episodes(em)]
    _, em, _ = consolidate_to_episodes(list(S.PROVENANCE.messages), keep_recent=0,
                                       conversation_id=S.PROVENANCE.conversation, episodic=em)
    assert [e.id for e in episodes(em)] == before


def test_message_order_does_not_change_retained_facts():
    messages = list(S.PROVENANCE.messages)
    sem_fwd = consolidate_to_episodes(messages, keep_recent=0)[-1]
    sem_rev = consolidate_to_episodes(list(reversed(messages)), keep_recent=0)[-1]
    assert {i.content for i in sem_fwd.items} == {i.content for i in sem_rev.items}


def test_legacy_data_remains_readable(tmp_path):
    state = {
        "version": 2, "recent": {"messages": []},
        "episodic": {"episodes": [{"id": "ep_old", "summary": "old",
                                   "message_ids": ["msg_0", "msg_1"]}]},
        "semantic": {"items": [], "entities": {}, "relations": [],
                     "decisions": [{"value": "Use SQLite", "reason": "x",
                                    "alternatives": [], "date": 1.0, "status": "active"}],
                     "contradictions": []},
        "saved_at": 1,
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    _, em, sem = MemoryPersistence().load_state(path=path)
    assert episodes(em)[0].message_ids == ["msg_0", "msg_1"]
    assert sem.decisions[0].value == "Use SQLite"
    assert sem.decisions[0].id == ""                     # no fabricated id


def test_partially_corrupted_data_only_keeps_valid_entries():
    messages = [
        {"id": "v1", "role": "user", "content": "Valid one uses SQLite."},
        None,
        {"role": "user", "content": ""},
        {"id": "v2", "role": "user", "content": "Valid two uses PostgreSQL."},
    ]
    sem = consolidate_to_episodes(messages, keep_recent=0)[-1]
    contents = [i.content for i in sem.items]
    assert contents == ["Valid one uses SQLite.", "Valid two uses PostgreSQL."]
    for item in sem.items:                               # nothing fabricated
        assert item.source_ids[0] in {"mid_v1", "mid_v2"}


# ════════════════════════════════════════════════════════════════════
# Metrics report
# ════════════════════════════════════════════════════════════════════

def test_metric_report_renders_every_scenario():
    report = E.scenario_report()
    for gt in S.ALL_SCENARIOS:
        assert f"### {gt.name}" in report
    assert "fact_recall" in report
    assert "entity_source_completeness" in report
    assert "cross_conversation_leakage" in report


def test_every_metric_exposes_its_denominator():
    """A metric that cannot be measured must say so (value is None) rather
    than silently report a perfect score."""
    _, _, sem = cons(S.RELATIONS)              # no decision is declared here
    m = E.decision_accuracy(sem, S.RELATIONS)
    assert m.denominator == 0
    assert m.value is None


# ════════════════════════════════════════════════════════════════════
# Phase B/C/D/E — reliability repairs (independent ground truth)
# ════════════════════════════════════════════════════════════════════

FIRM_UTTERANCES = [
    ("We decided to use SQLite.", "SQLite", "active"),
    ("We chose PostgreSQL.", "PostgreSQL", "active"),
    ("Let's use SQLite for the database.", "SQLite", "active"),
    ("We agreed on Kafka.", "Kafka", "active"),
    ("We rejected MongoDB.", "MongoDB", "rejected"),
    ("We ruled out Redis.", "Redis", "rejected"),
    ("We will use SQLite.", "SQLite", "proposed"),
]


@pytest.mark.parametrize("text,value,status", FIRM_UTTERANCES)
def test_firm_utterances_are_classified_with_the_right_value_and_status(text, value, status):
    assert classify_decision(text) == [(value, status)]
    assert [(d.value, d.status) for d in _extract_decisions(text)] == [(value, status)]
    assert status in DECISION_STATUSES


NON_FIRM_UTTERANCES = [
    "We could use Redis.",
    "We might migrate to PostgreSQL.",
    "We are considering Redis.",
    "Maybe Alex will manage the backend.",
    "Perhaps we should use Kafka.",
    "I think PostgreSQL would be preferable.",
]


@pytest.mark.parametrize("text", NON_FIRM_UTTERANCES)
def test_non_firm_utterances_are_never_stored_as_decisions(text):
    classified = classify_decision(text)
    assert classified, "the modality must still be recognised"
    _value, status = classified[0]
    assert status in {"tentative", "unknown"}   # the modality is seen...
    assert _extract_decisions(text) == []       # ...but nothing is stored


def test_relation_direction_is_normalised_for_passive_forms():
    conv = [{"id": "d1", "role": "user", "content": "SQLite is used by Orion."}]
    sem = consolidate_to_episodes(conv, keep_recent=0)[-1]
    assert [(r.subject, r.predicate, r.object) for r in sem.relations] == [
        ("Orion", "uses", "SQLite")
    ]
    assert sem.relations[0].source_id == "mid_d1"


def test_ambiguous_subjects_do_not_produce_relations():
    conv = [{"id": "a1", "role": "user", "content": "This uses SQLite."}]
    assert consolidate_to_episodes(conv, keep_recent=0)[-1].relations == []


def test_malformed_relation_text_produces_nothing():
    conv = [{"id": "x1", "role": "user", "content": "uses because since although"}]
    sem = consolidate_to_episodes(conv, keep_recent=0)[-1]
    assert sem.relations == []


def test_conversation_identity_survives_reload(tmp_path):
    opener = "We are starting the project now."
    conv_a = [{"role": "user", "content": opener}, {"role": "user", "content": "Alpha uses SQLite."}]
    conv_b = [{"role": "user", "content": opener}, {"role": "user", "content": "Beta uses PostgreSQL."}]
    rc, em, sm = consolidate_to_episodes(conv_a, keep_recent=0)
    _, em, _ = consolidate_to_episodes(conv_b, keep_recent=0, episodic=em)
    ids_before = sorted(e.id for e in episodes(em))
    assert len(ids_before) == 2
    path = tmp_path / "state.json"
    MemoryPersistence().save_state(rc, sm, em, path=path)
    _, em2, _ = MemoryPersistence().load_state(path=path)
    assert sorted(e.id for e in episodes(em2)) == ids_before
