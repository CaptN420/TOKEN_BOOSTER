"""Context Integrity Engine — preserves facts, decisions, and relationships.

This engine understands the semantic structure of conversation context
and ensures that compression does not lose critical information.

It tracks:
  - Facts: standalone pieces of information
  - Decisions: choices made with their reasoning
  - Constraints: limitations and requirements
  - Relationships: connections between facts/decisions
  - Dependencies: task ordering and prerequisites
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ── Data models ───────────────────────────────────────────────────────


@dataclass
class Fact:
    """A piece of preserved information."""
    id: str
    content: str
    category: str  # fact, decision, constraint, preference, task, error
    importance: float = 0.5
    source_message_index: int = -1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "source_message_index": self.source_message_index,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
        return cls(
            id=data["id"],
            content=data["content"],
            category=data.get("category", "fact"),
            importance=data.get("importance", 0.5),
            source_message_index=data.get("source_message_index", -1),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class FactRelation:
    """A relationship between two facts."""
    source_id: str
    target_id: str
    relation_type: str  # "depends_on", "contradicts", "supports", "part_of", "causes"
    strength: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type,
            "strength": self.strength,
        }


@dataclass
class ContextIntegrityState:
    """Structured representation of context integrity."""
    facts: List[Fact] = field(default_factory=list)
    relations: List[FactRelation] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    pending_tasks: List[str] = field(default_factory=list)
    version: int = 1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": [f.to_dict() for f in self.facts],
            "relations": [r.to_dict() for r in self.relations],
            "decisions": self.decisions,
            "constraints": self.constraints,
            "pending_tasks": self.pending_tasks,
            "version": self.version,
            "created_at": self.created_at,
        }


# ── Extraction patterns ───────────────────────────────────────────────


FACT_PATTERNS: List[tuple] = [
    # Paths and locations
    (re.compile(r"(?:path|dir|directory|folder|workspace)[:=]\s*([~/][\w./\\-]+)", re.IGNORECASE), "fact"),
    # Decisions with reasoning
    (re.compile(r"\b(decided|chose|selected|opted|settled|confirmed)\s+(?:on\s+)?(.+?)[.!]?$", re.IGNORECASE), "decision"),
    # Constraints and requirements
    (re.compile(r"\b(must|cannot|should not|required|constraint|limitation)\b.*", re.IGNORECASE), "constraint"),
    # Errors and failures
    (re.compile(r"\b(ERROR|FAILED|Exception|Traceback|crash)\b.*", re.IGNORECASE), "error"),
    # Preferences
    (re.compile(r"\b(prefer|like|dislike|want|need|love|hate)\s+", re.IGNORECASE), "preference"),
    # Task markers
    (re.compile(r"\b(todo|task|action item|work on|implement|fix|build)\s+", re.IGNORECASE), "task"),
]


# ── Integrity Engine ──────────────────────────────────────────────────


class ContextIntegrityEngine:
    """Extracts and preserves structured information from context.

    This engine analyzes conversation messages to extract facts, decisions,
    constraints, and relationships. It ensures that compression preserves
    the semantic structure of the context.
    """

    def __init__(self):
        self._facts: Dict[str, Fact] = {}
        self._relations: List[FactRelation] = []

    @property
    def fact_count(self) -> int:
        return len(self._facts)

    def extract_from_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> ContextIntegrityState:
        """Extract structured information from conversation messages.

        Args:
            messages: Conversation messages to analyze.

        Returns:
            ContextIntegrityState with extracted facts and relations.
        """
        state = ContextIntegrityState()

        for msg_idx, msg in enumerate(messages):
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue

            # Extract facts from content
            for pattern, default_category in FACT_PATTERNS:
                matches = pattern.finditer(content)
                for m in matches:
                    fact_content = m.group(0).strip()[:200]  # Truncate long facts
                    fact_id = self._make_id(fact_content, msg_idx)

                    if fact_id not in self._facts:
                        fact = Fact(
                            id=fact_id,
                            content=fact_content,
                            category=default_category,
                            importance=0.6,
                            source_message_index=msg_idx,
                        )
                        self._facts[fact_id] = fact
                        state.facts.append(fact)

            # Extract decisions specifically
            dec_match = re.search(
                r"\b(decided|chose|selected|opted|settled|confirmed)\s+(?:on\s+)?(.+?)[.!]?$",
                content, re.IGNORECASE,
            )
            if dec_match:
                decision_text = dec_match.group(2).strip().rstrip(".")[:150]
                state.decisions.append({
                    "text": decision_text,
                    "source_message_index": msg_idx,
                    "timestamp": time.time(),
                })

            # Extract constraints
            const_match = re.search(
                r"\b(must|cannot|should not|required)\s+(.+?)[.!]?$",
                content, re.IGNORECASE,
            )
            if const_match:
                constraint_text = const_match.group(2).strip().rstrip(".")[:150]
                if constraint_text not in state.constraints:
                    state.constraints.append(constraint_text)

            # Extract pending tasks
            task_match = re.search(
                r"\b(todo|task|action item)\s*[:=]\s*(.+?)[.!]?$",
                content, re.IGNORECASE,
            )
            if task_match:
                task_text = task_match.group(2).strip().rstrip(".")[:100]
                if task_text not in state.pending_tasks:
                    state.pending_tasks.append(task_text)

        # Build relations between facts
        self._build_relations(state)

        return state

    def _build_relations(self, state: ContextIntegrityState) -> None:
        """Build relationships between extracted facts."""
        # Group facts by category for relation building
        by_category: Dict[str, List[Fact]] = {}
        for fact in state.facts:
            by_category.setdefault(fact.category, []).append(fact)

        # Create "supports" relations between same-category facts
        for category, facts in by_category.items():
            if len(facts) > 1:
                for i in range(len(facts) - 1):
                    relation = FactRelation(
                        source_id=facts[i].id,
                        target_id=facts[i + 1].id,
                        relation_type="supports",
                        strength=0.4,
                    )
                    state.relations.append(relation)

        # Link decisions to their supporting facts
        for decision in state.decisions:
            dec_idx = decision.get("source_message_index", -1)
            for fact in state.facts:
                if fact.source_message_index == dec_idx:
                    relation = FactRelation(
                        source_id=fact.id,
                        target_id=f"decision_{dec_idx}",
                        relation_type="supports",
                        strength=0.7,
                    )
                    state.relations.append(relation)

    def _make_id(self, content: str, msg_idx: int) -> str:
        """Generate a unique ID for a fact."""
        h = hashlib.md5(f"{msg_idx}:{content[:50]}".encode()).hexdigest()[:10]
        return f"fact_{h}"

    def preserve_in_compression(
        self,
        original_text: str,
        compressed_text: str,
    ) -> Dict[str, Any]:
        """Check that compression preserved critical information.

        Args:
            original_text: Text before compression.
            compressed_text: Text after compression.

        Returns:
            Dict with preservation status and any missing items.
        """
        # Extract facts from both versions
        orig_state = self.extract_from_messages([{"content": original_text}])
        comp_state = self.extract_from_messages([{"content": compressed_text}])

        # Compare fact counts
        orig_fact_ids = {f.id for f in orig_state.facts}
        comp_fact_ids = {f.id for f in comp_state.facts}
        missing_facts = orig_fact_ids - comp_fact_ids

        # Check decision preservation
        orig_decisions = set(d["text"][:50] for d in orig_state.decisions)
        comp_decisions = set(d["text"][:50] for d in comp_state.decisions)
        missing_decisions = orig_decisions - comp_decisions

        # Check constraint preservation
        missing_constraints = []
        for c in orig_state.constraints:
            if c not in compressed_text:
                missing_constraints.append(c)

        return {
            "facts_preserved": len(orig_fact_ids) - len(missing_facts),
            "facts_missing": len(missing_facts),
            "decisions_preserved": len(orig_decisions) - len(missing_decisions),
            "constraints_preserved": len(orig_state.constraints) - len(missing_constraints),
            "missing_constraints": missing_constraints,
            "integrity_ok": len(missing_facts) == 0 and len(missing_decisions) == 0,
        }

    def get_state_dict(self) -> Dict[str, Any]:
        """Get current integrity state as dict."""
        return ContextIntegrityState(
            facts=list(self._facts.values()),
            relations=list(self._relations),
        ).to_dict()


__all__ = [
    "ContextIntegrityEngine",
    "Fact",
    "FactRelation",
    "ContextIntegrityState",
]
