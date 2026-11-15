"""Memory persistence: save/load memory to/from disk."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import MemoryEntry
from .manager import MemoryManager

logger = logging.getLogger("boster.persistence")


class MemoryPersistence:
    """Save and load memory entries to/from JSON files."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".hermes" / "memory.json"
        self._in_memory: Dict[str, MemoryEntry] = {}

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
                entry = MemoryEntry.from_dict(item)
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
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Saved %d memory entries to %s", len(self._in_memory), self.storage_path)
            return True
        except Exception as e:
            logger.error("Failed to save memory: %s", e)
            return False

    def add_entry(self, entry: MemoryEntry) -> None:
        """Add an entry to in-memory store."""
        self._in_memory[entry.id] = entry

    def get_entry(self, entry_id: str) -> Optional[MemoryEntry]:
        """Get an entry by ID."""
        return self._in_memory.get(entry_id)

    def remove_entry(self, entry_id: str) -> bool:
        """Remove an entry. Returns True if found and removed."""
        if entry_id in self._in_memory:
            del self._in_memory[entry_id]
            return True
        return False

    def get_all(self) -> List[MemoryEntry]:
        """Get all in-memory entries."""
        return list(self._in_memory.values())


__all__ = ["MemoryPersistence"]
