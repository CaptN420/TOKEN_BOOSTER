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
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    UNKNOWN_TIME,
    Contradiction,
    Decision,
    Entity,
    Episode,
    EpisodicMemory,
    MemoryItem,
    RecentContext,
    Relation,
    SemanticMemory,
)

logger = logging.getLogger("boster.consolidation")

# ── Decision extraction (v2.2.3) ────────────────────────────────────
# A decision is only recorded when the modality is firm. Possibilities,
# preferences and hedged language are deliberately NOT stored: an unextracted
# information is better than a misinterpreted one.
DECISION_STATUSES = frozenset({
    "unknown", "proposed", "tentative", "decided", "confirmed",
    "rejected", "superseded", "active",
})

# Statuses that are stored as a Decision. ``active`` is the historical value
# for a firm, current decision (kept for backward compatibility);
# ``superseded`` is applied later, only on an explicit replacement.
_STORED_DECISION_STATUSES = frozenset({"active", "proposed", "rejected"})

# A name token, case-sensitive on purpose: under a case-insensitive class the
# capitalised branch also matches lowercase words and the object swallows the
# whole trailing clause ("SQLite because it is lightweight").
_DECISION_OBJECT = r"([A-Z][A-Za-z0-9+-]*(?:\s+[A-Z][A-Za-z0-9+-]*)*|[A-Za-z][A-Za-z0-9+-]*)"

# Ordered: the FIRST matching rule wins. Firm rules come first so a firm
# commitment is not shadowed; modality-bearing rules are tried next. Only the
# keyword part is case-insensitive — never the captured object.
_DECISION_RULES = [
    # explicit rejection is itself a recorded act
    (re.compile(r"(?i:\bwe\s+(?:rejected|ruled\s+out|abandoned|dropped)\s+)" + _DECISION_OBJECT), "rejected"),
    (re.compile(r"(?i:\bwe\s+(?:won't|will\s+not)\s+(?:be\s+)?use\s+)" + _DECISION_OBJECT), "rejected"),
    # firm decisions
    (re.compile(r"(?i:\bwe\s+(?:decided|agreed)\s+(?:to\s+use|to\s+go\s+with|on|upon)\s+)" + _DECISION_OBJECT), "active"),
    (re.compile(r"(?i:\bwe\s+chose\s+)" + _DECISION_OBJECT), "active"),
    (re.compile(r"(?i:\b(?:the\s+)?decision\s+(?:was|is)\s+(?:to\s+)?(?:use|adopt|choose|go\s+with)\s+)" + _DECISION_OBJECT), "active"),
    (re.compile(r"(?i:\blet['\u2019]?s\s+(?:use|go\s+with|stick\s+with|keep)\s+)" + _DECISION_OBJECT), "active"),
    # future commitment: recorded, but not as a confirmed decision
    (re.compile(r"(?i:\bwe\s+(?:will|are\s+going\s+to)\s+use\s+)" + _DECISION_OBJECT), "proposed"),
    # possibility / consideration -> NOT a decision
    (re.compile(r"(?i:\bwe\s+(?:could|might|may|would)\s+(?:also\s+)?(?:use|adopt|try|consider|migrate\s+to|switch\s+to)\s+)" + _DECISION_OBJECT), "tentative"),
    (re.compile(r"(?i:\b(?:we\s+are\s+)?consider(?:ing|ed)\s+(?:using\s+|migrating\s+to\s+|adopting\s+)?)" + _DECISION_OBJECT), "tentative"),
    (re.compile(r"(?i:\bshould\s+(?:maybe\s+)?use\s+)" + _DECISION_OBJECT), "tentative"),
    # any hedge -> NOT a decision
    (re.compile(r"(?i:\b(?:maybe|perhaps|possibly)\b|\bwould\s+be\s+preferable\b)"), "unknown"),
]

# Explicit replacement markers. Only these allow marking an older decision as
# superseded — a mere wording resemblance is never treated as proof.
_REPLACEMENT_PATTERNS = [
    re.compile(r"(?i:\binstead\s+of\s+)" + _DECISION_OBJECT),
    re.compile(r"(?i:\breplac(?:e|es|ing|ed)\s+(?:by\s+)?)" + _DECISION_OBJECT),
    re.compile(r"(?i:\bno\s+longer\s+us(?:e|es|ing)\s+)" + _DECISION_OBJECT),
    re.compile(r"(?i:\bswitch(?:ing|ed)?\s+(?:away\s+)?from\s+)" + _DECISION_OBJECT),
]


