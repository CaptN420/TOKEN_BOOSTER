"""Memory persistence: save/load memory to/from disk."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    EpisodicMemory,
    MemoryEntry,
    MemoryItem,
    RecentContext,
    SemanticMemory,
)

logger = logging.getLogger("boster.persistence")

# On-disk schema version. v1 = flat {"entries": [...]} (legacy MemoryEntry).
# v2 = full three-tier {"recent", "episodic", "semantic"}.
SCHEMA_VERSION = 2


class MemoryPersistence:
    """Save and load memory entries to/from JSON files."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".hermes" / "memory.json"
        self._in_memory: Dict[str, MemoryItem] = {}

    @property
    def count(self) -> int:
        return len(self._in_memory)

    def load(self) -> int:
        """Load memory from disk. Returns number of entries loaded."""
        if not self.storage_path.exists():
            return 0
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("entries", []):
                # Handle both old MemoryEntry format and new MemoryItem format
                if "kind" in item and "content" in item:
                    entry = MemoryItem.from_dict(item)
                elif "category" in item and "importance" in item:
                    # Old format: convert to new format
                    entry = MemoryItem(
                        id=item["id"],
                        content=item["content"],
                        kind=item.get("category", "fact"),
                        source_ids=item.get("tags", []),
                        confidence=item.get("importance", 0.5),
                    )
                else:
                    continue
                self._in_memory[entry.id] = entry
            logger.info("Loaded %d memory entries from %s", len(self._in_memory), self.storage_path)
            return len(self._in_memory)
        except Exception as e:
            logger.warning("Failed to load memory: %s", e)
            return 0

    def save(self) -> bool:
        """Save memory to disk. Returns True on success."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "entries": [e.to_dict() for e in self._in_memory.values()],
                "saved_at": time.time(),
                "count": len(self._in_memory),
            }
            # Atomic write: write to temp file first, then rename
            tmp_path = self.storage_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.storage_path)
            logger.info("Saved %d memory entries to %s", len(self._in_memory), self.storage_path)
            return True
        except Exception as e:
            logger.error("Failed to save memory: %s", e)
            # Clean up temp file if it exists
            tmp_path = self.storage_path.with_suffix(".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            return False

    # ── Full three-tier state (schema v2) ──────────────────────────────
    def save_state(
        self,
        recent: Optional[RecentContext] = None,
        semantic: Optional[SemanticMemory] = None,
        episodic: Optional[EpisodicMemory] = None,
        path: Optional[Path] = None,
        saved_at: Optional[float] = None,
    ) -> bool:
        """Atomically save the full three-tier memory state.

        Writes a schema-v2 JSON document preserving RecentContext,
        EpisodicMemory and every SemanticMemory level (items, entities,
        relations, decisions, contradictions) with their provenance refs.

        ``saved_at`` defaults to the wall clock (it is metadata about the
        write, not knowledge); pass an explicit value to make the file
        byte-identical across runs.
        """
        target = Path(path) if path else self.storage_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": SCHEMA_VERSION,
                "recent": (recent or RecentContext()).to_dict(),
                "episodic": (episodic or EpisodicMemory()).to_dict(),
                "semantic": (semantic or SemanticMemory()).to_dict(),
                "saved_at": time.time() if saved_at is None else float(saved_at),
            }
            # Atomic write: temp file first, then rename on the same filesystem.
            tmp_path = target.with_suffix(target.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(target)
            logger.info("Saved three-tier state (schema v%d) to %s", SCHEMA_VERSION, target)
            return True
        except Exception as e:
            logger.error("Failed to save memory state: %s", e)
            tmp_path = target.with_suffix(target.suffix + ".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            return False

    def load_state(
        self,
        path: Optional[Path] = None,
    ) -> Tuple[RecentContext, EpisodicMemory, SemanticMemory]:
        """Load the full three-tier state, migrating legacy files.

        - missing file    -> empty structures
        - malformed file   -> empty structures (never raises)
        - schema v1 (flat ``entries``) -> non-destructive migration: the
          original file is backed up to ``<path>.v1.bak`` first, then every
          legacy entry becomes a SemanticMemory item.
        - schema v2        -> loaded verbatim.

        No data is silently dropped: every recognisable field is carried over.
        """
        target = Path(path) if path else self.storage_path
        if not target.exists():
            return RecentContext(), EpisodicMemory(), SemanticMemory()
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to load memory state (returning empty): %s", e)
            return RecentContext(), EpisodicMemory(), SemanticMemory()

        if "recent" in data or "semantic" in data or "episodic" in data:
            recent = RecentContext.from_dict(data.get("recent", {}))
            episodic = EpisodicMemory.from_dict(data.get("episodic", {}))
            semantic = SemanticMemory.from_dict(data.get("semantic", {}))
            return recent, episodic, semantic

        if "entries" in data:
            # Legacy v1 flat file: back up before migrating (non-destructive).
            self._backup_legacy(target)
            semantic = self._migrate_entries(data.get("entries", []))
            return RecentContext(), EpisodicMemory(), semantic

        logger.warning("Unrecognised memory file format at %s (returning empty)", target)
        return RecentContext(), EpisodicMemory(), SemanticMemory()

    @staticmethod
    def _backup_legacy(target: Path) -> None:
        """Copy a legacy v1 file to ``<path>.v1.bak`` once (non-destructive)."""
        backup = target.with_suffix(target.suffix + ".v1.bak")
        try:
            if not backup.exists():
                shutil.copy2(target, backup)
        except Exception as e:
            logger.warning("Could not back up legacy memory file: %s", e)

    @staticmethod
    def _migrate_entries(entries: List[Dict[str, Any]]) -> SemanticMemory:
        """Convert legacy MemoryEntry dicts into a SemanticMemory (v1 -> v2)."""
        semantic = SemanticMemory()
        for item in entries:
            if not isinstance(item, dict):
                continue
            if "kind" in item and "content" in item:
                entry = MemoryItem.from_dict(item)
            elif "content" in item:
                source_ids = item.get("tags") or (
                    [item["source"]] if item.get("source") else []
                )
                entry = MemoryItem(
                    id=item.get("id", f"migrated_{len(semantic.items)}"),
                    content=item["content"],
                    kind=item.get("category", "fact"),
                    source_ids=list(source_ids),
                    confidence=item.get("importance", 0.5),
                )
            else:
                continue
            semantic.add_item(entry)
        return semantic

    def add_entry(self, entry: MemoryItem) -> None:
        """Add an entry to in-memory store."""
        self._in_memory[entry.id] = entry

    def get_entry(self, entry_id: str) -> Optional[MemoryItem]:
        """Get an entry by ID."""
        return self._in_memory.get(entry_id)

    def remove_entry(self, entry_id: str) -> bool:
        """Remove an entry. Returns True if found and removed."""
        if entry_id in self._in_memory:
            del self._in_memory[entry_id]
            return True
        return False

    def get_all(self) -> List[MemoryItem]:
        """Get all in-memory entries."""
        return list(self._in_memory.values())


__all__ = ["MemoryPersistence"]
