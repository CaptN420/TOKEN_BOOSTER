"""Context Intelligence Engine — smart compression with KV cache awareness.

This module implements the v2.0 architecture upgrade:
  - ContextMonitor: threshold-based surveillance
  - ContextCompressor: on-demand compression only
  - ContextIntegrityEngine: preserve facts, decisions, relations
  - ContextSnapshot: versioned, reversible state snapshots
  - CacheAwareness: KV cache compatibility layer

Design principle: NEVER compress unless it brings real benefit.
"""
from __future__ import annotations

from .monitor import ContextMonitor, CompressionMode, ThresholdConfig
from .compressor import ContextCompressor, CompressionResult
from .integrity import ContextIntegrityEngine, FactRelation
from .snapshot import Snapshot as ContextSnapshot, SnapshotManager
from .cache_awareness import CacheAwareness, CacheImpactReport

__all__ = [
    "ContextMonitor",
    "CompressionMode",
    "ThresholdConfig",
    "ContextCompressor",
    "CompressionResult",
    "ContextIntegrityEngine",
    "FactRelation",
    "ContextSnapshot",
    "SnapshotManager",
    "CacheAwareness",
    "CacheImpactReport",
]
