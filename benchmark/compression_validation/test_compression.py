"""Compression validation tests for Boster memory system.

Tests that compression preserves important information while reducing token count.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from boster.memory.models import MemoryItem, Entity, Relation, Decision, Contradiction, RecentContext, SemanticMemory
from boster.memory.consolidation import consolidate_exchanges, consolidate_similar


class TestCompressionPreservation:
    """Test that compression preserves important information."""

    def test_facts_preserved(self):
        """Facts should be preserved after consolidation."""
        exchanges = [
            {"role": "user", "content": "Project Orion uses Python and SQLite."},
            {"role": "assistant", "content": "Noted: Orion = Python + SQLite."},
            {"role": "user", "content": "Alex is the lead developer."},
            {"role": "assistant", "content": "Alex → Orion lead dev."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=2)

        # Check facts were extracted
        fact_contents = [item.content for item in semantic_mem.items]
        combined = " ".join(fact_contents)
        assert "Orion" in combined or "Python" in combined or "SQLite" in combined

    def test_entities_extracted(self):
        """Entities should be extracted from conversation."""
        exchanges = [
            {"role": "user", "content": "Alex is working on Project Orion using PostgreSQL."},
            {"role": "assistant", "content": "Noted: Alex → Orion, PostgreSQL database."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check entities were extracted
        assert len(semantic_mem.entities) > 0
        entity_names = list(semantic_mem.entities.keys())
        assert any("alex" in e.lower() for e in entity_names) or any("orion" in e.lower() for e in entity_names)

    def test_relations_extracted(self):
        """Relations should be extracted from conversation."""
        exchanges = [
            {"role": "user", "content": "Orion uses PostgreSQL for storage."},
            {"role": "assistant", "content": "Noted: Orion → PostgreSQL."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check relations were extracted
        assert len(semantic_mem.relations) > 0
        rel_subjects = [r.subject for r in semantic_mem.relations]
        assert any("orion" in s.lower() for s in rel_subjects)

    def test_recent_messages_preserved(self):
        """Recent messages should be preserved verbatim."""
        exchanges = [
            {"role": "user", "content": f"Message {i}"} for i in range(10)
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=5)

        # Last 5 messages should be preserved
        assert len(recent_ctx.messages) == 5
        assert recent_ctx.messages[-1]["content"] == "Message 9"


class TestConsolidation:
    """Test consolidation logic."""

    def test_similar_items_merged(self):
        """Similar items should be merged."""
        # Use very similar items to ensure merging
        items = [
            MemoryItem(id="1", content="Project Orion uses Python language", kind="fact"),
            MemoryItem(id="2", content="Orion project uses the Python language", kind="fact"),
            MemoryItem(id="3", content="Different topic entirely about Rust", kind="fact"),
        ]
        result = consolidate_similar(items, similarity_threshold=0.5)
        assert len(result) < len(items)

    def test_dissimilar_items_kept(self):
        """Dissimilar items should not be merged."""
        items = [
            MemoryItem(id="1", content="Project Orion uses Python"),
            MemoryItem(id="2", content="Project Nebula uses Rust"),
        ]
        result = consolidate_similar(items)
        assert len(result) == 2


class TestProvenance:
    """Test provenance tracking."""

    def test_source_ids_preserved(self):
        """Source IDs should be preserved through consolidation."""
        exchanges = [
            {"role": "user", "content": "Orion uses SQLite."},
            {"role": "assistant", "content": "Noted."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check that items have source IDs
        for item in semantic_mem.items:
            assert len(item.source_ids) > 0


class TestMemoryModels:
    """Test memory model serialization."""

    def test_memory_item_roundtrip(self):
        """MemoryItem should serialize/deserialize correctly."""
        item = MemoryItem(
            id="test-1",
            content="Test fact",
            kind="fact",
            source_ids=["msg_1"],
            confidence=0.9,
        )
        data = item.to_dict()
        restored = MemoryItem.from_dict(data)
        assert restored.id == item.id
        assert restored.content == item.content
        assert restored.kind == item.kind
        assert restored.source_ids == item.source_ids
        assert restored.confidence == 0.9

    def test_entity_roundtrip(self):
        """Entity should serialize/deserialize correctly."""
        entity = Entity(name="Orion", kind="project", source_ids=["msg_1"])
        data = entity.to_dict()
        restored = Entity.from_dict(data)
        assert restored.name == entity.name
        assert restored.kind == entity.kind

    def test_relation_roundtrip(self):
        """Relation should serialize/deserialize correctly."""
        rel = Relation(subject="Orion", predicate="uses", object="SQLite", source_id="msg_1")
        data = rel.to_dict()
        restored = Relation.from_dict(data)
        assert restored.subject == rel.subject
        assert restored.predicate == rel.predicate
        assert restored.object == rel.object

    def test_decision_roundtrip(self):
        """Decision should serialize/deserialize correctly."""
        dec = Decision(value="Use SQLite", reason="lightweight", alternatives=["PostgreSQL"])
        data = dec.to_dict()
        restored = Decision.from_dict(data)
        assert restored.value == dec.value
        assert restored.reason == dec.reason
        assert restored.alternatives == dec.alternatives

    def test_contradiction_roundtrip(self):
        """Contradiction should serialize/deserialize correctly."""
        contra = Contradiction(
            id="contra-1",
            claim_a="Terminal UI",
            claim_b="Web dashboard",
            resolved=True,
            resolution="Web dashboard accepted",
        )
        data = contra.to_dict()
        restored = Contradiction.from_dict(data)
        assert restored.id == contra.id
        assert restored.claim_a == contra.claim_a
        assert restored.claim_b == contra.claim_b
        assert restored.resolved == contra.resolved


class TestSemanticMemory:
    """Test SemanticMemory operations."""

    def test_add_item(self):
        """Items should be added to semantic memory."""
        sm = SemanticMemory()
        item = MemoryItem(id="1", content="Test")
        sm.add_item(item)
        assert len(sm.items) == 1

    def test_upsert_entity(self):
        """Entities should be upserted by name."""
        sm = SemanticMemory()
        sm.upsert_entity(Entity(name="Orion", kind="project"))
        sm.upsert_entity(Entity(name="Orion", kind="project"))  # duplicate
        assert len(sm.entities) == 1

    def test_add_relation(self):
        """Relations should be added."""
        sm = SemanticMemory()
        sm.add_relation(Relation(subject="A", predicate="uses", object="B"))
        assert len(sm.relations) == 1

    def test_add_decision(self):
        """Decisions should be added."""
        sm = SemanticMemory()
        sm.add_decision(Decision(value="Use X"))
        assert len(sm.decisions) == 1

    def test_add_contradiction(self):
        """Contradictions should be added."""
        sm = SemanticMemory()
        sm.add_contradiction(Contradiction(id="c1", claim_a="A", claim_b="B"))
        assert len(sm.contradictions) == 1

    def test_to_dict_from_dict(self):
        """SemanticMemory should serialize/deserialize correctly."""
        sm = SemanticMemory()
        sm.add_item(MemoryItem(id="1", content="Test fact"))
        sm.upsert_entity(Entity(name="Orion", kind="project"))
        sm.add_relation(Relation(subject="Orion", predicate="uses", object="SQLite"))
        sm.add_decision(Decision(value="Use SQLite"))
        sm.add_contradiction(Contradiction(id="c1", claim_a="A", claim_b="B"))

        data = sm.to_dict()
        restored = SemanticMemory.from_dict(data)

        assert len(restored.items) == 1
        assert len(restored.entities) == 1
        assert len(restored.relations) == 1
        assert len(restored.decisions) == 1
        assert len(restored.contradictions) == 1


class TestRecentContext:
    """Test RecentContext operations."""

    def test_add_message(self):
        """Messages should be added."""
        rc = RecentContext()
        rc.add({"role": "user", "content": "Hello"})
        assert len(rc.messages) == 1

    def test_max_messages(self):
        """Should respect max_messages limit."""
        rc = RecentContext(max_messages=5)
        for i in range(10):
            rc.add({"role": "user", "content": f"Message {i}"})
        assert len(rc.messages) == 5
        assert rc.messages[0]["content"] == "Message 5"

    def test_roundtrip(self):
        """RecentContext should serialize/deserialize correctly."""
        rc = RecentContext()
        rc.add({"role": "user", "content": "Hello"})
        data = rc.to_dict()
        restored = RecentContext.from_dict(data)
        assert len(restored.messages) == 1
        assert restored.messages[0]["content"] == "Hello"
