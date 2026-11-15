"""Real on-disk persistence tests for Boster's three-tier memory.

These tests exercise the *disk* round-trip via ``MemoryPersistence.save_state``
/``load_state`` using a temporary directory. A plain ``to_dict()``/``from_dict()``
in-memory check is NOT a persistence test and is deliberately avoided here.
"""
from __future__ import annotations

import json

import pytest

from boster.memory.models import (
    Contradiction,
    Decision,
    Entity,
    EpisodicMemory,
    MemoryItem,
    RecentContext,
    Relation,
    SemanticMemory,
)
from boster.memory.persistence import MemoryPersistence, SCHEMA_VERSION


def _full_state():
    """Build a three-tier state containing every supported level + provenance."""
    sm = SemanticMemory()
    sm.add_item(MemoryItem(id="fact1", content="Orion uses Python",
                           kind="fact", source_ids=["msg_0"]))
    sm.upsert_entity(Entity(name="Orion", kind="project", source_ids=["msg_0"]))
    sm.upsert_entity(Entity(name="Python", kind="tool", source_ids=["msg_0"]))
    sm.add_relation(Relation(subject="Orion", predicate="uses", object="Python",
                             source_id="msg_0"))
    sm.add_decision(Decision(value="Use SQLite", reason="lightweight", status="active"))
    sm.add_contradiction(Contradiction(id="c1", claim_a="Use PostgreSQL",
                                       claim_b="Use SQLite",
                                       source_a="msg_1", source_b="msg_2",
                                       resolved=False))
    sm.add_contradiction(Contradiction(id="c2", claim_a="Redis", claim_b="Memcached",
                                       source_a="msg_3", source_b="msg_4",
                                       resolved=True, resolution="Redis chosen"))

    rc = RecentContext()
    rc.add({"role": "user", "content": "Hello"})
    rc.add({"role": "assistant", "content": "Hi"})

    em = EpisodicMemory()
    em.add_episode({"type": "decision", "content": "Chose SQLite"})

    return rc, em, sm


class TestDiskRoundTrip:
    """Full save/load on real files in a temporary directory."""

    def test_all_levels_survive_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        rc, em, sm = _full_state()

        assert MemoryPersistence().save_state(rc, sm, em, path=path) is True
        assert path.exists()

        # Reload from disk with a fresh instance.
        rc2, em2, sm2 = MemoryPersistence().load_state(path=path)

        # RecentContext
        assert [m["content"] for m in rc2.messages] == ["Hello", "Hi"]
        # EpisodicMemory
        assert len(em2.episodes) == 1
        assert em2.episodes[0]["content"] == "Chose SQLite"
        # SemanticMemory: every level
        assert [i.id for i in sm2.items] == ["fact1"]
        assert set(sm2.entities) == {"orion", "python"}
        assert len(sm2.relations) == 1
        assert len(sm2.decisions) == 1
        assert len(sm2.contradictions) == 2

    def test_provenance_preserved(self, tmp_path):
        path = tmp_path / "state.json"
        rc, em, sm = _full_state()
        MemoryPersistence().save_state(rc, sm, em, path=path)
        _, _, sm2 = MemoryPersistence().load_state(path=path)

        assert sm2.items[0].source_ids == ["msg_0"]
        assert sm2.entities["orion"].source_ids == ["msg_0"]
        assert sm2.relations[0].source_id == "msg_0"
        # Contradiction source refs on both claims
        c1 = next(c for c in sm2.contradictions if c.id == "c1")
        assert c1.source_a == "msg_1" and c1.source_b == "msg_2"

    def test_contradiction_states_preserved(self, tmp_path):
        path = tmp_path / "state.json"
        rc, em, sm = _full_state()
        MemoryPersistence().save_state(rc, sm, em, path=path)
        _, _, sm2 = MemoryPersistence().load_state(path=path)

        by_id = {c.id: c for c in sm2.contradictions}
        assert by_id["c1"].resolved is False
        assert by_id["c2"].resolved is True
        assert by_id["c2"].resolution == "Redis chosen"

    def test_schema_version_written(self, tmp_path):
        path = tmp_path / "state.json"
        rc, em, sm = _full_state()
        MemoryPersistence().save_state(rc, sm, em, path=path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["version"] == SCHEMA_VERSION
        assert "recent" in doc and "episodic" in doc and "semantic" in doc

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        path = tmp_path / "state.json"
        rc, em, sm = _full_state()
        MemoryPersistence().save_state(rc, sm, em, path=path)
        assert not (tmp_path / "state.json.tmp").exists()


class TestLegacyMigration:
    """Legacy v1 flat JSON must migrate non-destructively."""

    def test_legacy_entries_migrate_to_semantic_items(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({
            "entries": [
                {"id": "old1", "content": "Old fact", "category": "fact",
                 "importance": 0.8, "tags": ["msg_1"]},
                {"id": "old2", "content": "Old pref", "category": "preference",
                 "importance": 0.4},
            ],
            "saved_at": 1,
        }), encoding="utf-8")

        rc, em, sm = MemoryPersistence().load_state(path=path)

        assert len(sm.items) == 2
        assert sm.items[0].content == "Old fact"
        assert sm.items[0].kind == "fact"
        assert sm.items[0].source_ids == ["msg_1"]   # tags -> source_ids
        assert sm.items[0].confidence == 0.8          # importance -> confidence
        assert sm.items[1].kind == "preference"

    def test_legacy_file_backed_up(self, tmp_path):
        path = tmp_path / "legacy.json"
        original = json.dumps({"entries": [{"id": "o", "content": "c", "category": "fact"}]})
        path.write_text(original, encoding="utf-8")

        MemoryPersistence().load_state(path=path)

        backup = tmp_path / "legacy.json.v1.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original  # byte-faithful copy


class TestFailureModes:
    """Missing / malformed / unrecognised files -> empty, never raise."""

    def test_missing_file(self, tmp_path):
        rc, em, sm = MemoryPersistence().load_state(path=tmp_path / "nope.json")
        assert rc.messages == [] and em.episodes == []
        assert sm.items == [] and sm.contradictions == []

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        rc, em, sm = MemoryPersistence().load_state(path=path)
        assert sm.items == [] and rc.messages == []

    def test_unrecognised_format(self, tmp_path):
        path = tmp_path / "weird.json"
        path.write_text(json.dumps({"unrelated": True}), encoding="utf-8")
        rc, em, sm = MemoryPersistence().load_state(path=path)
        assert sm.items == []
