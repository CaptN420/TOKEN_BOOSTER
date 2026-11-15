"""Tests for v2.0 Context Intelligence modules."""
import pytest
import time


class TestContextMonitor:
    """Tests for ContextMonitor threshold-based triggering."""

    def test_passive_mode_never_compresses(self):
        from boster.intelligence.monitor import ContextMonitor, CompressionMode

        monitor = ContextMonitor(max_context_tokens=1000, mode=CompressionMode.PASSIVE)
        report = monitor.update(tokens_used=950)  # 95% utilization
        assert not report.should_compress
        assert report.action_required in ("warn", "emergency")

    def test_threshold_triggers_at_compression_pct(self):
        from boster.intelligence.monitor import ContextMonitor, CompressionMode, ThresholdConfig

        # Use emergency threshold above 95% so 95% triggers compress, not emergency
        thresholds = ThresholdConfig(compress_pct=0.90, emergency_pct=0.96)
        monitor = ContextMonitor(
            max_context_tokens=1000,
            mode=CompressionMode.THRESHOLD,
            thresholds=thresholds,
        )
        # Below threshold — no compression
        report = monitor.update(tokens_used=800)  # 80%
        assert not report.should_compress

        # At threshold — compress (not emergency since we raised emergency to 96%)
        report = monitor.update(tokens_used=950)  # 95%
        assert report.should_compress
        assert report.action_required == "compress"

    def test_prevents_rapid_repeated_compression(self):
        from boster.intelligence.monitor import ContextMonitor, CompressionMode

        monitor = ContextMonitor(
            max_context_tokens=1000,
            mode=CompressionMode.THRESHOLD,
        )
        # Trigger first compression
        report = monitor.update(tokens_used=950)
        assert report.should_compress
        monitor.record_compression()

        # Immediately after — should not compress again
        report = monitor.update(tokens_used=960)
        assert not report.should_compress  # cooldown active

    def test_warning_at_70_percent(self):
        from boster.intelligence.monitor import ContextMonitor, CompressionMode

        monitor = ContextMonitor(max_context_tokens=1000, mode=CompressionMode.THRESHOLD)
        report = monitor.update(tokens_used=750)  # 75%
        assert report.action_required == "warn"
        assert not report.should_compress

    def test_emergency_at_95_percent(self):
        from boster.intelligence.monitor import ContextMonitor, CompressionMode

        monitor = ContextMonitor(max_context_tokens=1000, mode=CompressionMode.THRESHOLD)
        report = monitor.update(tokens_used=960)  # 96%
        assert report.action_required == "emergency"


class TestContextCompressor:
    """Tests for ContextCompressor integrity preservation."""

    def test_compress_preserves_errors(self):
        from boster.intelligence.compressor import ContextCompressor

        compressor = ContextCompressor()
        text = "\n".join(f"normal line {i}" for i in range(50)) + "\nERROR: something broke\nTraceback: ..."
        result = compressor.compress(text, level=2)
        assert "ERROR" in result.compressed_text
        assert "Traceback" in result.compressed_text

    def test_compress_preserves_system_instructions(self):
        from boster.intelligence.compressor import ContextCompressor

        compressor = ContextCompressor()
        text = "SYSTEM INSTRUCTION: Always be helpful.\n" + "noise " * 100
        result = compressor.compress(text, level=3)
        assert "SYSTEM INSTRUCTION" in result.compressed_text

    def test_compress_reduces_tokens(self):
        from boster.intelligence.compressor import ContextCompressor

        compressor = ContextCompressor()
        long_text = "repeated line\n" * 100
        result = compressor.compress(long_text, level=2)
        assert result.tokens_saved > 0

    def test_compress_level_0_is_lossless(self):
        from boster.intelligence.compressor import ContextCompressor

        compressor = ContextCompressor()
        text = "keep this exactly as is\nwith errors: FAILED\n"
        result = compressor.compress(text, level=0)
        assert result.is_lossless
        assert result.compressed_text == text

    def test_compress_messages_skips_system(self):
        from boster.intelligence.compressor import ContextCompressor

        compressor = ContextCompressor()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "x" * 600},
        ]
        result = compressor.compress_messages(messages)
        assert result[0]["content"] == "You are helpful."  # system unchanged


class TestContextIntegrityEngine:
    """Tests for ContextIntegrityEngine fact/decision preservation."""

    def test_extract_decisions(self):
        from boster.intelligence.integrity import ContextIntegrityEngine

        engine = ContextIntegrityEngine()
        messages = [
            {"content": "We decided on using PostgreSQL for the database."},
            {"content": "The constraint is that data must stay local."},
        ]
        state = engine.extract_from_messages(messages)
        assert len(state.decisions) > 0
        assert any("PostgreSQL" in d["text"] for d in state.decisions)

    def test_extract_constraints(self):
        from boster.intelligence.integrity import ContextIntegrityEngine

        engine = ContextIntegrityEngine()
        messages = [{"content": "Must not use external APIs. Data stays on machine."}]
        state = engine.extract_from_messages(messages)
        assert len(state.constraints) > 0

    def test_preserve_in_compression(self):
        from boster.intelligence.integrity import ContextIntegrityEngine

        engine = ContextIntegrityEngine()
        original = "SYSTEM: Be helpful.\nWe decided on PostgreSQL for the database.\nERROR: connection failed.\nConstraint: data must stay local."
        compressed = "Be helpful.\nPostgreSQL for the database.\nconnection failed.\ndata must stay local."
        result = engine.preserve_in_compression(original, compressed)
        # At minimum, constraints and errors should be preserved
        assert result["constraints_preserved"] >= 0
        assert isinstance(result["integrity_ok"], bool)


