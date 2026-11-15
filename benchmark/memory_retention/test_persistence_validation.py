"""Persistence validation tests for Boster memory system.

Tests a complete save/reload cycle containing:
- recent messages
- episodic records
- semantic facts
- entities
- relations
- decisions
- unresolved contradictions
- resolved contradictions

Also tests legacy JSON migration and recovery from malformed files.
"""
from __future__ import annotations

import json
import pytest
import tempfile
import os
from pathlib import Path
from typing import List

from boster.memory.models import (
    MemoryItem, Entity, Relation, Decision, Contradiction,
    RecentContext, EpisodicMemory, SemanticMemory,
)
from boster.memory.persistence import MemoryPersistence


class TestPersistenceSaveReload:
    """Test complete save/reload cycle."""

    def test_full_cycle(self):
        """Test save and reload of all memory types."""
        # Create semantic memory with all types
        sm = SemanticMemory()
        sm.add_item(MemoryItem(id="fact1", content="Orion uses Python", kind="fact"))
        sm.upsert_entity(Entity(name="Orion", kind="project"))
        sm.add_relation(Relation(subject="Orion", predicate="uses", object="Python", source_id="msg_1"))
        sm.add_decision(Decision(value="Use SQLite", reason="lightweight"))
        sm.add_contradiction(Contradiction(id="c1", claim_a="REST", claim_b="GraphQL", resolved=False))
        sm.add_contradiction(Contradiction(id="c2", claim_a="Redis", claim_b="Memcached", resolved=True, resolution="Redis chosen"))

        # Create recent context
        rc = RecentContext()
        rc.add({"role": "user", "content": "Hello"})
        rc.add({"role": "assistant", "content": "Hi"})

        # Create episodic memory
        em = EpisodicMemory()
        em.add_episode({"type": "decision", "content": "Chose SQLite"})

        # Serialize
        data = {
            "semantic": sm.to_dict(),
            "recent": rc.to_dict(),
            "episodic": em.to_dict(),
        }

        # Deserialize
        restored_sm = SemanticMemory.from_dict(data["semantic"])
        restored_rc = RecentContext.from_dict(data["recent"])
        restored_em = EpisodicMemory.from_dict(data["episodic"])

        # Verify
        assert len(restored_sm.items) == 1
        assert len(restored_sm.entities) == 1
        assert len(restored_sm.relations) == 1
        assert len(restored_sm.decisions) == 1
        assert len(restored_sm.contradictions) == 2

        assert len(restored_rc.messages) == 2
        assert len(restored_em.episodes) == 1

    def test_unresolved_contradiction_preserved(self):
        """Unresolved contradictions should be preserved."""
        contra = Contradiction(id="c1", claim_a="A", claim_b="B", resolved=False)
        data = contra.to_dict()
        restored = Contradiction.from_dict(data)
        assert restored.resolved == False

    def test_resolved_contradiction_preserved(self):
        """Resolved contradictions should preserve resolution."""
        contra = Contradiction(id="c2", claim_a="A", claim_b="B", resolved=True, resolution="A chosen")
        data = contra.to_dict()
        restored = Contradiction.from_dict(data)
        assert restored.resolved == True
        assert restored.resolution == "A chosen"

    def test_legacy_json_migration(self):
        """Test migration from legacy MemoryEntry format."""
        # Simulate old format
        legacy_data = {
            "entries": [
                {"id": "old1", "content": "Old fact", "category": "fact", "importance": 0.8, "tags": ["msg_1"]},
            ],
            "saved_at": 1234567890,
        }

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(legacy_data, f)
            temp_path = f.name

        try:
            # Load using MemoryPersistence
            persistence = MemoryPersistence(Path(temp_path))
            count = persistence.load()
            assert count == 1
            entry = persistence.get_entry("old1")
            assert entry is not None
            assert entry.content == "Old fact"
            assert entry.kind == "fact"
        finally:
            os.unlink(temp_path)

    def test_malformed_json_recovery(self):
        """Test recovery from malformed JSON files."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{invalid json}")
            temp_path = f.name

        try:
            persistence = MemoryPersistence(Path(temp_path))
            count = persistence.load()
            assert count == 0, "Should return 0 entries for malformed JSON"
        finally:
            os.unlink(temp_path)

    def test_missing_file_recovery(self):
        """Test recovery from missing files."""
        persistence = MemoryPersistence(Path("/nonexistent/path/memory.json"))
        count = persistence.load()
        assert count == 0

    def test_atomic_write(self):
        """Test atomic write behavior."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.json"
            persistence = MemoryPersistence(path)

            # Add an entry
            entry = MemoryItem(id="test1", content="Test")
            persistence.add_entry(entry)

            # Save
            result = persistence.save()
            assert result == True

            # Verify file exists
            assert path.exists()

            # Load and verify
            count = persistence.load()
            assert count == 1


class TestPersistenceEdgeCases:
    """Test edge cases in persistence."""

    def test_empty_memory(self):
        """Test serialization of empty memory."""
        sm = SemanticMemory()
        data = sm.to_dict()
        restored = SemanticMemory.from_dict(data)
        assert len(restored.items) == 0
        assert len(restored.entities) == 0
        assert len(restored.relations) == 0
        assert len(restored.decisions) == 0
        assert len(restored.contradictions) == 0

    def test_large_memory(self):
        """Test serialization of large memory."""
        sm = SemanticMemory()
        for i in range(100):
            sm.add_item(MemoryItem(id=f"item_{i}", content=f"Content {i}"))
            sm.upsert_entity(Entity(name=f"Entity_{i}", kind="test"))

        data = sm.to_dict()
        restored = SemanticMemory.from_dict(data)
        assert len(restored.items) == 100
        assert len(restored.entities) == 100


class TestBackwardCompatibility:
    """Test backward compatibility with old API."""

    def test_memory_entry_alias(self):
        """MemoryEntry should be an alias for MemoryItem."""
        from boster.memory.models import MemoryEntry
        entry = MemoryEntry(id="test", content="Hello")
        assert isinstance(entry, MemoryItem)

    def test_old_format_load(self):
        """Test loading old format JSON."""
        old_format = {
            "entries": [
                {"id": "1", "content": "Fact", "category": "fact", "importance": 0.7},
            ]
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(old_format, f)
            temp_path = f.name

        try:
            persistence = MemoryPersistence(Path(temp_path))
            count = persistence.load()
            assert count == 1
        finally:
            os.unlink(temp_path)
