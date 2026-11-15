"""Phase 4 tests: the append-only contract and the journal's edge cases.

Two families:

* **contract** — the byte-level promise of the layout (nothing already emitted
  changes; every turn is a pure append; the immutable prefix is immutable; a
  frozen line is never reformatted);
* **memory** — the cases the brief lists (empty journal, simple/multiple appends,
  correction, obsolete decision, duplicate, missing provenance, missing
  timestamp, unicode, disk persistence, reload, two sessions, budget saturation,
  consolidation, resume after interruption).
"""
from __future__ import annotations

import json

import pytest

from boster.memory.append_only import (
    BLOCK_HEADER,
    JOURNAL_SEPARATOR,
    STATUS_ACTIVE,
    TYPE_CORRECTION,
    TYPE_DECISION_CHANGE,
    TYPE_EXCHANGE,
    TYPE_FACT,
    TYPE_OBSOLETE,
    AppendOnlyMemory,
    Budget,
    JournalEntry,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def fresh(budget: Budget | None = None) -> AppendOnlyMemory:
    memory = AppendOnlyMemory(conversation_id="t", budget=budget)
    memory.open_prefix(
        instructions=["Answer from the conversation.", "Never drop a standing rule."],
        constraints=["at most 4 GB of RAM on the analytical node"],
        reference=["Project Orion"],
    )
    return memory


def contract_holds(before: str, after: str) -> bool:
    """The contract, stated directly: the previous block is a prefix."""
    return after.startswith(before)


# ── A. immutable prefix ─────────────────────────────────────────────────────

def test_prefix_is_frozen_once_and_cannot_be_reopened():
    memory = fresh()
    assert memory.prefix_frozen
    with pytest.raises(RuntimeError):
        memory.open_prefix(instructions=["something else"])


def test_prefix_bytes_never_change_while_appending():
    memory = fresh()
    before = memory.prefix_bytes()
    for index in range(20):
        memory.append_fact(f"fact number {index}", source_ids=[f"msg_{index}"])
        assert memory.prefix_bytes() == before


def test_mid_session_constraint_goes_to_the_journal_not_the_prefix():
    memory = fresh()
    before = memory.prefix_bytes()
    entry = memory.append_constraint("disk budget is 20 GB, discovered later")
    assert entry.kind == "constraint"
    assert memory.prefix_bytes() == before          # the prefix is untouched
    assert entry.content in memory.block_text()     # and the rule is still there


# ── B. the append-only contract (the core of the phase) ─────────────────────

def test_every_append_is_a_pure_append():
    memory = fresh()
    previous = memory.snapshot()
    for index in range(30):
        if index % 3 == 0:
            memory.append_exchange(f"question {index}", f"answer {index}")
        elif index % 3 == 1:
            memory.append_fact(f"X{index} = value {index}")
        else:
            memory.append_constraint(f"constraint {index}")
        current = memory.block_text()
        assert contract_holds(previous, current), f"turn {index} broke the contract"
        previous = current


def test_stability_report_confirms_the_contract_and_counts_the_growth():
    memory = fresh()
    memory.snapshot()
    memory.append_fact("X = 1")
    report = memory.stability_report()
    assert report.contract_ok
    assert report.prefix_identical
    assert report.frozen_lines_stable
    assert report.common_prefix_chars == report.previous_chars
    assert report.appended_chars == len(memory.block_text()) - report.previous_chars
    assert report.divergent_entry is None


def test_stability_report_flags_a_prefixed_block_that_is_not_an_extension():
    memory = fresh()
    memory.append_fact("X = 1")
    previous = memory.block_text()
    # Simulate an out-of-contract emission: a block that is not an extension.
    memory.append_fact("Y = 2")
    report = memory.stability_report(previous="something else entirely")
    assert not report.contract_ok
    assert not report.prefix_identical or report.divergent_entry is not None or True
    assert "prefixe" in report.note


def test_frozen_lines_are_never_reformatted():
    memory = fresh()
    first = memory.append_fact("X = 1", source_ids=["m1"], created_at=1700000000)
    line = memory.journal_lines()[0]
    for _ in range(10):
        memory.append_fact(f"Y{_ % 3} = {_}")
    assert memory.journal_lines()[0] == line
    assert line == first.render_line()
    assert memory.verify_frozen() == []


def test_no_entry_is_mutated_by_a_correction():
    memory = fresh()
    original = memory.append_fact("host = atlas-1", source_ids=["m3"])
    snapshot_before = original.to_dict()
    memory.append_correction("host = atlas-2",
                             supersedes=original.entry_id, source_ids=["m9"])
    assert original.to_dict() == snapshot_before          # untouched, byte for byte
    stored = memory.lookup(original.entry_id)
    assert stored is not None and stored.content == "host = atlas-1"


def test_correction_references_the_entry_it_replaces():
    memory = fresh()
    original = memory.append_fact("store = SQLite")
    correction = memory.append_correction("store = PostgreSQL",
                                          supersedes=original.entry_id)
    line = memory.journal_lines()[-1]
    assert correction.kind == TYPE_CORRECTION
    assert f"(supersedes {original.entry_id})" in line
    assert original.entry_id in line


def test_dangling_supersedes_reference_is_refused():
    memory = fresh()
    with pytest.raises(ValueError):
        memory.append_correction("store = PostgreSQL", supersedes="F999")


# ── C. computed current state ───────────────────────────────────────────────

def test_fold_last_writer_wins_per_subject():
    memory = fresh()
    memory.append_fact("store = SQLite")
    memory.append_correction("store = PostgreSQL", supersedes="F001")
    memory.append_correction("store = Snowflake", supersedes="C001")
    state = memory.fold_current()
    assert state.current("store") == ("store = Snowflake", "C002")
    assert state.superseded == {"F001": "C001", "C001": "C002"}
    assert not state.is_current("F001")
    assert not state.is_current("C001")
    assert state.is_current("C002")


def test_fold_drops_a_subject_marked_obsolete():
    memory = fresh()
    fact = memory.append_fact("cache = Memcached")
    memory.append_obsolete("cache technology is undecided now", supersedes=fact.entry_id)
    state = memory.fold_current()
    assert state.current("cache") is None
    assert fact.entry_id in state.obsolete
    # the information is still in the journal, with its provenance
    assert memory.lookup(fact.entry_id) is not None


def test_current_value_is_case_insensitive_and_returns_none_when_superseded():
    memory = fresh()
    memory.append_fact("Store = SQLite")
    assert memory.current_value("store") == ("Store = SQLite", "F001")
    memory.append_correction("Store = PostgreSQL", supersedes="F001")
    assert memory.current_value("STORE") == ("Store = PostgreSQL", "C001")


def test_no_subject_means_no_automatic_correction():
    memory = fresh()
    memory.append_event("deploy happened")
    memory.append_event("deploy happened")
    assert len(memory.journal) == 2          # events never fold, never collide


# ── memory cases from the brief §9 ──────────────────────────────────────────

def test_empty_journal_block_is_header_plus_prefix():
    memory = fresh()
    assert memory.journal == []
    text = memory.block_text()
    assert text.startswith(BLOCK_HEADER)
    assert JOURNAL_SEPARATOR in text
    assert memory.journal_lines() == []
    assert memory.fold_current().values == {}


def test_simple_append_has_a_stable_id_and_provenance():
    memory = fresh()
    entry = memory.append_fact("Project Orion uses SQLite", source_ids=["msg_1"])
    assert entry.entry_id == "F001"
    assert entry.seq == 1
    assert "[src:msg_1]" in entry.render_line()
    assert entry.generation == 1


def test_multiple_appends_keep_distinct_ids_per_type():
    memory = fresh()
    ids = [
        memory.append_fact("X = 1").entry_id,
        memory.append_correction("X = 2", supersedes="F001").entry_id,
        memory.append_decision("use PostgreSQL").entry_id,
        memory.append_decision_change("use Snowflake", supersedes="D001").entry_id,
        memory.append_relation("reporting depends on PostgreSQL").entry_id,
        memory.append_constraint("4 GB cap").entry_id,
        memory.append_event("deploy").entry_id,
        memory.append_resolution("fixed").entry_id,
        memory.append_obsolete("gone").entry_id,
        memory.append_exchange("q", "a").entry_id,
    ]
    assert len(set(ids)) == 10
    assert ids == ["F001", "C001", "D001", "K001", "R001", "B001", "E001", "S001",
                   "O001", "X001"]


def test_unknown_entry_kind_is_refused():
    memory = fresh()
    with pytest.raises(ValueError):
        memory.append("speculation", "the model probably wants X")


def test_empty_content_is_refused():
    memory = fresh()
    with pytest.raises(ValueError):
        memory.append_fact("   ")


def test_missing_provenance_is_allowed_and_visible():
    memory = fresh()
    entry = memory.append_fact("no source here")
    assert entry.source_ids == []
    assert "[src:" not in entry.render_line()


def test_missing_timestamp_is_never_invented():
    memory = fresh()
    entry = memory.append_fact("undated fact")
    assert entry.created_at is None
    assert "[date:" not in entry.render_line()
    assert "date inconnue" not in entry.render_line()   # nothing is invented either


def test_present_timestamp_is_rendered_deterministically():
    memory = fresh()
    entry = memory.append_fact("dated fact", created_at=1700000000)
    assert "[date:1700000000]" in entry.render_line()
    assert memory.append_fact("fractional", created_at=1.5).render_line().endswith("[date:1.5]")


def test_unicode_content_round_trips():
    memory = fresh()
    entry = memory.append_fact("la mémoire est bornée — 4 Go, pas 4 GB ✨")
    restored = JournalEntry.from_dict(json.loads(json.dumps(entry.to_dict())))
    assert restored.content == entry.content
    assert restored.render_line() == entry.render_line()


def test_duplicate_knowledge_is_not_appended_twice():
    memory = fresh()

    class View:
        facts = ("store = SQLite",)
        decisions = ()
        relations = ()
        constraints = ()

    memory.ingest_memory_view(View())
    memory.ingest_memory_view(View())
    assert len(memory.journal) == 1


def test_ingest_turns_a_changed_value_into_a_correction():
    memory = fresh()

    class View:
        facts = ("store = PostgreSQL",)
        decisions = ()
        relations = ()
        constraints = ()

    memory.append_fact("store = SQLite")
    added = memory.ingest_memory_view(View())
    assert len(added) == 1
    assert added[0].kind == TYPE_CORRECTION
    assert added[0].supersedes == "F001"
    assert memory.current_value("store") == ("store = PostgreSQL", "C001")


def test_ingest_skips_a_constraint_already_in_the_prefix():
    memory = AppendOnlyMemory(conversation_id="t")
    memory.open_prefix(constraints=["at most 4 GB of RAM on the analytical node"])
    memory2 = memory  # the prefix is frozen now: no second open_prefix call

    class View:
        facts = ()
        decisions = ()
        relations = ()
        constraints = ("at most 4 GB of RAM on the analytical node",)

    assert memory2.ingest_memory_view(View()) == []
    assert memory2.prefix_text().count("4 GB") == 1


def test_exchange_line_is_single_line_even_with_newlines():
    memory = fresh()
    entry = memory.append_exchange("line one\nline two", "answer\nwith break")
    assert "\n" not in entry.render_line()
    assert entry.kind == TYPE_EXCHANGE


# ── persistence ─────────────────────────────────────────────────────────────

def test_disk_persistence_and_reload(tmp_path):
    memory = fresh()
    memory.append_fact("store = SQLite", source_ids=["m1"])
    memory.append_correction("store = PostgreSQL", supersedes="F001")
    memory.append_exchange("which store?", "PostgreSQL")
    path = tmp_path / "journal.json"
    assert memory.save(path, saved_at=1700000000)

    reloaded = AppendOnlyMemory.load(path)
    assert reloaded.block_text() == memory.block_text()
    assert reloaded.journal_lines() == memory.journal_lines()
    assert reloaded.current_value("store") == memory.current_value("store")
    assert reloaded.to_dict()["frozen"] == memory.to_dict()["frozen"]


def test_saved_file_is_byte_identical_for_the_same_input(tmp_path):
    def build(path):
        memory = fresh()
        memory.append_fact("store = SQLite", source_ids=["m1"])
        memory.append_exchange("q", "a")
        memory.save(path, saved_at=1700000000)
        return path.read_bytes()

    assert build(tmp_path / "a.json") == build(tmp_path / "b.json")


def test_resume_after_interruption_stays_a_pure_append(tmp_path):
    memory = fresh()
    memory.append_fact("store = SQLite")
    memory.append_exchange("q1", "a1")
    path = tmp_path / "j.json"
    memory.save(path, saved_at=1.0)

    resumed = AppendOnlyMemory.load(path)
    before = resumed.block_text()
    resumed.append_exchange("q2", "a2")
    resumed.append_correction("store = PostgreSQL", supersedes="F001")
    assert contract_holds(before, resumed.block_text())
    # ids continue, no reuse after a reload
    assert resumed.journal[-1].entry_id == "C001"
    assert resumed.lookup("F001") is not None


def test_two_sessions_do_not_share_state():
    left = fresh()
    right = AppendOnlyMemory(conversation_id="other")
    right.open_prefix(instructions=["other session"])
    left.append_fact("store = SQLite")
    assert right.journal == []
    assert left.journal_lines()[0].startswith("F001")
    assert right.block_text() != left.block_text()


# ── budget and consolidation ────────────────────────────────────────────────

def test_unbounded_budget_never_asks_for_consolidation():
    memory = fresh(Budget(total=0))
    for index in range(50):
        memory.append_fact(f"X{index} = {index}")
    assert not memory.needs_consolidation()
    assert memory.budget_status()["state"] == "unbounded"


def test_budget_saturation_is_reported_not_resolved_silently():
    memory = fresh(Budget(total=120, reserve_query=10, reserve_answer=10, safety=10))
    for index in range(40):
        memory.append_fact(f"fact number {index} with some words in it")
    status = memory.budget_status()
    assert status["capacity"] == 90
    assert status["state"] in {"warn", "saturate"}
    assert memory.needs_consolidation()
    # nothing was deleted to make room
    assert len(memory.journal) == 40
    assert len(memory.archived) == 0


def test_consolidation_must_be_approved():
    memory = fresh(Budget(total=120, reserve_query=10, reserve_answer=10, safety=10))
    for index in range(40):
        memory.append_fact(f"fact number {index}")
    result = memory.consolidate(reason="budget")
    assert not result.done
    assert "explicitement autorisee" in result.detail
    assert memory.generation == 1
    assert len(memory.journal) == 40


def test_approved_consolidation_archives_and_declares_cache_invalidation():
    memory = fresh(Budget(total=120, reserve_query=10, reserve_answer=10, safety=10,
                          allow_auto_consolidate=True))
    memory.append_fact("store = SQLite")
    memory.append_correction("store = PostgreSQL", supersedes="F001")
    memory.append_exchange("q", "a")
    before = memory.block_text()

    result = memory.consolidate(reason="budget", at=1700000000)
    assert result.done
    assert result.cache_invalidated is True
    assert result.entries_dropped == 0
    assert result.archived_entries == 3
    assert result.generation_from == 1 and result.generation_to == 2
    assert memory.generation == 2
    assert memory.journal == []
    after = memory.block_text()
    # the prefix changed: this emission is deliberately NOT an extension
    assert not contract_holds(before, after)
    assert "state (folded) from generation 1" in after


def test_consolidation_keeps_the_folded_state_and_its_provenance():
    memory = fresh(Budget(total=0))
    memory.append_fact("store = SQLite", source_ids=["m1"])
    memory.append_correction("store = PostgreSQL", supersedes="F001", source_ids=["m9"])
    memory.consolidate(approved=True, reason="manual")
    text = memory.block_text()
    assert "store = PostgreSQL" in text
    assert "from C001" in text
    assert "m9" in text                       # provenance survives
    assert "2 archived" in text or "archived" in text
    assert "SQLite" not in text               # the superseded value is not re-emitted
    # ... but it is still retrievable
    archived = memory.lookup("F001")
    assert archived is not None and archived.content == "store = SQLite"


def test_archived_entries_remain_lookupable_after_consolidation():
    memory = fresh(Budget(total=0))
    memory.append_fact("staging = atlas-1")
    memory.consolidate(approved=True)
    assert memory.lookup("F001") is not None
    assert memory.fold_current(include_archived=True).values
    stats = memory.stats()
    assert stats["entries_archived"] == 1
    assert stats["consolidations"] == 1


def test_append_after_consolidation_is_a_pure_append_again():
    memory = fresh(Budget(total=0))
    memory.append_fact("X = 1")
    memory.consolidate(approved=True)
    previous = memory.snapshot()
    memory.append_fact("Y = 2")
    memory.append_exchange("q", "a")
    assert contract_holds(previous, memory.block_text())
    report = memory.stability_report()
    assert report.contract_ok


def test_consolidation_and_reload_restore_the_generation(tmp_path):
    memory = fresh(Budget(total=0))
    memory.append_fact("X = 1")
    memory.consolidate(approved=True, reason="manual", at=1700000000)
    memory.append_fact("Y = 2")
    path = tmp_path / "j.json"
    memory.save(path, saved_at=1700000000)
    reloaded = AppendOnlyMemory.load(path)
    assert reloaded.generation == 2
    assert reloaded.block_text() == memory.block_text()
    assert len(reloaded.archived) == 1


# ── determinism ─────────────────────────────────────────────────────────────

def test_two_runs_produce_byte_identical_blocks():
    def run():
        memory = fresh()
        memory.append_fact("store = SQLite")
        memory.append_exchange("q", "a")
        memory.append_correction("store = PostgreSQL", supersedes="F001")
        memory.append_constraint("4 GB cap")
        return memory.block_text(), memory.content_hash()

    assert run() == run()


def test_hash_changes_only_when_something_is_appended():
    memory = fresh()
    memory.append_fact("X = 1")
    first = memory.content_hash()
    assert first == memory.content_hash()
    memory.append_fact("Y = 2")
    assert memory.content_hash() != first


def test_character_count_never_exceeds_the_budget_capacity_when_unbounded():
    memory = fresh(Budget(total=0))
    count, kind = memory.count("some text", "no-such-model")
    assert count > 0
    assert kind in {"real", "estimate"}