def classify_decision(text: str) -> List[Tuple[str, str]]:
    """Classify the decision utterance of ``text`` as ``(value, status)``.

    Returns at most one pair: the first matching rule wins, and ``value`` is
    the real decision object (never the verb). A modality that is not storable
    (possibility, preference, hedge) is returned with an empty value so the
    caller can see the modality without recording a decision. Empty list means
    no decision-like utterance at all.
    """
    for pattern, status in _DECISION_RULES:
        match = pattern.search(text)
        if match is None:
            continue
        value = ""
        if match.lastindex and match.lastindex >= 1:
            value = (match.group(1) or "").strip()
        return [(value, status)]
    return []

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


# ── Relation extraction (v2.2.3) ────────────────────────────────────
# Precision first: a relation is emitted only when the subject is an entity we
# actually recognised, both sides are unambiguous, and the object does not
# swallow a connective ("because", "since", ...). Direction is normalised so a
# passive sentence yields the same triple as its active form.
_RELATION_STOPWORDS = frozenset({
    "we", "i", "you", "they", "he", "she", "it", "this", "that", "these",
    "those", "the", "a", "an", "and", "or", "but", "then", "there", "here",
    "now", "also", "maybe", "perhaps", "one", "some", "all", "its", "our",
})
_RELATION_CONNECTIVES = frozenset({
    "because", "since", "while", "although", "though", "but", "and", "so",
    "hence", "thus", "however", "therefore",
})

_RELATION_OBJECT = r"([A-Z][A-Za-z0-9+-]*(?:\s+[A-Z][A-Za-z0-9+-]*)*|[A-Za-z][A-Za-z0-9+-]*)"

# (pattern, predicate, passive) — ``passive`` swaps subject/object so that
# "SQLite is used by Orion" becomes "Orion -> uses -> SQLite". Only the keyword
# part is case-insensitive; the captured object never is.
_RELATION_RULES = [
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+is\s+used\s+by\s+)" + _RELATION_OBJECT), "uses", True),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+uses\s+)" + _RELATION_OBJECT), "uses", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+is\s+written\s+in\s+)" + _RELATION_OBJECT), "uses", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+works?\s+on\s+)" + _RELATION_OBJECT), "works-on", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+is\s+responsible\s+for\s+)" + _RELATION_OBJECT), "responsible-for", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+depends\s+on\s+)" + _RELATION_OBJECT), "depends-on", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+owns?\s+)" + _RELATION_OBJECT), "owns", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+runs?\s+on\s+)" + _RELATION_OBJECT), "runs-on", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+stores?\s+)" + _RELATION_OBJECT), "stores", False),
    (re.compile(r"(?i:\b([A-Za-z][A-Za-z0-9+-]*)\s+chose\s+)" + _RELATION_OBJECT), "chose", False),
]


def _clean_relation_object(obj: str) -> str:
    """Drop leading articles and trailing filler/punctuation from an object."""
    obj = (obj or "").strip()
    obj = re.sub(r"^(?:the|a|an)\s+", "", obj, flags=re.IGNORECASE)
    obj = re.sub(r"\s+(?:for|to|the|a|an)\s*$", "", obj, flags=re.IGNORECASE)
    obj = re.sub(r"[.,;:!?]+$", "", obj)
    return obj.strip()


def _is_ambiguous_name(name: str) -> bool:
    """True when a name cannot be trusted as a relation endpoint."""
    lowered = (name or "").strip().lower()
    if len(lowered) <= 1:
        return True
    return lowered in _RELATION_STOPWORDS or lowered in _RELATION_CONNECTIVES


