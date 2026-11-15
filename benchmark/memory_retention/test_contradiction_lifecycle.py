"""Contradiction lifecycle tests for Boster memory system.

Honest lifecycle coverage: creation, both claims, both sources, unresolved
state, explicit resolution, history preservation, and disk persistence.

The *automatic* detection of contradictions from free text is NOT implemented
in ``_detect_contradictions`` (it returns ``[]`` for incompatible claims such as
"use PostgreSQL" vs "use SQLite"). That missing feature is documented by
``TestAutoDetectionMissing`` marked ``xfail`` rather than faked with ``or True``.
"""
from __future__ import annotations

import pytest

from boster.memory.consolidation import consolidate_exchanges
from boster.memory.models import (
    Contradiction,
    EpisodicMemory,
    RecentContext,
    SemanticMemory,
)
from boster.memory.persistence import MemoryPersistence


class TestContradictionCreation:
    """A contradiction can be created and recorded with full content."""

    def test_creation_keeps_both_claims(self):
        c = Contradiction(id="c1", claim_a="Use PostgreSQL", claim_b="Use SQLite")
        assert c.claim_a == "Use PostgreSQL"
        assert c.claim_b == "Use SQLite"

    def test_creation_keeps_both_sources(self):
        c = Contradiction(id="c1", claim_a="A", claim_b="B",
                          source_a="msg_1", source_b="msg_2")
        assert c.source_a == "msg_1"
        assert c.source_b == "msg_2"

    def test_created_unresolved_by_default(self):
        c = Contradiction(id="c1", claim_a="A", claim_b="B")
        assert c.resolved is False
        assert c.resolution == ""

    def test_recorded_in_semantic_memory(self):
        sm = SemanticMemory()
        sm.add_contradiction(Contradiction(id="c1", claim_a="A", claim_b="B",
                                           source_a="m1", source_b="m2"))
        assert len(sm.contradictions) == 1
        stored = sm.contradictions[0]
        assert stored.claim_a == "A" and stored.claim_b == "B"
        assert stored.source_a == "m1" and stored.source_b == "m2"


class TestContradictionLifecycle:
    """Unresolved -> resolved, without destroying history."""

    def test_stays_unresolved_until_explicitly_resolved(self):
        sm = SemanticMemory()
        sm.add_contradiction(Contradiction(id="c1", claim_a="REST", claim_b="GraphQL"))
        assert sm.contradictions[0].resolved is False

        # Explicit resolution
        sm.contradictions[0].resolved = True
        sm.contradictions[0].resolution = "GraphQL chosen for flexibility"
        assert sm.contradictions[0].resolved is True
        assert sm.contradictions[0].resolution == "GraphQL chosen for flexibility"

    def test_original_claims_never_deleted_on_resolution(self):
        c = Contradiction(id="c1", claim_a="REST", claim_b="GraphQL")
        c.resolved = True
        c.resolution = "GraphQL chosen"
        # Both original claims survive resolution untouched
        assert c.claim_a == "REST"
        assert c.claim_b == "GraphQL"

    def test_later_reversal_preserves_history(self):
        sm = SemanticMemory()
        sm.add_contradiction(Contradiction(id="c1", claim_a="Use PostgreSQL",
                                           claim_b="Use SQLite"))
        # A later reversal is represented as a NEW contradiction, not by
        # deleting the first one.
        sm.add_contradiction(Contradiction(id="c2", claim_a="Use SQLite",
                                           claim_b="Use PostgreSQL"))
        assert len(sm.contradictions) == 2
        assert {c.id for c in sm.contradictions} == {"c1", "c2"}


class TestContradictionPersistence:
    """Resolution state and claims survive a real disk round-trip."""

    def test_states_survive_disk_roundtrip(self, tmp_path):
        sm = SemanticMemory()
        sm.add_contradiction(Contradiction(id="c1", claim_a="Use PostgreSQL",
                                           claim_b="Use SQLite",
                                           source_a="msg_1", source_b="msg_2",
                                           resolved=False))
        sm.add_contradiction(Contradiction(id="c2", claim_a="Redis", claim_b="Memcached",
                                           source_a="msg_3", source_b="msg_4",
                                           resolved=True, resolution="Redis chosen"))

        path = tmp_path / "mem.json"
        assert MemoryPersistence().save_state(RecentContext(), sm, EpisodicMemory(), path=path) is True
        _, _, sm2 = MemoryPersistence().load_state(path=path)

        by_id = {c.id: c for c in sm2.contradictions}
        assert by_id["c1"].claim_a == "Use PostgreSQL" and by_id["c1"].claim_b == "Use SQLite"
        assert by_id["c1"].source_a == "msg_1" and by_id["c1"].source_b == "msg_2"
        assert by_id["c1"].resolved is False
        assert by_id["c2"].resolved is True and by_id["c2"].resolution == "Redis chosen"


class TestContradictionModel:
    """Serialization of the Contradiction dataclass."""

    def test_contradiction_serialization(self):
        contra = Contradiction(id="c1", claim_a="A", claim_b="B")
        restored = Contradiction.from_dict(contra.to_dict())
        assert restored.id == "c1"
        assert restored.claim_a == "A"
        assert restored.claim_b == "B"
        assert restored.resolved is False

    def test_contradiction_with_resolution(self):
        contra = Contradiction(id="c2", claim_a="Redis", claim_b="Memcached",
                               resolved=True, resolution="Redis chosen")
        restored = Contradiction.from_dict(contra.to_dict())
        assert restored.resolved is True
        assert restored.resolution == "Redis chosen"


@pytest.mark.xfail(
    strict=True,
    reason="REQUIRED FEATURE MISSING: automatic contradiction detection from "
           "free text. _detect_contradictions() does not link incompatible "
           "claims (e.g. 'use PostgreSQL' vs 'use SQLite') and returns [].",
)
class TestAutoDetectionMissing:
    """Documents a feature that does not exist yet — not a fabricated pass."""

    def test_auto_detection_from_text(self):
        exchanges = [
            {"role": "user", "content": "We should use PostgreSQL for the database."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "Actually, let's use SQLite instead, it's lighter."},
        ]
        _, sem = consolidate_exchanges(exchanges, keep_recent=0)
        assert len(sem.contradictions) > 0
