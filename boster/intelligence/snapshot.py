"""Context Snapshot — versioned, reversible state snapshots.

Snapshots capture the state of context at a point in time.
They enable:
  - Before/after comparison
  - Rollback to previous state
  - Audit trail of changes
  - Recovery from bad compression decisions
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Snapshot:
    """A versioned snapshot of context state."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    version: int = 1
    created_at: float = field(default_factory=time.time)
    label: str = ""  # Human-readable label for the snapshot
    summary: str = ""  # High-level summary of what this snapshot captures
    facts: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    pending_tasks: List[str] = field(default_factory=list)
    token_count: int = 0
    char_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "label": self.label,
            "summary": self.summary,
            "facts": self.facts,
            "decisions": self.decisions,
            "constraints": self.constraints,
            "pending_tasks": self.pending_tasks,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Snapshot":
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            version=data.get("version", 1),
            created_at=data.get("created_at", time.time()),
            label=data.get("label", ""),
            summary=data.get("summary", ""),
            facts=data.get("facts", []),
            decisions=data.get("decisions", []),
            constraints=data.get("constraints", []),
            pending_tasks=data.get("pending_tasks", []),
            token_count=data.get("token_count", 0),
            char_count=data.get("char_count", 0),
            metadata=data.get("metadata", {}),
        )

    def diff_against(self, other: "Snapshot") -> Dict[str, Any]:
        """Compute difference between this snapshot and another."""
        return {
            "facts_added": len(set(f["id"] for f in other.facts) - set(f["id"] for f in self.facts)),
            "facts_removed": len(set(f["id"] for f in self.facts) - set(f["id"] for f in other.facts)),
            "decisions_added": len(other.decisions) - len(self.decisions),
            "constraints_changed": len(set(self.constraints) ^ set(other.constraints)),
            "token_delta": other.token_count - self.token_count,
        }


class SnapshotManager:
    """Manages context snapshots with versioning and rollback support.

    Snapshots are NEVER overwritten — they are immutable records of state.
    New snapshots get incrementing versions. Rollback restores from a
    previous snapshot but creates a new snapshot for the restored state.
    """

    def __init__(self, max_snapshots: int = 20):
        self._snapshots: Dict[str, Snapshot] = {}
        self._history: List[str] = []  # Ordered list of snapshot IDs
        self.max_snapshots = max_snapshots

    def create(
        self,
        *,
        label: str = "",
        summary: str = "",
        facts: Optional[List[Dict[str, Any]]] = None,
        decisions: Optional[List[Dict[str, Any]]] = None,
        constraints: Optional[List[str]] = None,
        pending_tasks: Optional[List[str]] = None,
        token_count: int = 0,
        char_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Snapshot:
        """Create a new snapshot of current context state."""
        version = len(self._history) + 1

        snapshot = Snapshot(
            version=version,
            label=label or f"Snapshot v{version}",
            summary=summary,
            facts=facts or [],
            decisions=decisions or [],
            constraints=constraints or [],
            pending_tasks=pending_tasks or [],
            token_count=token_count,
            char_count=char_count,
            metadata=metadata or {},
        )

        self._snapshots[snapshot.id] = snapshot
        self._history.append(snapshot.id)

        # Enforce max snapshots (keep most recent)
        if len(self._history) > self.max_snapshots:
            old_id = self._history.pop(0)
            self._snapshots.pop(old_id, None)

        return snapshot

    def get(self, snapshot_id: str) -> Optional[Snapshot]:
        """Retrieve a snapshot by ID."""
        return self._snapshots.get(snapshot_id)

    def get_latest(self) -> Optional[Snapshot]:
        """Get the most recent snapshot."""
        if not self._history:
            return None
        return self._snapshots.get(self._history[-1])

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all snapshots with metadata."""
        result = []
        for sid in self._history:
            snap = self._snapshots.get(sid)
            if snap:
                result.append({
                    "id": snap.id,
                    "version": snap.version,
                    "label": snap.label,
                    "created_at": snap.created_at,
                    "token_count": snap.token_count,
                    "fact_count": len(snap.facts),
                })
        return result

    def rollback_to(self, snapshot_id: str) -> Optional[Snapshot]:
        """Roll back to a previous snapshot.

        Creates a new snapshot representing the restored state.
        Does NOT delete the target snapshot.
        """
        target = self._snapshots.get(snapshot_id)
        if target is None:
            return None

        # Create a new snapshot reflecting the rolled-back state
        restored = Snapshot(
            version=len(self._history) + 1,
            label=f"Restored from v{target.version}: {target.label}",
            summary=f"Rolled back to snapshot v{target.version}",
            facts=list(target.facts),
            decisions=list(target.decisions),
            constraints=list(target.constraints),
            pending_tasks=list(target.pending_tasks),
            token_count=target.token_count,
            char_count=target.char_count,
            metadata={**target.metadata, "restored_from": snapshot_id},
        )

        self._snapshots[restored.id] = restored
        self._history.append(restored.id)

        return restored

    def diff(self, snapshot_id_a: str, snapshot_id_b: str) -> Optional[Dict[str, Any]]:
        """Compare two snapshots."""
        snap_a = self._snapshots.get(snapshot_id_a)
        snap_b = self._snapshots.get(snapshot_id_b)
        if snap_a is None or snap_b is None:
            return None
        return snap_a.diff_against(snap_b)

    def clear(self) -> int:
        """Remove all snapshots. Returns count removed."""
        count = len(self._history)
        self._snapshots.clear()
        self._history.clear()
        return count

    @property
    def count(self) -> int:
        return len(self._history)


__all__ = [
    "Snapshot",
    "SnapshotManager",
]
