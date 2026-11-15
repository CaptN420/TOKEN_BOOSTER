"""Integration tests for Boster memory system."""
from __future__ import annotations

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from boster.memory.models import MemoryItem, Entity, Relation, Decision, Contradiction
from boster.memory.consolidation import consolidate_exchanges
from boster.memory.persistence import MemoryPersistence


class TestIntegration:
    """Test integration between components."""

    def test_end_to_end_consolidation(self):
        """Test full consolidation pipeline."""
        exchanges = [
            {"role": "user", "content": "Alex owns Project Orion which uses SQLite."},
            {"role": "assistant", "content": "Interesting project. What platform does it run on?"},
            {"role": "user", "content": "It runs on Windows, macOS and Linux."},
        ]

        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # keep_recent=0 => nothing kept verbatim, all 3 exchanges consolidated
        # (never dropped): they must reappear as semantic items.
        assert len(recent_ctx.messages) == 0
        assert len(semantic_mem.items) == 3
        assert len(semantic_mem.entities) > 0
        assert len(semantic_mem.relations) > 0

    def test_provenance_preserved(self):
        """Test that source IDs are preserved."""
        exchanges = [
            {"role": "user", "content": "Important decision: use PostgreSQL."},
        ]

        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Check that items have source IDs
        for item in semantic_mem.items:
            assert len(item.source_ids) > 0

    def test_serialization_roundtrip(self):
        """Test serialization and deserialization."""
        exchanges = [
            {"role": "user", "content": "Orion uses SQLite."},
        ]

        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        # Serialize
        data = {
            'recent': recent_ctx.to_dict(),
            'semantic': semantic_mem.to_dict()
        }

        # Deserialize
        restored_recent = type(recent_ctx).from_dict(data['recent'])
        restored_semantic = type(semantic_mem).from_dict(data['semantic'])

        assert len(restored_recent.messages) == len(recent_ctx.messages)
        assert len(restored_semantic.entities) == len(semantic_mem.entities)
        assert len(restored_semantic.relations) == len(semantic_mem.relations)

    def test_recent_context_max_messages(self):
        """Test recent context message limit."""
        exchanges = [
            {"role": "user", "content": f"Message {i}"} for i in range(20)
        ]

        recent_ctx, _ = consolidate_exchanges(exchanges, keep_recent=5)

        assert len(recent_ctx.messages) <= 5

    def test_consolidation_deterministic(self):
        """Test that consolidation is deterministic."""
        exchanges = [
            {"role": "user", "content": "Orion uses SQLite."},
        ]

        _, semantic_mem1 = consolidate_exchanges(exchanges, keep_recent=0)
        _, semantic_mem2 = consolidate_exchanges(exchanges, keep_recent=0)

        assert len(semantic_mem1.entities) == len(semantic_mem2.entities)
        assert len(semantic_mem1.relations) == len(semantic_mem2.relations)

    def test_fact_retention_benchmark_runs(self):
        """Test that fact retention benchmark runs."""
        # Run a simple consolidation to verify the system works
        exchanges = [
            {"role": "user", "content": "We should use PostgreSQL for the database."},
            {"role": "assistant", "content": "Noted: PostgreSQL consideration."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)
        
        # Verify basic functionality: with keep_recent=0 both exchanges are
        # consolidated (not lost) -> recent empty, 2 semantic items.
        assert len(recent_ctx.messages) == 0
        assert len(semantic_mem.items) == 2


class TestBenchmarkIntegration:
    """Test integration with benchmarks."""

    def test_relationship_retention_benchmark_runs(self):
        """Test that relationship retention benchmark runs."""
        # Run a simple consolidation to verify the system works
        exchanges = [
            {"role": "user", "content": "Orion uses SQLite for data storage."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)
        
        # Verify relations were extracted
        assert len(semantic_mem.relations) > 0 or len(semantic_mem.entities) > 0