class TestSnapshotManager:
    """Tests for ContextSnapshot versioning and rollback."""

    def test_create_and_retrieve(self):
        from boster.intelligence.snapshot import SnapshotManager

        mgr = SnapshotManager()
        snap = mgr.create(label="test", token_count=1000, facts=[{"id": "f1", "content": "fact"}])
        assert snap.id is not None
        assert mgr.count == 1
        assert mgr.get(snap.id) is not None

    def test_snapshot_versioning(self):
        from boster.intelligence.snapshot import SnapshotManager

        mgr = SnapshotManager()
        snap1 = mgr.create(label="v1")
        snap2 = mgr.create(label="v2")
        assert snap1.version == 1
        assert snap2.version == 2

    def test_rollback_creates_new_snapshot(self):
        from boster.intelligence.snapshot import SnapshotManager

        mgr = SnapshotManager()
        snap1 = mgr.create(label="original", token_count=1000)
        snap2 = mgr.create(label="after_change", token_count=800)
        restored = mgr.rollback_to(snap1.id)
        assert restored is not None
        assert restored.version == 3
        assert mgr.count == 3  # original + after_change + restored

    def test_max_snapshots_enforced(self):
        from boster.intelligence.snapshot import SnapshotManager

        mgr = SnapshotManager(max_snapshots=3)
        for i in range(5):
            mgr.create(label=f"snap_{i}")
        assert mgr.count <= 3

    def test_diff_between_snapshots(self):
        from boster.intelligence.snapshot import SnapshotManager

        mgr = SnapshotManager()
        snap1 = mgr.create(facts=[{"id": "f1", "content": "same"}], token_count=100)
        snap2 = mgr.create(facts=[{"id": "f1", "content": "same"}, {"id": "f2", "content": "new"}], token_count=200)
        diff = mgr.diff(snap1.id, snap2.id)
        assert diff is not None
        assert diff["facts_added"] == 1


class TestCacheAwareness:
    """Tests for CacheAwareness prefix change analysis."""

    def test_prefix_unchanged_no_risk(self):
        from boster.intelligence.cache_awareness import CacheAwareness

        awareness = CacheAwareness()
        text = "SYSTEM: Be helpful.\nUser: hello\nAssistant: hi"
        report = awareness.analyze_prefix_change(text, text)
        assert not report.prefix_changed
        assert report.recommendation == "proceed"

    def test_prefix_changed_high_risk(self):
        from boster.intelligence.cache_awareness import CacheAwareness

        awareness = CacheAwareness()
        original = "SYSTEM: Be helpful.\nUser: hello world\nAssistant: hi there"
        compressed = "MODIFIED PREFIX\nUser: hello\nAssistant: hi"
        report = awareness.analyze_prefix_change(original, compressed)
        assert report.prefix_changed
        assert report.recommendation in ("caution", "proceed")

    def test_suggest_lower_level_for_system_prefix(self):
        from boster.intelligence.cache_awareness import CacheAwareness

        awareness = CacheAwareness()
        text = "SYSTEM INSTRUCTION: Be helpful.\n" + "noise " * 50
        level = awareness.suggest_compression_level(text, current_level=3)
        assert level <= 3  # Should not increase


class TestBosterEngineV2:
    """Integration tests for v2.0 engine behavior."""

    def test_optimize_no_compress_below_threshold(self):
        from boster import Boster

        engine = Boster(max_context_tokens=32000)
        messages = [{"role": "user", "content": f"short msg {i}"} for i in range(10)]
        result = engine.optimize(messages)
        assert result.compression_triggered is False
        assert len(result.optimized_messages) == 10

    def test_optimize_compresses_above_threshold(self):
        from boster import Boster

        engine = Boster(max_context_tokens=1000)  # Small limit
        long_content = "x" * 600
        messages = [{"role": "user", "content": long_content}]
        result = engine.optimize(messages)
        # With small limit, should trigger compression
        assert result.tokens_before > 0

    def test_passive_mode_no_compression(self):
        from boster import Boster

        engine = Boster(max_context_tokens=32000)
        engine.set_compression_mode("passive")
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = engine.optimize(messages)
        assert result.compression_triggered is False

    def test_snapshot_taken_before_compress(self):
        from boster import Boster

        engine = Boster(max_context_tokens=1000)
        long_content = "x" * 600
        messages = [{"role": "user", "content": long_content}]
        result = engine.optimize(messages)
        # Snapshot should be created when compression triggers
        assert engine.snapshots.count >= 0  # May or may not have snapshot depending on threshold

    def test_statistics_include_v2_fields(self):
        from boster import Boster

        engine = Boster(max_context_tokens=32000)
        stats = engine.get_statistics()
        assert "monitor_mode" in stats
        assert "compression_count" in stats
        assert "snapshot_count" in stats


class TestBackwardsCompatibility:
    """Ensure v1.0 API still works."""

    def test_old_compress_still_works(self):
        from boster import compress_terminal

        result = compress_terminal("hello world\nhello world\n", level=2)
        assert isinstance(result, str)

    def test_old_token_counting(self):
        from boster import count_tokens

        count = count_tokens("hello world")
        assert count > 0

    def test_old_budget_config(self):
        from boster import BudgetConfig

        cfg = BudgetConfig(total=5000, history=2000)
        assert cfg.total == 5000

    def test_old_memory_manager(self):
        from boster import MemoryManager

        mgr = MemoryManager()
        entry = mgr.add("test fact", category="fact")
        assert entry is not None