def _extract_relations(text: str, entities: List[str]) -> List[Relation]:
    """Extract relations between recognised entities from text.

    ``entities`` is the set of lowercased names already extracted from the
    text; a relation whose subject is not one of them is not emitted (an
    ambiguous relation is worse than a missing one).
    """
    relations: List[Relation] = []
    known = {name.lower() for name in entities}
    # a subject may be one word of a multi-word entity ("Orion" inside
    # "Project Orion"): accept any token of a recognised name.
    known_words = set()
    for name in entities:
        for word in re.split(r"\s+", str(name).strip().lower()):
            if word:
                known_words.add(word)

    for pattern, rel_type, passive in _RELATION_RULES:
        for match in pattern.finditer(text):
            left = _clean_relation_object((match.group(1) or "").strip())
            right = _clean_relation_object((match.group(2) or "").strip())
            if passive:
                left, right = right, left
            if not left or not right:
                continue
            if _is_ambiguous_name(left) or _is_ambiguous_name(right):
                continue
            if left.lower() == right.lower():
                continue
            if left.lower() not in known and left.lower() not in known_words:
                continue
            relations.append(Relation(subject=left, predicate=rel_type, object=right))

    return relations


def _extract_decisions(text: str) -> List[Decision]:
    """Extract *firm* decisions only.

    Possibilities, considerations, preferences and hedged statements are not
    turned into decisions (see :func:`classify_decision`).
    """
    decisions: List[Decision] = []
    for value, status in classify_decision(text):
        if status not in _STORED_DECISION_STATUSES or not value:
            continue
        decisions.append(
            Decision(value=value, reason="extracted from text", status=status)
        )
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
    """Consolidate exchanges into recent context + semantic memory.

    Deterministic: every artifact it creates is stamped with the newest source
    timestamp (``UNKNOWN_TIME`` when the messages carry none) — never with the
    wall clock.  Identical input therefore yields identical output.
    """
    if not exchanges:
        return RecentContext(), SemanticMemory()

    stamp = pipeline_stamp(exchanges)

    # Split exchanges into a kept "recent" tail and an older prefix that is
    # consolidated. The split must never drop messages and must never overlap:
    #   - keep_recent=0  -> nothing kept, everything consolidated
    #   - len <= keep_recent -> everything kept, nothing consolidated
    # (The previous `exchanges[-keep_recent:] if len > keep_recent else []`
    #  dropped every message whenever len(exchanges) <= keep_recent, and
    #  `exchanges[-0:]` returned the *whole* list, double-counting on kr=0.)
    keep = min(max(keep_recent, 0), len(exchanges))
    older = exchanges[:len(exchanges) - keep]
    recent = exchanges[len(exchanges) - keep:] if keep > 0 else []

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
            ent.created_at = stamp
            semantic.upsert_entity(ent)
            all_entities.add(ent.name.lower())

        rels = _extract_relations(content, list(all_entities))
        for rel in rels:
            rel.source_id = source_id
            rel.created_at = stamp
            semantic.add_relation(rel)

        decs = _extract_decisions(content)
        message_date = _message_timestamp(ex)
        for dec in decs:
            dec.date = message_date
            semantic.add_decision(dec)

        kind = "fact"
        if "decide" in content.lower() or "choose" in content.lower() or "let's" in content.lower():
            kind = "decision"
        elif "contradict" in content.lower() or "but" in content.lower() or "actually" in content.lower():
            kind = "contradiction"

        item = MemoryItem(
            id=f"item_{item_id_counter}", content=content[:200],
            kind=kind, source_ids=[source_id], status="active",
            created_at=stamp, updated_at=stamp,
        )
        semantic.add_item(item)

    if len(older) > 1:
        for contra in _detect_contradictions(older):
            contra.created_at = stamp
            semantic.add_contradiction(contra)

    # History is never rewritten: only an explicit replacement marker may mark
    # an earlier decision as superseded.
    _apply_supersessions(semantic, older)

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

# ── Episodic consolidation (v2.2, Phase 2.2) ────────────────────────
# Additive sibling of ``consolidate_exchanges`` (whose public 2-tuple
# contract is left untouched). It produces an EpisodicMemory of coherent
# conversation units that *reference* the durable knowledge held in
# SemanticMemory instead of copying it.

