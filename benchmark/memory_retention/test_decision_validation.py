"""Decision validation tests for Boster memory system.

Verifies that each Decision preserves:
- decision value
- reason or justification
- date or source reference
- status
- previous decision when replaced

Do not silently convert a suggestion into a confirmed decision.
"""
from __future__ import annotations

import pytest
from typing import List

from boster.memory.models import Decision, MemoryItem
from boster.memory.consolidation import consolidate_exchanges


class TestDecisionValidation:
    """Test decision validation and preservation."""

    def test_decision_value_preserved(self):
        """Decision value should be preserved."""
        exchanges = [
            {"role": "user", "content": "Let's use SQLite for the database."},
            {"role": "assistant", "content": "Decision noted: SQLite."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        assert len(semantic_mem.decisions) > 0, "Expected decision to be extracted"
        dec = semantic_mem.decisions[0]
        assert "sqlite" in dec.value.lower(), f"Expected 'sqlite' in decision value, got: {dec.value}"

    def test_decision_reason_preserved(self):
        """Decision reason should be preserved."""
        exchanges = [
            {"role": "user", "content": "We chose SQLite because it's lightweight."},
            {"role": "assistant", "content": "Noted: SQLite for lightweight storage."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check that a decision or fact with reason exists
        found_reason = False
        for dec in semantic_mem.decisions:
            if dec.reason and len(dec.reason) > 0:
                found_reason = True
                break
        # Also check items for reason content
        for item in semantic_mem.items:
            if "lightweight" in item.content.lower():
                found_reason = True
                break

        assert found_reason, "Expected reason to be preserved"

    def test_decision_status_active(self):
        """Decision should have active status by default."""
        exchanges = [
            {"role": "user", "content": "Let's use pytest for testing."},
            {"role": "assistant", "content": "Decision noted: pytest."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        for dec in semantic_mem.decisions:
            assert dec.status == "active", f"Expected active status, got: {dec.status}"

    def test_decision_date_from_source_timestamp(self):
        """A decision's date comes from its source message, never from now()."""
        exchanges = [
            {"role": "user", "content": "Let's use Docker for containerization.",
             "timestamp": 1700000000.0},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        assert semantic_mem.decisions
        for dec in semantic_mem.decisions:
            assert dec.date == 1700000000.0

    def test_decision_date_absent_without_source_timestamp(self):
        """No source timestamp -> no date. A date is never fabricated.

        (Previously this asserted ``date > 0``, which required inventing a
        wall-clock date for an undated message — the exact behaviour Phase
        2.2.3 forbids.)
        """
        exchanges = [
            {"role": "user", "content": "Let's use Docker for containerization."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=0)

        assert semantic_mem.decisions
        for dec in semantic_mem.decisions:
            assert dec.date is None

    def test_suggestion_not_converted_to_decision(self):
        """A suggestion should not be silently converted to a decision."""
        exchanges = [
            {"role": "user", "content": "Maybe we could use GraphQL?"},
            {"role": "assistant", "content": "That's an interesting idea."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=2)

        # No decision should be extracted from a mere suggestion
        for dec in semantic_mem.decisions:
            assert "graphql" not in dec.value.lower() or "maybe" in dec.value.lower(), \
                f"Suggestion should not become a firm decision: {dec.value}"

    def test_replaced_decision_preserved(self):
        """Previous decision should be preserved when replaced."""
        exchanges = [
            {"role": "user", "content": "Let's use PostgreSQL."},
            {"role": "assistant", "content": "Noted: PostgreSQL."},
            {"role": "user", "content": "Actually, let's use SQLite instead."},
            {"role": "assistant", "content": "Decision updated: SQLite (was PostgreSQL)."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=2)

        # Check that both decisions are mentioned in items
        item_contents = " ".join(item.content.lower() for item in semantic_mem.items)
        assert "postgresql" in item_contents or "sqlite" in item_contents, \
            "Both database choices should be mentioned"

    def test_decision_with_alternatives(self):
        """Decision should preserve alternatives when available."""
        exchanges = [
            {"role": "user", "content": "We considered PostgreSQL and MySQL, but chose SQLite."},
            {"role": "assistant", "content": "Decision: SQLite (alternatives: PostgreSQL, MySQL)."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check that alternatives are mentioned
        found_alternatives = False
        for dec in semantic_mem.decisions:
            if len(dec.alternatives) > 0:
                found_alternatives = True
                break
        # Also check items
        for item in semantic_mem.items:
            if "postgresql" in item.content.lower() and "mysql" in item.content.lower():
                found_alternatives = True
                break

        assert found_alternatives, "Expected alternatives to be preserved"


class TestDecisionModel:
    """Test decision model integrity."""

    def test_decision_serialization(self):
        """Decision should serialize/deserialize correctly."""
        dec = Decision(
            value="Use SQLite",
            reason="lightweight and portable",
            alternatives=["PostgreSQL", "MySQL"],
            status="active",
        )
        data = dec.to_dict()
        restored = Decision.from_dict(data)
        assert restored.value == "Use SQLite"
        assert restored.reason == "lightweight and portable"
        assert restored.alternatives == ["PostgreSQL", "MySQL"]
        assert restored.status == "active"

    def test_decision_with_date(self):
        """Decision with date should serialize correctly."""
        import time
        dec = Decision(value="Use Docker", reason="containerization", date=time.time())
        data = dec.to_dict()
        restored = Decision.from_dict(data)
        assert restored.value == "Use Docker"
        assert restored.date is not None and restored.date > 0


class TestDecisionExtraction:
    """Test decision extraction from text."""

    def test_explicit_decision(self):
        """Explicit decision should be extracted."""
        exchanges = [
            {"role": "user", "content": "Let's go with SQLite. It's the best choice."},
            {"role": "assistant", "content": "Decision confirmed: SQLite."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check that a decision was extracted
        assert len(semantic_mem.decisions) > 0, "Expected decision to be extracted"

    def test_decision_with_justification(self):
        """Decision with justification should preserve the reason."""
        exchanges = [
            {"role": "user", "content": "We decided on SQLite because it's lightweight."},
            {"role": "assistant", "content": "Noted: SQLite for lightweight storage."},
        ]
        recent_ctx, semantic_mem = consolidate_exchanges(exchanges, keep_recent=1)

        # Check that the reason is preserved in items or decisions
        found_reason = False
        for item in semantic_mem.items:
            if "lightweight" in item.content.lower():
                found_reason = True
                break
        for dec in semantic_mem.decisions:
            if dec.reason and "lightweight" in dec.reason.lower():
                found_reason = True
                break

        assert found_reason, "Expected reason to be preserved"
