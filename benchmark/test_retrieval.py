"""Non-regression tests for lexical/hybrid retrieval threshold handling.

Regression guarded: ``lexical_search`` used to filter with a leftover ``score``
from the scoring loop (``retrieval.py:40``), so the returned set depended on
insertion order. Each entry must be compared against its OWN score.

Scores for query ``python`` (single word, time_bonus == 0):
    importance 0.0 -> 0.20   ("python low")
    importance 0.5 -> 0.30   ("python mid")
    importance 1.0 -> 0.40   ("python high")
"""
from __future__ import annotations

import pytest

from boster.memory.manager import MemoryManager
from boster.memory.retrieval import bm25_style_search, hybrid_search, lexical_search

LOW = ("python low", 0.0)    # score 0.20
MID = ("python mid", 0.5)    # score 0.30
HIGH = ("python high", 1.0)  # score 0.40


def _mgr(items):
    m = MemoryManager()
    for content, importance in items:
        m.add(content, category="fact", importance=importance)
    return m


def _names(result):
    return [e.content for e in result.entries]


class TestLexicalThreshold:
    """1-2: entries above/below the configured threshold."""

    def test_entry_above_threshold_returned(self):
        assert _names(lexical_search("python", _mgr([HIGH]), threshold=0.25)) == ["python high"]

    def test_entry_below_threshold_excluded(self):
        assert lexical_search("python", _mgr([LOW]), threshold=0.25).entries == []

    def test_boundary_is_inclusive(self):
        # threshold == score -> kept (>=)
        assert _names(lexical_search("python", _mgr([MID]), threshold=0.30)) == ["python mid"]


class TestLexicalOrdering:
    """3-4: multiple scores, order of relevance, insertion-order independence."""

    def test_multiple_entries_ordered_by_relevance(self):
        m = _mgr([LOW, MID, HIGH])
        assert _names(lexical_search("python", m, threshold=0.1)) == [
            "python high", "python mid", "python low"]

    def test_result_independent_of_insertion_order(self):
        forward = _mgr([LOW, MID, HIGH])
        reverse = _mgr([HIGH, MID, LOW])
        a = _names(lexical_search("python", forward, threshold=0.25))
        b = _names(lexical_search("python", reverse, threshold=0.25))
        assert a == b == ["python high", "python mid"]

    def test_bug_scenario_would_differ(self):
        # This is the exact regression: before the fix the two orders disagreed.
        m1 = _mgr([HIGH, LOW])   # high inserted first, low last
        m2 = _mgr([LOW, HIGH])   # low inserted first, high last
        assert _names(lexical_search("python", m1, threshold=0.25)) == ["python high"]
        assert _names(lexical_search("python", m2, threshold=0.25)) == ["python high"]


class TestLexicalThresholds:
    """5: several threshold values."""

    def test_multiple_threshold_values(self):
        m = _mgr([LOW, MID, HIGH])
        assert _names(lexical_search("python", m, threshold=0.1)) == [
            "python high", "python mid", "python low"]
        assert _names(lexical_search("python", m, threshold=0.25)) == ["python high", "python mid"]
        assert _names(lexical_search("python", m, threshold=0.35)) == ["python high"]
        assert _names(lexical_search("python", m, threshold=0.45)) == []


class TestLexicalLimits:
    """6-8: max_results, no admissible result, empty list."""

    def test_max_results_smaller_than_admissible(self):
        m = _mgr([LOW, MID, HIGH])
        r = lexical_search("python", m, max_results=2, threshold=0.1)
        assert _names(r) == ["python high", "python mid"]
        assert r.total_candidates == 3  # candidates before the limit

    def test_no_admissible_result(self):
        m = _mgr([LOW, MID, HIGH])
        assert lexical_search("python", m, threshold=0.99).entries == []

    def test_empty_manager(self):
        r = lexical_search("python", MemoryManager(), threshold=0.1)
        assert r.entries == []
        assert r.total_candidates == 0

    def test_empty_query(self):
        assert lexical_search("", _mgr([HIGH]), threshold=0.1).entries == []


class TestBm25Unaffected:
    """bm25_style_search never used the leftover ``score`` — must stay correct."""

    def test_bm25_returns_all_scoring_entries(self):
        m = _mgr([LOW, MID, HIGH])
        assert {e.content for e in bm25_style_search("python", m).entries} == {
            "python low", "python mid", "python high"}


class TestHybridOrderIndependence:
    """hybrid_search consumes the corrected lexical_search."""

    def test_hybrid_independent_of_insertion_order(self):
        forward = _mgr([LOW, MID, HIGH])
        reverse = _mgr([HIGH, MID, LOW])
        a = {e.content for e in hybrid_search("python", forward).entries}
        b = {e.content for e in hybrid_search("python", reverse).entries}
        assert a == b == {"python high", "python mid", "python low"}

    def test_hybrid_returns_relevance_ordered_entries(self):
        m = _mgr([LOW, MID, HIGH])
        assert _names(hybrid_search("python", m)) == ["python high", "python mid", "python low"]