# Explicit topic-change markers. Only unambiguous phrases split an episode:
# lexical dissimilarity alone is never treated as proof of a topic change,
# and sharing an entity never merges (or splits) episodes. When no marker
# and no session gap is found the episode simply grows — a larger episode
# is always preferred over arbitrary fragmentation.
_TOPIC_BREAK_PATTERNS = [
    re.compile(
        r"\b(?:moving on|moving to|on to|next topic|next up|new topic|"
        r"changing topic|change of topic|different topic|unrelated|"
        r"separately|switching to|switch to|let'?s talk about|"
        r"let'?s discuss|now about|as for)\b",
        re.IGNORECASE,
    ),
]

# Optional session boundary: only used when messages carry real timestamps.
_SESSION_GAP_SECONDS = 3600.0
_EPISODE_SUMMARY_MAX = 120


# ── Durable provenance (v2.2.1) ─────────────────────────────────────
# Message ids must survive prepending, windowed consolidation and reloads,
# so they are never derived from the local index alone.
_NATIVE_MESSAGE_ID_KEYS = ("id", "message_id", "msg_id")
_CONVERSATION_ID_KEYS = ("conversation_id", "session_id", "conv_id")


def _native_message_id(ex: Dict[str, Any]) -> Optional[str]:
    for key in _NATIVE_MESSAGE_ID_KEYS:
        value = ex.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _conversation_key(exchanges: List[Any], explicit: Optional[str]) -> str:
    """Conversation identity: explicit argument > per-message field > ""."""
    if explicit:
        return str(explicit)
    for ex in exchanges:
        if isinstance(ex, dict):
            for key in _CONVERSATION_ID_KEYS:
                value = ex.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def message_id_for(
    exchange: Dict[str, Any],
    *,
    occurrence: int = 0,
    conversation_id: str = "",
) -> str:
    """Durable, deterministic id for a single message.

    A native id (``id``/``message_id``/``msg_id``) is preferred and read as
    ``mid_<native>``. Otherwise the id is derived from stable fields only —
    conversation, role, content, timestamp, and the occurrence index that
    distinguishes two byte-identical messages. The legacy ``msg_<index>``
    form is never produced but stays readable (ids are opaque strings).
    """
    if not isinstance(exchange, dict):
        raise ValueError("message_id_for expects a dict exchange")
    native = _native_message_id(exchange)
    if native is not None:
        return f"mid_{native}"
    ts = _message_timestamp(exchange)
    basis = "\x1f".join([
        str(conversation_id),
        str(exchange.get("role", "") or ""),
        _message_content(exchange),
        "" if ts is None else format(ts, ".6f"),
        str(int(occurrence)),
    ])
    return "msg_" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]


def episode_id_for(conversation_id: str, first_message_id: str) -> str:
    """Durable episode identity: conversation + first source message.

    Anchoring on the first message keeps a legitimate extension stable, while
    the conversation component stops two windows that merely start with the
    same text from colliding.
    """
    basis = f"{conversation_id}\x1f{first_message_id}"
    return "ep_" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]


def decision_id_for(value: str, source_id: str = "", occurrence: int = 0) -> str:
    """Durable, position-independent decision id."""
    basis = "\x1f".join([str(value), str(source_id), str(int(occurrence))])
    return "dec_" + hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]


def decision_key(decision: "Decision") -> str:
    """Reference key for a decision (durable id, never a position)."""
    return f"decision:{decision.id}"


def resolve_decision_key(semantic: SemanticMemory, key: str) -> Optional["Decision"]:
    """Resolve a ``decision:<...>`` key.

    Supports the v2.2.1 durable form (``decision:<id>``) and, read-only, the
    legacy positional form (``decision:<index>``), so old episodes stay usable.
    """
    if not isinstance(key, str) or not key.startswith("decision:"):
        return None
    payload = key.split(":", 1)[1]
    for decision in semantic.decisions:
        if decision.id and decision.id == payload:
            return decision
    if payload.isdigit():
        index = int(payload)
        if 0 <= index < len(semantic.decisions):
            return semantic.decisions[index]
    return None


def _legacy_message_index(message_id: str) -> Optional[int]:
    """Parse a legacy ``msg_<index>`` reference; None for any other form."""
    if isinstance(message_id, str) and message_id.startswith("msg_"):
        tail = message_id[4:]
        if tail.isdigit():
            return int(tail)
    return None


