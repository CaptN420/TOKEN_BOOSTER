"""Memory consolidation: merge related entries, remove duplicates, preserve provenance.

Consolidation rules:
  - Keep raw recent exchanges verbatim (no rewriting)
  - Consolidate older exchanges into structured episodes with provenance refs
  - Extract facts, entities, relations, decisions, contradictions
  - Never delete the raw trace that produced a consolidation
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    Contradiction,
    Decision,
    Entity,
    MemoryItem,
    RecentContext,
    Relation,
    SemanticMemory,
)

logger = logging.getLogger("boster.consolidation")

# Decision patterns - more flexible
_DECISION_PATTERNS = [
    re.compile(r"let['\']?s\s+(?:go|use|stick|keep)\s+with\s+([^.!?]+)", re.IGNORECASE),
    re.compile(r"(?:chosen|selected|decided|confirmed)\s*[:\-]\s*([^.!?]+)", re.IGNORECASE),
    re.compile(r"(\w+)\s+(?:instead of|rather than)\s+(\w+)", re.IGNORECASE),
    re.compile(r"we\s+chose\s+(\w+)", re.IGNORECASE),
    re.compile(r"we'll\s+use\s+(\w+)", re.IGNORECASE),
    re.compile(r"use\s+(\w+)\s+for", re.IGNORECASE),
    re.compile(r"\b(use|using|uses)\s+(\w+(?:\s+\w+)?)\b", re.IGNORECASE),
]

# Contradiction patterns
_CONTRADICTION_PATTERNS = [
    re.compile(r"(?:but|however|actually|wait| reconsider)\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:was|previously)\s+([^.!?]+)\s+(?:now|changed to)\s+([^.!?]+)", re.IGNORECASE),
]


def _extract_entities(text: str) -> List[Entity]:
    """Extract named entities from text."""
    entities: List[Entity] = []

    # Extract capitalized phrases (projects, products)
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        name = match.group(1).strip()
        if len(name) > 3 and not name.isdigit():
            entities.append(Entity(name=name, kind="project", source_ids=[]))

    # Extract individual capitalized names (people)
    for match in re.finditer(r"\b([A-Z][a-z]+)\b", text):
        name = match.group(1)
        if len(name) > 2 and name not in ["The", "And", "For", "Not", "We", "Or"]:
            # Check if it's not already captured as part of a phrase
            if not any(name in e.name for e in entities):
                entities.append(Entity(name=name, kind="person", source_ids=[]))

    # Extract known tools/technologies
    tool_patterns = [
        (r"\b(SQLite|PostgreSQL|Redis|Kubernetes|Kafka|gRPC|GraphQL|REST)\b", "tool"),
        (r"\b(Docker|pytest|Python|Linux|Windows|macOS)\b", "tool"),
        (r"\b(terminal|web|dashboard|UI)\b", "concept"),
    ]
    for pattern, kind in tool_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                name = match.group(1)
                if not any(e.name.lower() == name.lower() for e in entities):
                    entities.append(Entity(name=name, kind=kind, source_ids=[]))

    return entities


def _clean_relation_object(obj: str) -> str:
    """Clean up relation object to remove trailing filler words."""
    # Remove trailing prepositions and articles
    obj = re.sub(r'\s+(?:for|to|the|a|an)\s*$', '', obj, flags=re.IGNORECASE)
    return obj.strip()


def _extract_relations(text: str, entities: List[str]) -> List[Relation]:
    """Extract relations between entities from text."""
    relations: List[Relation] = []

    # Extract "X uses Y" patterns - more flexible
    use_patterns = [
        (re.compile(r"(\w+)\s+uses\s+(\w+(?:\s+\w+)?)", re.IGNORECASE), "uses"),
        (re.compile(r"(\w+)\s+is\s+written\s+in\s+(\w+)", re.IGNORECASE), "uses"),
        (re.compile(r"(\w+)\s+runs?\s+on\s+(\w+)", re.IGNORECASE), "runs-on"),
        (re.compile(r"(\w+)\s+stores?\s+(\w+(?:\s+\w+)?)", re.IGNORECASE), "stores"),
        (re.compile(r"(\w+)\s+owns?\s+(\w+(?:\s+\w+)?)", re.IGNORECASE), "owns"),
        (re.compile(r"(\w+)\s+chose\s+(\w+(?:\s+\w+)?)", re.IGNORECASE), "chose"),
    ]

    # Extract role relationships
    role_patterns = [
        (re.compile(r"(\w+)\s+is\s+(?:the\s+)?lead\s+developer", re.IGNORECASE), "lead-dev"),
        (re.compile(r"(\w+)\s+is\s+(?:the\s+)?architect", re.IGNORECASE), "architect"),
    ]

    all_patterns = use_patterns + role_patterns

    for pattern, rel_type in all_patterns:
        for match in pattern.finditer(text):
            subject = match.group(1).strip().capitalize()
            obj = match.group(2).strip().capitalize() if match.lastindex and match.lastindex >= 2 else ""
            # Clean up object - remove trailing words like "for"
            obj = _clean_relation_object(obj)
            if subject != obj and len(subject) > 1 and len(obj) > 1:
                relations.append(Relation(subject=subject, predicate=rel_type, object=obj))

    return relations


def _extract_decisions(text: str) -> List[Decision]:
    """Extract decisions from text."""
    decisions: List[Decision] = []
    for pattern in _DECISION_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip() if match.lastindex else match.group(0).strip()
            decisions.append(Decision(value=value, reason="extracted from text"))
    return decisions


def _detect_contradictions(exchanges: List[Dict[str, str]]) -> List[Contradiction]:
    """Detect contradictions across exchanges."""
    contradictions: List[Contradiction] = []

    # Collect all claims with their source IDs
    claims: List[Tuple[int, str, str]] = []
    for idx, ex in enumerate(exchanges):
        content = ex.get("content", "")
        if not content:
            continue

        # Look for claim indicators - be more inclusive
        lower_content = content.lower()
        # Check for any statement about technology choices
        if any(kw in lower_content for kw in ["should use", "let's use", "we chose", "decided on", "actually", "wait", "instead"]):
            claims.append((idx, content.strip(), f"msg_{idx}"))

    # Find contradictions (claims about same topic with different values)
    # Group by topic keywords
    topic_map: Dict[str, List[Tuple[int, str, str]]] = {}
    for idx, claim, source in claims:
        # Extract key entities from claim
        tokens = set(re.findall(r"\b([A-Z][a-z]+)\b", claim))
        for token in tokens:
            topic_map.setdefault(token.lower(), []).append((idx, claim, source))

    # Find contradictions within same topic
    for topic, topic_claims in topic_map.items():
        if len(topic_claims) >= 2:
            first, last = topic_claims[0], topic_claims[-1]
            if first[0] != last[0]:
                contra_id = hashlib.md5(f"{topic}_{first[0]}_{last[0]}".encode()).hexdigest()[:8]
                contradictions.append(Contradiction(
                    id=f"contra_{contra_id}", claim_a=first[1], claim_b=last[1],
                    source_a=first[2], source_b=last[2],
                ))

    return contradictions


def consolidate_exchanges(
    exchanges: List[Dict[str, str]],
    *,
    keep_recent: int = 5,
) -> Tuple[RecentContext, SemanticMemory]:
    """Consolidate exchanges into recent context + semantic memory."""
    if not exchanges:
        return RecentContext(), SemanticMemory()

    recent = exchanges[-keep_recent:] if len(exchanges) > keep_recent else []
    older = exchanges[:len(exchanges) - keep_recent] if len(exchanges) > keep_recent else []

    recent_ctx = RecentContext()
    for ex in recent:
        recent_ctx.add(ex)

    semantic = SemanticMemory()
    all_entities: Set[str] = set()
    item_id_counter = 0

    for ex in older:
        content = ex.get("content", "")
        source_id = f"msg_{item_id_counter}"
        item_id_counter += 1
        if not content:
            continue

        entities = _extract_entities(content)
        for ent in entities:
            ent.source_ids = [source_id]
            semantic.upsert_entity(ent)
            all_entities.add(ent.name.lower())

        rels = _extract_relations(content, list(all_entities))
        for rel in rels:
            rel.source_id = source_id
            semantic.add_relation(rel)

        decs = _extract_decisions(content)
        for dec in decs:
            dec.date = time.time()
            semantic.add_decision(dec)

        kind = "fact"
        if "decide" in content.lower() or "choose" in content.lower() or "let's" in content.lower():
            kind = "decision"
        elif "contradict" in content.lower() or "but" in content.lower() or "actually" in content.lower():
            kind = "contradiction"

        item = MemoryItem(
            id=f"item_{item_id_counter}", content=content[:200],
            kind=kind, source_ids=[source_id], status="active",
        )
        semantic.add_item(item)

    if len(older) > 1:
        for contra in _detect_contradictions(older):
            semantic.add_contradiction(contra)

    logger.info(
        "Consolidated %d older exchanges -> %d items, %d entities, %d relations, %d decisions, %d contradictions",
        len(older), len(semantic.items), len(semantic.entities),
        len(semantic.relations), len(semantic.decisions), len(semantic.contradictions),
    )
    return recent_ctx, semantic


def consolidate_similar(
    items: List[MemoryItem],
    similarity_threshold: float = 0.8,
) -> List[MemoryItem]:
    """Merge similar memory items, keeping the most important one."""
    if len(items) <= 1:
        return items

    result: List[MemoryItem] = []
    used: Set[str] = set()

    for i, item_a in enumerate(items):
        if item_a.id in used:
            continue
        merged = item_a
        for j, item_b in enumerate(items[i + 1:], i + 1):
            if item_b.id in used:
                continue
            if _similarity(item_a.content, item_b.content) >= similarity_threshold:
                merged = MemoryItem(
                    id=merged.id, content=merged.content, kind=merged.kind,
                    source_ids=list(set(merged.source_ids + item_b.source_ids)),
                    confidence=max(merged.confidence, item_b.confidence),
                    status=merged.status,
                )
                used.add(item_b.id)
        used.add(item_a.id)
        result.append(merged)

    return result


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity between two text strings."""
    words_a = set(re.sub(r"[^\w\s]", " ", a).lower().split())
    words_b = set(re.sub(r"[^\w\s]", " ", b).lower().split())
    if not words_a and not words_b:
        return 1.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / max(len(union), 1)



def deduplicate_by_category(
    items: List[MemoryItem],
    category: str,
) -> List[MemoryItem]:
    """Deduplicate entries within a category. Returns deduplicated list."""
    categorized = [item for item in items if item.kind == category]
    if len(categorized) <= 1:
        return items
    original_count = len(categorized)
    consolidated = consolidate_similar(categorized)
    # Keep non-categorized items, replace categorized with consolidated
    other = [item for item in items if item.kind != category]
    return other + consolidated

__all__ = ["consolidate_exchanges", "consolidate_similar", "deduplicate_by_category"]
