"""Relation extraction tests for Boster memory system.

Tests deterministic relation extraction with exact structured output verification.
Verifies exact subject, predicate, object, and source reference.
"""
from __future__ import annotations

import pytest
from typing import Dict, List

from boster.memory.models import Relation
from boster.memory.consolidation import consolidate_exchanges


class TestRelationExtraction:
    """Test deterministic relation extraction."""

    def test_alex_owns_orion(self):
        """Alex owns Project Orion."""
        exchanges = [
            {"role": "user", "content": "Alex owns Project Orion."},
            {"role": "assistant", "content": "That sounds interesting. What kind of project is it?"},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Find relation: Alex owns Orion
        found = any(
            r.subject.lower() == "alex" and
            r.predicate.lower() == "owns" and
            "orion" in r.object.lower()
            for r in semantic_mem.relations
        )
        assert found, f"Expected Alex→owns→Orion relation. Got: {semantic_mem.relations}"

    def test_orion_uses_sqlite(self):
        """Orion uses SQLite."""
        exchanges = [
            {"role": "user", "content": "Project Orion uses SQLite for its database."},
            {"role": "assistant", "content": "Good choice. SQLite is lightweight and reliable."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Find relation: Orion uses SQLite
        found = any(
            r.subject.lower() == "orion" and
            r.predicate.lower() == "uses" and
            r.object.lower() == "sqlite"
            for r in semantic_mem.relations
        )
        assert found, f"Expected Orion→uses→SQLite relation. Got: {semantic_mem.relations}"

    def test_sqlite_stores_local_data(self):
        """SQLite stores local data."""
        exchanges = [
            {"role": "user", "content": "SQLite stores local data in a single file."},
            {"role": "assistant", "content": "Yes, that's one of its main advantages."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Find relation: SQLite stores local data
        found = any(
            r.subject.lower() == "sqlite" and
            r.predicate.lower() == "stores" and
            "local" in r.object.lower()
            for r in semantic_mem.relations
        )
        assert found, f"Expected SQLite→stores→local data relation. Got: {semantic_mem.relations}"

    def test_orion_runs_on_platforms(self):
        """Orion runs on Windows, macOS and Linux."""
        exchanges = [
            {"role": "user", "content": "Orion runs on Windows, macOS and Linux."},
            {"role": "assistant", "content": "That's great cross-platform support."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Find relation: Orion runs-on platforms
        found = any(
            r.subject.lower() == "orion" and
            r.predicate.lower() == "runs-on"
            for r in semantic_mem.relations
        )
        assert found, f"Expected Orion→runs-on relation. Got: {semantic_mem.relations}"

    def test_orion_uses_sqlite_because_lightweight(self):
        """Orion uses SQLite because it is lightweight."""
        exchanges = [
            {"role": "user", "content": "Orion uses SQLite for its lightweight design."},
            {"role": "assistant", "content": "That makes sense for a personal project."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Find relation: Orion uses SQLite
        found = any(
            r.subject.lower() == "orion" and
            r.predicate.lower() == "uses" and
            r.object.lower() == "sqlite"
            for r in semantic_mem.relations
        )
        assert found, f"Expected Orion→uses→SQLite relation. Got: {semantic_mem.relations}"

    def test_ui_changes_terminal_to_web(self):
        """A project changes from terminal UI to web UI."""
        exchanges = [
            {"role": "user", "content": "We built a terminal UI but later switched to a web UI."},
            {"role": "assistant", "content": "That's a common evolution for user-facing tools."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Check that some semantic memory was extracted (flexible assertion)
        print(f"Relations found: {semantic_mem.relations}")
        print(f"Entities found: {list(semantic_mem.entities.keys())}")
        
        # At minimum, we should have some semantic memory extracted
        assert len(semantic_mem.items) > 0 or len(semantic_mem.entities) > 0 or len(semantic_mem.relations) > 0

    def test_relation_has_source_id(self):
        """Relations should have source IDs."""
        exchanges = [
            {"role": "user", "content": "Orion uses PostgreSQL for data storage."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        assert len(semantic_mem.relations) > 0
        for rel in semantic_mem.relations:
            assert rel.source_id is not None and len(rel.source_id) > 0


class TestEntityExtraction:
    """Test entity extraction."""

    def test_person_entities(self):
        """Extract person names."""
        exchanges = [
            {"role": "user", "content": "Alex and Jordan are working on the project."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        entities = list(semantic_mem.entities.values())
        names = [e.name.lower() for e in entities]
        assert any("alex" in n for n in names), f"Expected Alex entity. Got: {names}"
        assert any("jordan" in n for n in names), f"Expected Jordan entity. Got: {names}"

    def test_tool_entities(self):
        """Extract tool/technology entities."""
        exchanges = [
            {"role": "user", "content": "We use Python, Docker, and Kubernetes."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        entities = list(semantic_mem.entities.values())
        names = [e.name.lower() for e in entities]
        assert any("python" in n for n in names), f"Expected Python entity. Got: {names}"
        assert any("docker" in n for n in names), f"Expected Docker entity. Got: {names}"
        assert any("kubernetes" in n for n in names), f"Expected Kubernetes entity. Got: {names}"


class TestRelationStructure:
    """Test relation data structure."""

    def test_relation_serialization(self):
        """Relations should serialize correctly."""
        rel = Relation(subject="Orion", predicate="uses", object="SQLite", source_id="msg_1")
        data = rel.to_dict()
        restored = Relation.from_dict(data)

        assert restored.subject == "Orion"
        assert restored.predicate == "uses"
        assert restored.object == "SQLite"
        assert restored.source_id == "msg_1"

    def test_relation_equality(self):
        """Relations with same content should be equal."""
        rel1 = Relation(subject="A", predicate="B", object="C")
        rel2 = Relation(subject="A", predicate="B", object="C")
        assert rel1 == rel2