def _add_decision_unique(semantic: SemanticMemory, decision: Decision) -> Decision:
    """Insert a decision, merging provenance if its durable id already exists."""
    for existing in semantic.decisions:
        if existing.id and existing.id == decision.id:
            existing.source_ids = _dedupe_str(existing.source_ids + decision.source_ids)
            return existing
    semantic.add_decision(decision)
    return decision


def _dedupe_str(items: List[str]) -> List[str]:
    """Remove duplicates while preserving first-seen order."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _message_content(ex: Any) -> str:
    if not isinstance(ex, dict):
        return ""
    return ex.get("content", "") or ""


def _message_timestamp(ex: Any) -> Optional[float]:
    if not isinstance(ex, dict):
        return None
    for key in ("timestamp", "ts", "time"):
        value = ex.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def pipeline_stamp(exchanges: List[Any]) -> float:
    """Deterministic timestamp shared by every artifact of one consolidation.

    The newest source timestamp when the messages carry one, else
    ``UNKNOWN_TIME``.  Never ``time.time()``: the pipeline must reproduce
    byte-identical output for identical input, and it must never invent a date
    the sources do not support.
    """
    stamps = [t for t in (_message_timestamp(ex) for ex in exchanges) if t is not None]
    return max(stamps) if stamps else UNKNOWN_TIME


def _apply_supersessions(semantic: SemanticMemory, exchanges: List[Dict[str, Any]]) -> int:
    """Mark a decision superseded only when the sources explicitly replace it.

    A wording resemblance is never enough: the old value must be named right
    after an explicit marker ("instead of", "replaced by", "no longer use",
    "switch from"). The superseded decision is never deleted — only its status
    changes, so history stays intact. Returns the number of decisions marked.
    """
    marked = 0
    for ex in exchanges:
        text = _message_content(ex)
        if not text:
            continue
        for pattern in _REPLACEMENT_PATTERNS:
            for match in pattern.finditer(text):
                old_value = (match.group(1) or "").strip().lower()
                if not old_value:
                    continue
                for decision in semantic.decisions:
                    if (decision.value.strip().lower() == old_value
                            and decision.status != "superseded"):
                        decision.status = "superseded"
                        marked += 1
    return marked


def _starts_new_topic(text: str) -> bool:
    return any(p.search(text) for p in _TOPIC_BREAK_PATTERNS)


def _segment_older(older_safe: List[Dict[str, Any]]) -> List[List[int]]:
    """Group the non-empty older messages into coherent index segments."""
    segments: List[List[int]] = []
    current: List[int] = []
    last_ts: Optional[float] = None

    for idx, ex in enumerate(older_safe):
        content = _message_content(ex)
        if not content.strip():
            continue

        ts = _message_timestamp(ex)
        split = False
        if current:
            if _starts_new_topic(content):
                split = True
            elif ts is not None and last_ts is not None and (ts - last_ts) > _SESSION_GAP_SECONDS:
                split = True

        if split:
            segments.append(current)
            current = []

        current.append(idx)
        if ts is not None:
            last_ts = ts

    if current:
        segments.append(current)
    return segments


def _episode_summary(texts: List[str]) -> str:
    """First non-empty text of a segment, bounded — never empty."""
    for text in texts:
        text = (text or "").strip()
        if text:
            return text[:_EPISODE_SUMMARY_MAX]
    return ""


def _disambiguated_episode_id(base_id: str, message_ids: List[str]) -> str:
    """Deterministic alternative id for an episode whose identity is uncertain."""
    digest = hashlib.md5(",".join(sorted(message_ids)).encode("utf-8")).hexdigest()[:8]
    return f"{base_id}_{digest}"


def _upsert_episode(
    episodic: "EpisodicMemory",
    episode: "Episode",
    *,
    explicit_conversation: bool = True,
    stamp: Optional[float] = None,
) -> "Episode":
    """Idempotent insert/merge by durable episode id.

    Identity is conversation-anchored (see :func:`episode_id_for`), so an equal
    id means the same episode or a legitimate extension of it. When no
    conversation identity is available the merge is refused as soon as the
    incoming sources are not an extension of the stored ones — two different
    conversations must never be conflated just because they open alike.
    """
    candidate = episode
    while True:
        existing = next(
            (e for e in episodic.episodes
             if isinstance(e, Episode) and e.id == candidate.id),
            None,
        )
        if existing is None:
            episodic.add_episode(candidate)
            return candidate
        extended = set(existing.message_ids) <= set(candidate.message_ids)
        if not explicit_conversation and not extended:
            # Identity is uncertain and this is not an extension: keep both,
            # under a deterministic disambiguated id.
            candidate.id = _disambiguated_episode_id(candidate.id, candidate.message_ids)
            continue
        existing.message_ids = _dedupe_str(existing.message_ids + candidate.message_ids)
        existing.entity_names = _dedupe_str(existing.entity_names + candidate.entity_names)
        existing.relation_keys = _dedupe_str(existing.relation_keys + candidate.relation_keys)
        existing.decision_keys = _dedupe_str(existing.decision_keys + candidate.decision_keys)
        if candidate.starts_at is not None:
            existing.starts_at = (
                candidate.starts_at if existing.starts_at is None
                else min(existing.starts_at, candidate.starts_at)
            )
        if candidate.ends_at is not None:
            existing.ends_at = (
                candidate.ends_at if existing.ends_at is None
                else max(existing.ends_at, candidate.ends_at)
            )
        existing.touch(stamp)
        return existing


def consolidate_to_episodes(
    exchanges: List[Dict[str, Any]],
    *,
    keep_recent: int = 5,
    episodic: Optional["EpisodicMemory"] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[RecentContext, "EpisodicMemory", SemanticMemory]:
    """Consolidate exchanges into recent context + episodic + semantic memory.

    Deterministic and idempotent: identical input yields identical episodes,
    and reusing an existing ``episodic`` upserts by durable id instead of
    duplicating. Provenance uses durable message ids — the native id when the
    message carries one, otherwise a hash of conversation/role/content/
    timestamp/occurrence (never the local index alone) — shared with
    SemanticMemory. No reference is invented for an absent message.

    Every artifact produced is stamped with the newest source timestamp
    (``UNKNOWN_TIME`` when the messages carry none), so the wall clock never
    leaks into the output.
    """
    episodic_mem: "EpisodicMemory" = episodic if episodic is not None else EpisodicMemory()

    if not exchanges:
        return RecentContext(), episodic_mem, SemanticMemory()

    stamp = pipeline_stamp(exchanges)

    # Identical lossless / non-overlapping split to ``consolidate_exchanges``.
    keep = min(max(keep_recent, 0), len(exchanges))
    older = exchanges[:len(exchanges) - keep]
    recent = exchanges[len(exchanges) - keep:] if keep > 0 else []

    recent_ctx = RecentContext()
    for ex in recent:
        recent_ctx.add(ex if isinstance(ex, dict) else {"role": "unknown", "content": ""})

    # Non-dict entries become empty placeholders so positions still map onto
    # the real message list.
    older_safe: List[Dict[str, Any]] = [ex if isinstance(ex, dict) else {} for ex in older]

    conversation = _conversation_key(exchanges, conversation_id)

    # Durable per-message ids; ``occurrence`` disambiguates byte-identical texts.
    mid_by_index: Dict[int, str] = {}
    seen_message_keys: Dict[Tuple[str, str, str], int] = {}
    for idx, ex in enumerate(older_safe):
        ts = _message_timestamp(ex)
        occ_key = (
            str(ex.get("role", "") or ""),
            _message_content(ex),
            "" if ts is None else format(ts, ".6f"),
        )
        occurrence = seen_message_keys.get(occ_key, 0)
        seen_message_keys[occ_key] = occurrence + 1
        mid_by_index[idx] = message_id_for(
            ex, occurrence=occurrence, conversation_id=conversation
        )

    semantic = SemanticMemory()
    all_entities: Set[str] = set()
    per_message: Dict[int, Tuple[List[Entity], List[Relation], List[Decision]]] = {}
    seen_decision_keys: Dict[Tuple[str, str], int] = {}

    for idx, ex in enumerate(older_safe):
        content = _message_content(ex)
        if not content.strip():
            continue
        source_id = mid_by_index[idx]

        entities = _extract_entities(content)
        for ent in entities:
            ent.source_ids = [source_id]
            ent.created_at = stamp
            semantic.upsert_entity(ent)
            all_entities.add(ent.name.lower())

        relations = _extract_relations(content, list(all_entities))
        for rel in relations:
            rel.source_id = source_id
            rel.created_at = stamp
            semantic.add_relation(rel)

        message_decisions: List[Decision] = []
        message_date = _message_timestamp(ex)
        for dec in _extract_decisions(content):
            dec.date = message_date
            dec.source_ids = [source_id]
            occ_key = (dec.value, source_id)
            occurrence = seen_decision_keys.get(occ_key, 0)
            seen_decision_keys[occ_key] = occurrence + 1
            dec.id = decision_id_for(dec.value, source_id=source_id, occurrence=occurrence)
            message_decisions.append(_add_decision_unique(semantic, dec))

        lower = content.lower()
        kind = "fact"
        if "decide" in lower or "choose" in lower or "let's" in lower:
            kind = "decision"
        elif "contradict" in lower or "but" in lower or "actually" in lower:
            kind = "contradiction"

        semantic.add_item(MemoryItem(
            id=f"item_{idx + 1}", content=content[:200], kind=kind,
            source_ids=[source_id], status="active",
            created_at=stamp, updated_at=stamp,
        ))

        per_message[idx] = (entities, relations, message_decisions)

    if len(older_safe) > 1:
        for contra in _detect_contradictions(older_safe):
            contra.created_at = stamp
            # ``_detect_contradictions`` emits legacy ``msg_<index>`` sources;
            # remap them onto durable ids (index == position in the older list).
            for attr in ("source_a", "source_b"):
                legacy_index = _legacy_message_index(getattr(contra, attr))
                if legacy_index is not None and legacy_index in mid_by_index:
                    setattr(contra, attr, mid_by_index[legacy_index])
            semantic.add_contradiction(contra)

    # History is never rewritten: only an explicit replacement marker may mark
    # an earlier decision as superseded.
    _apply_supersessions(semantic, older_safe)

    for segment in _segment_older(older_safe):
        message_ids = [mid_by_index[idx] for idx in segment]
        summary = _episode_summary([_message_content(older_safe[idx]) for idx in segment])
        if not summary:
            continue  # never emit an empty episode

        entity_names: List[str] = []
        relation_keys: List[str] = []
        decision_keys: List[str] = []
        for idx in segment:
            ents, rels, decs = per_message.get(idx, ([], [], []))
            entity_names.extend(e.name for e in ents)
            relation_keys.extend(f"{r.subject}|{r.predicate}|{r.object}" for r in rels)
            decision_keys.extend(decision_key(d) for d in decs)

        timestamps = [
            t for t in (_message_timestamp(older_safe[i]) for i in segment) if t is not None
        ]

        episode = Episode(
            id=episode_id_for(conversation, message_ids[0]),
            summary=summary,
            message_ids=message_ids,
            starts_at=min(timestamps) if timestamps else None,
            ends_at=max(timestamps) if timestamps else None,
            # References, not copies: keep only names resolvable in SemanticMemory.
            entity_names=_dedupe_str([n for n in entity_names if n.lower() in semantic.entities]),
            relation_keys=_dedupe_str(relation_keys),
            decision_keys=_dedupe_str(decision_keys),
            status="active",
            confidence=1.0,
            created_at=stamp,
            updated_at=None,
        )
        _upsert_episode(
            episodic_mem, episode,
            explicit_conversation=bool(conversation), stamp=stamp,
        )

    logger.info(
        "consolidate_to_episodes: %d older exchanges -> %d episodes, %d items, "
        "%d entities, %d relations, %d decisions, %d contradictions",
        len(older_safe), len(episodic_mem.episodes), len(semantic.items),
        len(semantic.entities), len(semantic.relations), len(semantic.decisions),
        len(semantic.contradictions),
    )
    return recent_ctx, episodic_mem, semantic


__all__ = [
    "consolidate_exchanges",
    "consolidate_to_episodes",
    "consolidate_similar",
    "deduplicate_by_category",
    "message_id_for",
    "episode_id_for",
    "decision_id_for",
    "decision_key",
    "resolve_decision_key",
    "classify_decision",
    "DECISION_STATUSES",
    "pipeline_stamp",
    "UNKNOWN_TIME",
]
