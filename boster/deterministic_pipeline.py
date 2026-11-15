"""Deterministic context-optimisation pipeline (Phase 2.4).

Goal: reduce the tokens handed to an LLM **without any LLM**. Every saving comes
from deterministic transformations of existing Boster agents. The LLM is the
consumer of the optimised context, never the optimiser.

Executable stages (10 target responsibilities, fused into 8 stages where the
contracts do not overlap — see ``DETERMINISTIC_PIPELINE_AUDIT.md`` §3):

    1. InputAnalyzer      — roles, system/developer messages, per-turn metadata
    2. FormatDetector     — reuse output_compressor + adaptive detectors
    3. ContentClassifier  — reuse memory.classify_entry, extend with kinds
    4. DuplicateDetector  — reuse context.deduplication
    5. InfoSelector       — deterministic scoring (no recency-only, no keywords-only)
    6. MemoryRetriever    — reuse memory.consolidation + memory.retrieval
    7. ContextReconstructor — typed block, chronological turns, budget enforced
    8. IntegrityValidator — reuse core.policies.SafetyCheck (STATELESS)

Agent 9 (Token Budget Manager) is a phase of stages 5/7; agent 1's analysis is
stage 1. Nothing here reads the wall clock, uses builtin ``hash()``, or iterates
an unordered set in an output path: identical input + identical config yields
byte-identical output (``to_dict()``).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ._tool_domains import TOOL_DOMAIN_MAP
from .adaptive import adaptive_compress
from .compression.code import compress_code
from .compression.json import compress_json
from .compression.logs import compress_logs
from .compression.markdown import compress_markdown
from .compression.structured import compress_csv, compress_diff, compress_git_log, compress_sql
from .compression.terminal import compress_terminal
from .context.deduplication import deduplicate_messages
from .context.relevance import score_text_fragment
from .core.budget import estimate_messages_tokens
from .core.policies import PreservationPolicy, ProtectedMarker, SafetyCheck
from .core.tokenizer import count_tokens, get_tokenizer
from .history_compressor import compress_history
from .memory.consolidation import consolidate_to_episodes, message_id_for
from .memory.manager import MemoryManager, classify_entry
from .memory.models import UNKNOWN_TIME, Decision, Entity, Episode, MemoryItem, Relation
from .memory.retrieval import hybrid_search
from .output_compressor import _auto_detect_format, safe_compress

__all__ = [
    "PipelineConfig",
    "ContentClass",
    "InputAnalysis",
    "PipelineOutput",
    "StageReport",
    "run_deterministic_pipeline",
    "analyze_input",
    "detect_format",
    "classify_content",
    "token_count_kind",
    "BUDGET_LEVELS",
]

# ── mandatory-content markers (deterministic, case-insensitive) ──────────────

MANDATORY_ROLES = ("system", "developer")

# Above this size, a mandatory fragment already kept verbatim (a log or a tool
# dump) is not consolidated again: re-listing it would pay for it twice.
MANDATORY_MEMORY_MAX_CHARS = 1000

_INSTRUCTION_RE = re.compile(
    r"\b(must|must not|shall|should|never|always|do not|don't|required|mandatory|"
    r"tu dois|il faut|ne jamais|toujours|obligatoire|interdit)\b",
    re.IGNORECASE,
)
_CONSTRAINT_RE = re.compile(
    r"\b(only|at most|at least|max(?:imum)?|min(?:imum)?|limit(?:ed)? to|no more than|"
    r"within \d|budget|deadline|offline|without network|sans réseau|sans réseau|"
    r"contrainte|limite)\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(we decided|we chose|we agreed|decision|decided to use|let's use|"
    r"nous avons décidé|on a décidé|retenu)\b",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    # severity tokens are matched ANYWHERE in the line: a log line is
    # "2026-11-15 10:00:57 ERROR connection refused ..." — anchoring at the line
    # start missed it and the error was then dropped as ordinary output.
    r"\b(ERROR|FATAL|CRITICAL|FAILED|Traceback|Exception|panic:)\b|"
    r"\b(error|exception|traceback)\s*:",
    re.IGNORECASE,
)
# Severity markers that make a fragment non-droppable regardless of its kind.
_SEVERITY_RE = re.compile(r"\b(ERROR|FATAL|CRITICAL|FAILED|Traceback|Exception|panic:)\b")
# "We changed the cache to Redis instead of Memcached." — an explicit replacement
# the reused extractor misses, captured here so both values reach the view.
_DECISION_CHANGE_RE = re.compile(
    r"\b(?:changed|switched|migrated|moved|replaced)\b"
    r"(?P<middle>[^.;]{0,60}?)"
    r"\b(?:to|onto)\s+(?P<new>[A-Za-z][\w.\-]*)"
    r"[^.;]{0,80}?"
    r"\b(?:instead of|from|rather than|over)\s+(?P<old>[A-Za-z][\w.\-]*)",
    re.IGNORECASE,
)
# "The reporting service depends on PostgreSQL." — the reused relation extractor
# only knows "X uses/is Y", so a dependency between two entities never reached
# the structured view. Captured here, same policy as the decision supplement.
_RELATION_DEPENDENCY_RE = re.compile(
    r"\b(?P<subject>[A-Za-z][\w\-]*(?:\s+[a-z][\w\-]*){0,3})\s+"
    r"(?P<predicate>depends on|relies on|is built on|connects to)\s+"
    r"(?P<object>[A-Z][\w.\-]*)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?\s*$|\b(how|what|why|when|where|which|who|combien|comment)\b", re.IGNORECASE)
_TEMP_RE = re.compile(
    r"\b(today|tonight|right now|currently running|for now|temporarily|"
    r"aujourd'hui|pour l'instant|temporairement)\b",
    re.IGNORECASE,
)
_HISTORY_RE = re.compile(
    r"\b(previously|earlier|before|historically|used to|précédemment|auparavant)\b",
    re.IGNORECASE,
)
# Tokens that must survive a compression for the meaning to be intact.
_CRITICAL_TOKEN_RE = re.compile(
    r"\b(no|not|never|none|without|except|unless|ne|pas|jamais|aucun|sauf|"
    r"cannot|can't|must not|do not|don't)\b",
    re.IGNORECASE,
)

BUDGET_LEVELS: Dict[str, float] = {
    # fraction of max_tokens the *history* may occupy
    "minimal": 0.25,
    "balanced": 0.55,
    "complete": 0.90,
}

# How many of the newest turns stay verbatim when structured memory is in use.
# Everything older is represented by the reconstructed context block.
KEEP_RECENT_BY_LEVEL: Dict[str, int] = {
    "minimal": 2,
    "balanced": 4,
    "complete": 8,
}


def token_count_kind(model: str = "") -> str:
    """``"exact"`` only when a REAL tokenizer answers, else ``"estimated"``.

    Measured behaviour: the tiktoken/HF adapters exist but raise ImportError
    when the optional dependency is absent, and ``count_tokens`` then falls back
    to ``max(1, (len+3)//4)``. ``CharEstimateAdapter.encode`` does NOT raise — it
    is the estimate — so the adapter identity must be checked, not just whether
    ``encode`` succeeded. A character estimate must never be presented as an
    exact token saving (Phase 2.4 §7).
    """
    adapter = get_tokenizer(model)
    name = type(adapter).__name__
    if name not in ("TiktokenAdapter", "HuggingFaceTokenizerAdapter"):
        return "estimated"
    try:
        adapter.encode("probe")
    except Exception:
        return "estimated"
    return "exact"


# ── stage 1 — input analysis ────────────────────────────────────────────────

@dataclass(frozen=True)
class ContentClass:
    """Deterministic classification of one message."""
    kind: str                      # fact | instruction | constraint | decision |
                                   # question | answer | error | history | temporary
    category: str                  # memory.classify_entry output (fact/task/...)
    domain: str = "general"
    is_mandatory: bool = False     # never droppable
    is_error: bool = False
    has_negation: bool = False
    fmt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "category": self.category, "domain": self.domain,
            "is_mandatory": self.is_mandatory, "is_error": self.is_error,
            "has_negation": self.has_negation, "fmt": self.fmt,
        }


@dataclass(frozen=True)
class Turn:
    """One conversation turn with its stable identity."""
    index: int
    mid: str
    role: str
    content: str
    cls: ContentClass
    tokens: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index, "mid": self.mid, "role": self.role,
            "content": self.content, "tokens": self.tokens, "cls": self.cls.to_dict(),
        }


@dataclass(frozen=True)
class InputAnalysis:
    system_text: str
    developer_text: str
    turns: Tuple[Turn, ...]
    instruction_texts: Tuple[str, ...]     # mandatory fragments, chronological
    error_texts: Tuple[str, ...]

    @property
    def mandatory_texts(self) -> Tuple[str, ...]:
        out: List[str] = []
        if self.system_text:
            out.append(self.system_text)
        if self.developer_text:
            out.append(self.developer_text)
        out.extend(self.instruction_texts)
        return tuple(out)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_text": self.system_text,
            "developer_text": self.developer_text,
            "turns": [t.to_dict() for t in self.turns],
            "instruction_texts": list(self.instruction_texts),
            "error_texts": list(self.error_texts),
        }


def detect_format(text: str) -> Optional[str]:
    """Reuse the existing format detectors (no new heuristic)."""
    fmt = _auto_detect_format(text)
    if fmt:
        return fmt
    from .adaptive import _is_diff, _is_docker, _is_json, _is_markdown, _is_tree

    stripped = text.strip()
    if _is_json(stripped):
        return "json"
    if _is_diff(stripped):
        return "diff"
    if _is_docker(stripped):
        return "docker"
    if _is_tree(stripped):
        return "tree"
    if _is_markdown(stripped):
        return "markdown"
    if stripped.startswith(("Traceback (most recent call last)", "ERROR", "npm ERR!")):
        return "logs"
    return None


def _detect_domain(text: str) -> str:
    """Reuse ``_tool_domains.TOOL_DOMAIN_MAP`` (static keyword -> domain map).

    Iteration order is dict insertion order, so the result is deterministic.
    """
    lowered = text.lower()
    for keyword, domain in TOOL_DOMAIN_MAP.items():
        if keyword in lowered:
            return str(domain)
    return "general"


def classify_content(content: str, role: str = "user") -> ContentClass:
    """Classify a message. Ambiguity is never resolved by guessing.

    ``kind`` is the *pipeline* kind; ``category`` is the existing
    ``memory.classify_entry`` output (reused, not re-implemented).
    """
    text = content or ""
    kind = "fact"

    if _ERROR_RE.search(text):
        kind = "error"
    elif _DECISION_RE.search(text):
        kind = "decision"
    elif _CONSTRAINT_RE.search(text):
        kind = "constraint"
    elif _INSTRUCTION_RE.search(text):
        kind = "instruction"
    elif _HISTORY_RE.search(text):
        kind = "history"
    elif role == "assistant":
        kind = "answer"
    elif _TEMP_RE.search(text):
        kind = "temporary"
    elif _QUESTION_RE.search(text):
        kind = "question"

    mandatory = (
        role in MANDATORY_ROLES
        or kind in ("instruction", "constraint", "decision", "error")
        or _SEVERITY_RE.search(text) is not None
    )
    return ContentClass(
        kind=kind,
        category=classify_entry(text) if text.strip() else "fact",
        domain=_detect_domain(text),
        is_mandatory=mandatory,
        is_error=kind == "error",
        has_negation=bool(_CRITICAL_TOKEN_RE.search(text)),
        fmt=detect_format(text),
    )


def _message_id(index: int, message: Dict[str, Any]) -> str:
    """Stable per-message id: the native id when present, else ``mid_<index>``.

    The index is the message's position in the *input* list, so the same input
    always produces the same ids.
    """
    native = message.get("id")
    if isinstance(native, str) and native.strip():
        return native
    return f"mid_{index}"


def analyze_input(
    messages: Sequence[Dict[str, Any]],
    *,
    system_prompt: str = "",
    model: str = "",
) -> InputAnalysis:
    """Stage 1. Never drops a message; system/developer are separated out."""
    from .core.tokenizer import count_tokens

    system_parts: List[str] = []
    developer_parts: List[str] = []
    turns: List[Turn] = []
    instruction_texts: List[str] = []
    error_texts: List[str] = []

    if system_prompt.strip():
        system_parts.append(system_prompt.strip())

    for index, raw in enumerate(messages or []):
        msg = raw if isinstance(raw, dict) else {}
        role = str(msg.get("role", "") or "")
        content = msg.get("content", "")
        if not isinstance(content, str):
            # Structured (multi-part) content: keep it verbatim, no rewriting.
            content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        mid = _message_id(index, msg)

        if role == "system":
            if content.strip():
                system_parts.append(content.strip())
            continue
        if role in ("developer", "dev"):
            if content.strip():
                developer_parts.append(content.strip())
            continue

        cls = classify_content(content, role)
        if cls.kind in ("instruction", "constraint") and content.strip():
            instruction_texts.append(content.strip())
        if cls.is_error and content.strip():
            error_texts.append(content.strip())

        turns.append(Turn(
            index=index, mid=mid, role=role or "user", content=content,
            cls=cls, tokens=count_tokens(content, model),
        ))

    return InputAnalysis(
        system_text="\n\n".join(system_parts),
        developer_text="\n\n".join(developer_parts),
        turns=tuple(turns),
        instruction_texts=tuple(instruction_texts),
        error_texts=tuple(error_texts),
    )


# ── stage 4 — duplicate detection (reused agent) ────────────────────────────

def drop_duplicates(turns: Sequence[Turn]) -> Tuple[List[Turn], int]:
    """Reuse ``context.deduplication``; the current user turn is never removed."""
    if not turns:
        return [], 0
    payload = [
        {"role": t.role, "content": t.content, "_idx": t.index, "_turn": t}
        for t in turns
    ]
    kept_payload = deduplicate_messages(payload, strategy="exact")
    kept_indices = {p["_idx"] for p in kept_payload}
    # A mandatory turn is never dropped, even when it is an exact duplicate:
    # provenance/role matters (Phase 2.4 §5 Agent 4).
    for t in turns:
        if t.cls.is_mandatory or t.index == turns[-1].index:
            kept_indices.add(t.index)
    kept = [t for t in turns if t.index in kept_indices]
    return kept, len(turns) - len(kept)


# ── stage 5 — information selection ─────────────────────────────────────────

@dataclass(frozen=True)
class ScoredTurn:
    turn: Turn
    score: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"mid": self.turn.mid, "index": self.turn.index, "score": round(self.score, 6),
                "reason": self.reason, "kind": self.turn.cls.kind, "tokens": self.turn.tokens}


def score_turn(
    turn: Turn,
    *,
    query: str,
    entities: Sequence[str],
    decisions: Sequence[str],
    position_from_end: int,
    last_user_index: int,
) -> ScoredTurn:
    """Deterministic score. Not recency-only, not keywords-only.

    Weights (fixed, documented, no clock):
      mandatory        1.00  never dropped
      query overlap    0.30  reuses context.relevance.score_text_fragment
      entity match     0.25
      decision match   0.20
      role/answer      0.10
      position         0.15  newest turns first, linearly decaying
    """
    if turn.cls.is_mandatory or turn.index == last_user_index:
        return ScoredTurn(turn, 1.0, "mandatory")

    lowered = turn.content.lower()
    score = 0.0
    reasons: List[str] = []

    overlap = score_text_fragment(turn.content, query)
    if overlap > 0.1:
        score += 0.30 * overlap
        reasons.append("query")

    hit_entities = [e for e in entities if e and e.lower() in lowered]
    if hit_entities:
        score += 0.25
        reasons.append("entity:" + "|".join(sorted(hit_entities)))

    if any(d and d.lower() in lowered for d in decisions):
        score += 0.20
        reasons.append("decision")

    if turn.role == "assistant":
        score += 0.10
        reasons.append("answer")

    score += 0.15 * (1.0 / (1.0 + position_from_end))

    return ScoredTurn(turn, min(score, 0.999), "+".join(reasons) or "weak")


# ── stage 6 — memory retrieval (reused agents) ──────────────────────────────

@dataclass(frozen=True)
class MemoryView:
    """Structured memory retrieved for the current request."""
    facts: Tuple[str, ...] = ()
    decisions: Tuple[Tuple[str, str, str], ...] = ()      # (value, status, decision_id)
    relations: Tuple[Tuple[str, str, str], ...] = ()      # (subject, predicate, object)
    entities: Tuple[str, ...] = ()
    episodes: Tuple[Tuple[str, str], ...] = ()            # (episode_id, summary)
    sources: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    # facts/episodes in retriever order (the ranking the reconstruction uses)
    ranked: Tuple[str, ...] = ()
    # persistent constraints, one compact line each
    constraints: Tuple[str, ...] = ()
    # the original message text, kept so a reconstructed line can be checked
    # against what was actually said (partial words are repaired against it)
    source_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": list(self.facts),
            "decisions": [list(d) for d in self.decisions],
            "relations": [list(r) for r in self.relations],
            "entities": list(self.entities),
            "episodes": [list(e) for e in self.episodes],
            "sources": {k: list(v) for k, v in self.sources.items()},
            "ranked": list(self.ranked),
            "constraints": list(self.constraints),
        }


def build_memory(
    messages: Sequence[Dict[str, Any]],
    *,
    conversation_id: Optional[str] = None,
    model: str = "",
    query: str = "",
) -> Tuple[MemoryView, Dict[str, Any]]:
    """Build structured memory with the Phase-2.3 deterministic consolidator.

    Reuses ``consolidate_to_episodes`` (entities/relations/decisions/episodes)
    and ``memory.retrieval.hybrid_search`` for the query-restricted lookup.
    Nothing here reads the clock: the consolidator stamps from the sources.

    ``query`` is the question the context is being built FOR. Measured gap: the
    retriever used the last user message of the conversation instead, so asking
    different questions over one conversation produced the same ranking — which
    makes question-dependent reconstruction impossible.
    """
    if not messages:
        return MemoryView(), {"items": 0, "entities": 0, "relations": 0,
                              "decisions": 0, "episodes": 0, "retrieved": 0}

    # Content already preserved verbatim must not be re-listed as memory, or the
    # same tokens are paid for twice. Only the *bulky* preserved content is
    # skipped (system/developer text, log and tool dumps): a short mandatory turn
    # like "We decided to use PostgreSQL instead of SQLite" is exactly what the
    # structured view exists to distil, so it is still consolidated — measured,
    # skipping every mandatory turn emptied the "current decisions:" section.
    eligible = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        if role in ("system", "developer"):
            continue
        if len(content) > MANDATORY_MEMORY_MAX_CHARS and classify_content(content, role).is_mandatory:
            continue
        eligible.append(message)

    _recent, episodic, semantic = consolidate_to_episodes(
        eligible, keep_recent=0, conversation_id=conversation_id,
    )

    # ── deterministic ordering everywhere ────────────────────────────────
    facts = tuple(item.content for item in semantic.items)
    decisions = tuple(
        (d.value, d.status, d.id) for d in sorted(semantic.decisions, key=lambda d: (d.id, d.value))
    )
    # Deduplicated: measured, the consolidator stored the same relation twice
    # ("- Atlas uses Cassandra" appeared on two lines), which spends budget on a
    # repetition. Fixed order keeps the output deterministic.
    seen_relations: set = set()
    relation_rows: List[Tuple[str, str, str]] = []
    for relation in sorted(semantic.relations,
                           key=lambda r: (r.subject.lower(), r.predicate, r.object.lower())):
        key = (relation.subject.lower(), relation.predicate.lower(), relation.object.lower())
        if key in seen_relations:
            continue
        seen_relations.add(key)
        relation_rows.append((relation.subject, relation.predicate, relation.object))
    relations = tuple(relation_rows)
    entities = tuple(sorted(e.name for e in semantic.entities.values()))
    episodes = tuple(
        (ep.id, ep.summary) for ep in sorted(
            (e for e in episodic.episodes if isinstance(e, Episode)), key=lambda e: e.id,
        )
    )

    sources: Dict[str, Tuple[str, ...]] = {}
    for item in semantic.items:
        sources[item.content] = tuple(item.source_ids)
    for d in semantic.decisions:
        sources[f"decision:{d.id}"] = tuple(d.source_ids)

    # ── deterministic supplement: change-phrased decisions ───────────────
    # Measured gap: the reused extractor captures "We decided to use X instead
    # of Y" but not "We changed the cache to Redis instead of Memcached". The
    # current cache decision and the value it replaced therefore never reached
    # the structured view: "current decisions:" held a single entry, the history
    # view stayed empty, and "which cache was used before?" had no structured
    # answer even though the sentence was in the conversation.
    #
    # This scans the eligible turns for explicit change patterns and adds the
    # new value as active and the replaced value as superseded, each citing the
    # message it came from. Fixed regex, fixed order, ids from the durable
    # message id: deterministic. No memory module or public interface changes.
    seen_decisions = {value.lower() for value, _status, _did in decisions}
    extra_decisions: List[Tuple[str, str, str]] = []
    for message in eligible:
        content = str(message.get("content", ""))
        for match in _DECISION_CHANGE_RE.finditer(content):
            new_value = match.group("new").strip(" .,:")
            old_value = match.group("old").strip(" .,:")
            if not new_value or not old_value or new_value.lower() == old_value.lower():
                continue
            source_id = message_id_for(message, conversation_id=conversation_id or "")
            for value, status in ((new_value, "active"), (old_value, "superseded")):
                if value.lower() in seen_decisions:
                    continue
                seen_decisions.add(value.lower())
                decision_id = "pdec_" + hashlib.sha1(
                    f"{value}|{source_id}".encode("utf-8")).hexdigest()[:12]
                extra_decisions.append((value, status, decision_id))
                sources[f"decision:{decision_id}"] = (source_id,)
    if extra_decisions:
        # append, so the consolidator's own order is preserved unchanged
        decisions = decisions + tuple(sorted(
            extra_decisions, key=lambda d: (d[1] != "active", d[0].lower())))

    # ── deterministic supplement: dependency relations ────────────────────
    # Symmetric to the decision supplement: "X depends on Y" is a relation the
    # reused extractor does not model, so the reconstructed view could describe
    # the current value but never how two entities relate.
    extra_relations: List[Tuple[str, str, str]] = []
    for message in eligible:
        content = str(message.get("content", ""))
        for match in _RELATION_DEPENDENCY_RE.finditer(content):
            subject = match.group("subject").strip()
            predicate = match.group("predicate").strip().lower()
            obj = match.group("object").strip(" .,:")
            key = (subject.lower(), predicate, obj.lower())
            if not subject or not obj or key in seen_relations:
                continue
            seen_relations.add(key)
            extra_relations.append((subject, predicate, obj))
            sources[f"relation:{subject}:{obj}"] = (
                message_id_for(message, conversation_id=conversation_id or ""),)
    if extra_relations:
        relations = relations + tuple(sorted(extra_relations, key=lambda r: (r[0].lower(), r[2].lower())))

    # ── query-restricted retrieval, reusing the production retriever ─────
    # The caller's question wins; without one, fall back to the last user turn.
    effective_query = query or ""
    if not effective_query:
        for raw in reversed(list(messages)):
            if isinstance(raw, dict) and raw.get("role") == "user":
                effective_query = str(raw.get("content", "") or "")
                break
    query = effective_query

    retrieved = 0
    ranked: Tuple[str, ...] = ()
    manager = MemoryManager()
    for content in facts:
        entry = manager.add(content, source="pipeline")
        entry.created_at = UNKNOWN_TIME      # deterministic stamp (no clock)
        entry.updated_at = UNKNOWN_TIME
    for value, status, _did in decisions:
        entry = manager.add(f"Decision: {value} ({status})", category="decision", source="pipeline")
        entry.created_at = UNKNOWN_TIME
        entry.updated_at = UNKNOWN_TIME
    for subject, predicate, obj in relations:
        entry = manager.add(f"{subject} {predicate} {obj}", source="pipeline")
        entry.created_at = UNKNOWN_TIME
        entry.updated_at = UNKNOWN_TIME

    if query and manager.count:
        result = hybrid_search(query, manager, max_results=12)
        # keep the ranking: throwing it away left the block in consolidation
        # order, so the retriever never influenced the reconstruction
        ranked = tuple(entry.content for entry in result.entries)
        retrieved = len(ranked)

    # ── persistent constraints: one compact line each, deduplicated ──────
    constraints: List[str] = []
    for message in eligible:
        content = str(message.get("content", ""))
        if classify_content(content, str(message.get("role", "user"))).kind == "constraint":
            line = _one_line(content)
            if line and line not in constraints:
                constraints.append(line)

    stats = {
        "items": len(facts), "entities": len(entities), "relations": len(relations),
        "decisions": len(decisions), "episodes": len(episodes), "retrieved": retrieved,
        "ranked": len(ranked), "constraints": len(constraints),
    }
    return MemoryView(
        facts=facts, decisions=decisions, relations=relations,
        entities=entities, episodes=episodes, sources=sources,
        ranked=ranked, constraints=tuple(constraints),
        source_text="\n".join(str(m.get("content", "")) for m in messages),
    ), stats


# ── stage 7 — context reconstruction ────────────────────────────────────────

CONTEXT_BLOCK_HEADER = "[BOSTER CONTEXT]"
CONTEXT_BLOCK_FOOTER = "[/BOSTER CONTEXT]"


def _one_line(text: str, limit: int = 200) -> str:
    """Collapse a fact to one deterministic line (multi-line facts waste tokens).

    Truncation happens on a word boundary: cutting mid-word produced garbage
    fragments ("return pro") that no source could support.
    """
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut + "…"


def reconstruct_context(
    memory: MemoryView,
    *,
    query: str,
    level: str = "balanced",
    reconstruct: bool = True,
) -> str:
    """Build a compact, typed context block, then repair its cut boundaries.

    The block is deterministic: fixed section order, sorted entries, provenance
    shown. Current state is separated from history: a ``superseded`` decision
    appears only when it is asked for or at ``complete`` level.

    Memory items are stored truncated (``content[:200]``) by the memory layer, so
    a fact can end mid-word; the same source-grounded repair used for compressed
    fragments is applied to the finished block.
    """
    block, _stats = build_context_block(
        memory, query=query, level=level, reconstruct=reconstruct, max_tokens=0, model="",
    )
    return _repair_partial_words(block, memory.source_text or block)


def detect_unknown_subjects(query: str, source_text: str) -> Tuple[str, ...]:
    """Proper names the query asks about that the conversation never mentions.

    Heuristic, and reported as such: a capitalised token (3+ chars, not a
    sentence-initial interrogative/auxiliary) that appears nowhere in the source
    vocabulary. It exists so the context can say "this is not known" instead of
    leaving the model to guess — the brief asks for impossible questions to be
    identified ("Quelle information est encore inconnue ?").
    """
    vocabulary = {token.lower() for token in _WORD_TOKEN_RE.findall(source_text or "")}
    found: List[str] = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9_]{2,})\b", query or ""):
        token = match.group(1)
        if token in _QUERY_STOP_WORDS or token.lower() in vocabulary:
            continue
        if token not in found:
            found.append(token)
    return tuple(found)


_QUERY_STOP_WORDS = frozenset({
    "Which", "What", "Where", "When", "Why", "Who", "How", "Whom", "Whose",
    "Is", "Are", "Was", "Were", "Do", "Does", "Did", "Can", "Could", "Should",
    "Would", "Will", "Shall", "May", "Might", "Must", "The", "A", "An", "In",
    "On", "At", "For", "If", "Otherwise", "Also", "Note", "And", "But", "Or",
    "Please", "Tell", "Give", "Show", "List", "Summarise", "Summarize",
})


def build_context_block(
    memory: MemoryView,
    *,
    query: str,
    level: str = "balanced",
    reconstruct: bool = True,
    max_tokens: int = 0,
    model: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Build the context block under a token allowance. Returns ``(block, stats)``.

    Without an allowance the block grows with the conversation — measured: a
    200-exchange conversation produced a 25 459-character block, larger than the
    entire budget, so the reconstruction candidate was rejected as *bigger than
    the input* and the memory never engaged at all. With an allowance the sections
    are emitted in order of value and the entries that do not fit are **counted
    and reported** (''omitted''), never dropped silently.
    """
    allowance = max_tokens if max_tokens and max_tokens > 0 else 0
    omitted: Dict[str, int] = {}

    def _fits(lines: List[str]) -> bool:
        if not allowance:
            return True
        return count_tokens("\n".join(lines), model) <= allowance

    if not reconstruct:
        # Level D: a plain fact list, no current/history split, ranked by retrieval.
        lines = [CONTEXT_BLOCK_HEADER]
        for fact in _ranked_facts(memory):
            candidate = lines + [f"- {_one_line(fact)}"]
            if not _fits(candidate):
                omitted["facts"] = omitted.get("facts", 0) + 1
                continue
            lines.append(f"- {_one_line(fact)}")
        for value, status, _did in memory.decisions:
            lines.append(f"- decision: {value} ({status})")
        lines.append(CONTEXT_BLOCK_FOOTER)
        return "\n".join(lines), {"reconstruct": False, "omitted": omitted,
                                  "allowance_tokens": allowance}

    lowered_query = (query or "").lower()
    # "Which database did Orion use *before*?" must reach the history view:
    # measured, the first vocabulary only knew history/previously/why/change, so a
    # "before" question silently returned the current-only block.
    wants_history = level == "complete" or bool(
        re.search(r"\b(history|historique|previously|previously|before|earlier|prior|"
                  r"originally|used to|auparavant|avant|précédemment|"
                  r"why|pourquoi|change|changed|changement)\b",
                  lowered_query, re.IGNORECASE)
    )

    current = [(v, st, i) for v, st, i in memory.decisions if st == "active"]
    history = [(v, st, i) for v, st, i in memory.decisions if st != "active"]
    unknown = detect_unknown_subjects(query, memory.source_text)

    lines: List[str] = [CONTEXT_BLOCK_HEADER]

    # 1. what is not known: cheapest entry and the one that prevents invention
    if unknown:
        lines.append("unknown:")
        for token in unknown:
            lines.append(f"- no information about {token} in this conversation")

    # 2. current state: the highest-value, smallest section. Always complete.
    if current:
        lines.append("current decisions:")
        for value, status, did in current:
            srcs = memory.sources.get(f"decision:{did}", ())
            lines.append(f"- {value} ({status}) [{'|'.join(srcs)}]" if srcs else f"- {value} ({status})")

    # 3. persistent constraints: one compact line each, kept ahead of the facts
    if memory.constraints:
        lines.append("constraints:")
        for constraint in memory.constraints:
            lines.append(f"- {_one_line(constraint)}")

    # 4. history only when the question asks for it
    if history and wants_history:
        lines.append("history:")
        for value, status, _did in history:
            lines.append(f"- {value} ({status}) -> replaced")

    # 5. relations, then 6. facts: ranked by retrieval, truncated by the allowance
    if memory.relations:
        lines.append("relations:")
        for subject, predicate, obj in memory.relations:
            candidate = lines + [f"- {subject} {predicate} {obj}"]
            if not _fits(candidate):
                omitted["relations"] = omitted.get("relations", 0) + 1
                continue
            lines.append(f"- {subject} {predicate} {obj}")

    facts = _ranked_facts(memory)
    if facts:
        lines.append("facts:")
        for fact in facts:
            srcs = memory.sources.get(fact, ())
            line = f"- {_one_line(fact)}" + (f" [{'|'.join(srcs)}]" if srcs else "")
            if not _fits(lines + [line]):
                omitted["facts"] = omitted.get("facts", 0) + 1
                continue
            lines.append(line)

    # 7. episodes last: the most expensive, the least specific
    if memory.episodes and level != "minimal":
        lines.append("episodes:")
        budget_for_episodes = 3
        for episode_id, summary in memory.episodes:
            if budget_for_episodes <= 0:
                omitted["episodes"] = omitted.get("episodes", 0) + 1
                continue
            line = f"- {_one_line(summary)} [{episode_id}]"
            if not _fits(lines + [line]):
                omitted["episodes"] = omitted.get("episodes", 0) + 1
                continue
            lines.append(line)
            budget_for_episodes -= 1

    lines.append(CONTEXT_BLOCK_FOOTER)
    return "\n".join(lines), {
        "reconstruct": True,
        "allowance_tokens": allowance,
        "unknown_subjects": list(unknown),
        "current_decisions": len(current),
        "history_decisions": len(history),
        "history_shown": bool(history and wants_history),
        "constraints": len(memory.constraints),
        "facts": len(facts),
        "omitted": omitted,
    }


def _ranked_facts(memory: MemoryView) -> List[str]:
    """Facts in retrieval order first, then a stable deterministic order.

    Measured: ``hybrid_search`` was called and its ranking thrown away (only its
    length was kept), so the block listed facts in consolidation order and the
    retriever never influenced the reconstruction.
    """
    rank = {content: index for index, content in enumerate(memory.ranked)}
    absent = len(rank)
    return sorted(memory.facts, key=lambda fact: (rank.get(fact, absent), fact))


# ── stage 8 — compression of long fragments (reused compressors) ────────────

_COMPRESSORS: Dict[str, Callable[[str], str]] = {
    "json": compress_json,
    "code": compress_code,
    "markdown": compress_markdown,
    "diff": compress_diff,
    "git-log": compress_git_log,
    "gitlog": compress_git_log,
    "csv": compress_csv,
    "sql": compress_sql,
}


_WORD_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:\-]{1,}|[0-9]+(?:\.[0-9]+)?")
_LEAD_WORD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]{1,})")
_TAIL_WORD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{1,})\s*[.…]?\s*$")

# Lines the pipeline writes itself: they are structure, not quoted content, so a
# source-grounded repair must never touch them ("current decisions:" is not a
# word from the conversation — measured: the repair deleted "current").
STRUCTURAL_LINES = (
    CONTEXT_BLOCK_HEADER, CONTEXT_BLOCK_FOOTER, "[DEVELOPER]", "[/DEVELOPER]",
    "unknown:", "current decisions:", "constraints:", "history:", "relations:",
    "facts:", "episodes:", "reasons:",
)
# Lines the reconstructor writes itself (absence notices, omission counters).
# They carry no source words, so the source-grounded repair must leave them alone.
_GENERATED_LINE_RE = re.compile(
    r"^-\s*(?:no information about\b|\(omitted\b|omitted[: ])", re.IGNORECASE)


def _repair_partial_words(text: str, source: str, *, protect: Sequence[str] = STRUCTURAL_LINES) -> str:
    """Drop leading/trailing partial words left at a compressor's cut boundary.

    Measured: ``safe_compress`` caps mid-token (``return process(`` → ``return
    pro``), ``compress_logs`` joins its head and tail mid-word
    (``...processed`` → ``d batch 48``), and the memory layer stores items
    truncated (``...INFO proces``). Those fragments support nothing — they are
    halves of a word that exists nowhere in the source, while a *complete* word
    always exists in the source. Only the two edges of each line are inspected,
    so nothing inside a line is ever touched, and the check is source-grounded
    rather than a guess at where the cut landed.

    The vocabulary keeps both the raw token and its punctuation-stripped form:
    the token pattern includes ``.`` and ``:`` (for ``3.14``, ``postgres:5432``),
    so a sentence-final ``node.`` would otherwise not vouch for the word
    ``node`` — measured, that deleted the last word of every fact line
    ("...the analytical node." became "...the analytical .").

    The legacy modules are left untouched: the repair happens in this stage.
    """
    vocabulary = set()
    for match in _WORD_TOKEN_RE.finditer(source):
        token = match.group(0).lower()
        vocabulary.add(token)
        vocabulary.add(token.rstrip(".:-"))
    if not vocabulary:
        return text
    protected = {p.strip() for p in protect}
    repaired: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in protected or any(stripped.startswith(p) for p in protected):
            repaired.append(line)
            continue
        # Lines the reconstructor generates itself are not source material, so a
        # source-grounded check cannot judge their words: measured, the absence
        # notice lost "conversation" and read "- no information about Vega in
        # this". They are kept verbatim.
        if _GENERATED_LINE_RE.match(stripped):
            repaired.append(line)
            continue
        lead = _LEAD_WORD_RE.match(line)
        if lead and lead.group(1).lower() not in vocabulary:
            line = line[: lead.start(1)] + line[lead.end(1):]
        tail = _TAIL_WORD_RE.search(line)
        if tail and tail.group(1).lower() not in vocabulary:
            line = line[: tail.start(1)] + line[tail.end(1):]
        repaired.append(line)
    return "\n".join(repaired)


def _flat(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _keeps_all(text: str, lines: Sequence[str]) -> bool:
    """Every given line must still be present (whitespace-insensitive)."""
    flat = _flat(text)
    return all(_flat(line) in flat for line in lines if _flat(line))


def _capped(text: str, limit: int) -> str:
    """``safe_compress`` with a character cap, cut boundaries repaired."""
    return _repair_partial_words(safe_compress(text, max_chars=limit), text)


def compress_fragment(text: str, cls: ContentClass, *, level: str = "balanced") -> Tuple[str, str]:
    """Compress one fragment. Returns ``(text, method)``.

    Mandatory content (instructions, constraints, decisions, errors) is only ever
    passed through ``safe_compress``: ANSI stripping, blank collapsing and
    head/tail capping, never a structural rewrite. A fragment carrying a severity
    marker is compressed *around* the error with the log agent, and the result is
    only kept if every severity line is proven still present; otherwise the whole
    fragment is preserved. Non-mandatory tool output goes through the
    format-specific compressor, then is re-checked against the source by
    ``SafetyCheck``; on any integrity loss the capped ``safe_compress`` result is
    used instead.
    """
    if not text or len(text) < 200:
        return text, "untouched"

    severity_lines = [line for line in text.splitlines() if _SEVERITY_RE.search(line)]

    if severity_lines:
        # Never drop an error to reach a budget. Compress the noise around it,
        # then prove the error survived; a silent loss keeps the whole fragment.
        candidate = compress_logs(text, keep_errors=True)
        if len(candidate) < len(text) and _keeps_all(candidate, severity_lines):
            compressed, method = candidate, "logs(severity-preserving)"
        else:
            # verbatim: an error line is never edited, not even a half word
            return safe_compress(text, max_chars=None), "safe_compress(severity-full)"
    elif cls.is_mandatory or cls.kind in ("instruction", "constraint", "decision", "error"):
        # An instruction is never truncated, at any level: a budget that cannot
        # hold it is reported by stages 9/10 instead of being paid for with lost
        # content (measured: capping at "minimal" dropped 12 mandatory rules).
        return safe_compress(text, max_chars=None), "safe_compress"
    else:
        fmt = cls.fmt
        compressor = _COMPRESSORS.get(fmt or "")
        if compressor is None:
            if fmt == "logs":
                compressed = compress_logs(text, keep_errors=True)
                method = "logs"
            elif fmt in ("docker", "tree", "terminal"):
                compressed = compress_terminal(text, level=2)
                method = str(fmt)
            else:
                compressed, _report = adaptive_compress(text, max_chars=1500)
                method = "adaptive"
        else:
            compressed = compressor(text)
            method = str(fmt)

        if len(compressed) > len(text):
            # a "compressor" that grows its input is not a saving
            compressed, method = _capped(text, 1500), "safe_compress(growth-guard)"
        else:
            verdict = SafetyCheck.check(text, compressed)
            if verdict.get("warnings") or not verdict.get("integrity", True):
                compressed, method = _capped(text, 1500), "safe_compress(integrity-guard)"

    # A compressor's cut boundary can leave a half word at the edge of a line;
    # the source decides whether it is real (see ``_repair_partial_words``).
    repaired = _repair_partial_words(compressed, text)
    if repaired != compressed:
        method = method + "+word-repair"
    return repaired, method


# ── stage 10 — integrity validation (stateless, reused SafetyCheck) ─────────

@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    instructions_preserved: int
    instructions_total: int
    negations_preserved: int
    negations_total: int
    decisions_preserved: int
    decisions_total: int
    errors_preserved: int
    errors_total: int
    problems: Tuple[str, ...]
    safety: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "instructions_preserved": self.instructions_preserved,
            "instructions_total": self.instructions_total,
            "negations_preserved": self.negations_preserved,
            "negations_total": self.negations_total,
            "decisions_preserved": self.decisions_preserved,
            "decisions_total": self.decisions_total,
            "errors_preserved": self.errors_preserved,
            "errors_total": self.errors_total,
            "problems": list(self.problems),
            "safety": self.safety,
        }


def _has(text: str, fragment: str) -> bool:
    return fragment.strip() != "" and fragment.strip() in text


def validate_output(
    *,
    analysis: InputAnalysis,
    final_text: str,
    decision_values: Sequence[str],
    within_budget: bool = True,
    tokens_after: int = 0,
    max_tokens: int = 0,
) -> ValidationReport:
    """Stage 10. Stateless: the verdict depends only on the arguments.

    Deliberately does NOT reuse ``intelligence.integrity`` (measured
    non-idempotent: the same input gave ``integrity_ok=False`` then ``True``).
    It reuses the deterministic ``SafetyCheck`` / ``PreservationPolicy`` for the
    pattern-level diff and adds explicit mandatory-content checks.
    """
    problems: List[str] = []

    instructions = analysis.mandatory_texts
    kept_i = sum(1 for t in instructions if _has(final_text, t))
    if kept_i != len(instructions):
        problems.append(f"instructions: {len(instructions) - kept_i} missing")

    # negations: every negation token of a mandatory fragment must survive
    neg_total = 0
    neg_kept = 0
    for text in instructions:
        for token in _CRITICAL_TOKEN_RE.findall(text):
            neg_total += 1
            if re.search(rf"\b{re.escape(token)}\b", final_text, re.IGNORECASE):
                neg_kept += 1
    if neg_kept != neg_total:
        problems.append(f"negations: {neg_total - neg_kept} lost")

    decisions_kept = sum(1 for v in decision_values if v and v.lower() in final_text.lower())
    # a decision currently in force is mandatory; superseded ones are history
    if decision_values and decisions_kept == 0:
        problems.append("decisions: none represented")

    errors = analysis.error_texts
    # An error message is judged on its SEVERITY LINES, not on its whole body:
    # measured, a 120-line log carrying one ERROR line was compressed (noise
    # removed, error kept) and the whole-message comparison then declared the
    # error "missing" — which blocked every compression of any log. What must
    # never be lost is the error itself; the INFO lines around it are noise.
    errors_kept = 0
    for error_text in errors:
        severity_lines = [line for line in error_text.splitlines() if _SEVERITY_RE.search(line)]
        if severity_lines:
            if all(_has(final_text, line) for line in severity_lines):
                errors_kept += 1
        elif _has(final_text, error_text):
            errors_kept += 1
    if errors_kept != len(errors):
        problems.append(f"errors: {len(errors) - errors_kept} missing")

    policy = PreservationPolicy()
    flagged = policy.check(final_text) if final_text else []
    safety: Dict[str, Any] = {"policy_flagged": flagged}
    if instructions and flagged:
        # flags are advisory only for the mandatory fragments we already checked
        safety["policy_flagged_but_mandatory_ok"] = kept_i == len(instructions)

    if not within_budget:
        problems.append(f"budget: {tokens_after} > {max_tokens} tokens")

    return ValidationReport(
        ok=not problems,
        instructions_preserved=kept_i, instructions_total=len(instructions),
        negations_preserved=neg_kept, negations_total=neg_total,
        decisions_preserved=decisions_kept, decisions_total=len(decision_values),
        errors_preserved=errors_kept, errors_total=len(errors),
        problems=tuple(problems), safety=safety,
    )


# ── orchestration ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PipelineConfig:
    """Frozen config: the pipeline's determinism contract."""
    max_tokens: int = 20_000
    level: str = "balanced"                 # minimal | balanced | complete
    model: str = ""
    conversation_id: Optional[str] = None
    use_memory: bool = True
    reconstruct: bool = True
    compress_outputs: bool = True
    dedupe: bool = True
    validate: bool = True

    def __post_init__(self) -> None:
        if self.level not in BUDGET_LEVELS:
            raise ValueError(f"level must be one of {sorted(BUDGET_LEVELS)}")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_tokens": self.max_tokens, "level": self.level, "model": self.model,
            "conversation_id": self.conversation_id, "use_memory": self.use_memory,
            "reconstruct": self.reconstruct, "compress_outputs": self.compress_outputs,
            "dedupe": self.dedupe, "validate": self.validate,
        }


@dataclass(frozen=True)
class StageReport:
    name: str
    detail: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.name, **self.detail}


@dataclass
class PipelineOutput:
    """Everything the caller needs — and the trace of every transformation."""
    system_prompt: str
    messages: List[Dict[str, Any]]
    config: PipelineConfig
    analysis: InputAnalysis
    memory: MemoryView
    selected: List[ScoredTurn]
    dropped: List[Dict[str, Any]]
    stages: List[StageReport]
    validation: Optional[ValidationReport]
    tokens_before: int
    tokens_after: int
    count_kind: str

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def savings_pct(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return round(100.0 * self.tokens_saved / self.tokens_before, 2)

    def train_of_thought(self) -> List[str]:
        return [f"{s.name}: {json.dumps(s.detail, sort_keys=True, ensure_ascii=False)}" for s in self.stages]

    def to_dict(self) -> Dict[str, Any]:
        """Canonical, deterministic serialisation (sorted keys, no clock)."""
        return {
            "config": self.config.to_dict(),
            "count_kind": self.count_kind,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "savings_pct": self.savings_pct,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "validation": self.validation.to_dict() if self.validation else None,
            "stages": [s.to_dict() for s in self.stages],
            "selected": [s.to_dict() for s in self.selected],
            "dropped": self.dropped,
            "memory": self.memory.to_dict(),
            "analysis": self.analysis.to_dict(),
        }

    def report(self) -> str:
        lines = ["=" * 60, "  DETERMINISTIC PIPELINE", "=" * 60]
        lines.append(f"  tokens  : {self.tokens_before} -> {self.tokens_after} "
                     f"({self.savings_pct}% saved, {self.count_kind})")
        lines.append(f"  level   : {self.config.level}  budget={self.config.max_tokens}")
        lines.append(f"  messages: {len(self.messages)} kept")
        for stage in self.stages:
            lines.append(f"    - {stage.name}: {json.dumps(stage.detail, sort_keys=True, ensure_ascii=False)}")
        if self.validation:
            v = self.validation
            lines.append(f"  integrity: {'OK' if v.ok else 'FAILED ' + '; '.join(v.problems)}")
        lines.append("=" * 60)
        return "\n".join(lines)


def run_deterministic_pipeline(
    messages: Sequence[Dict[str, Any]],
    *,
    system_prompt: str = "",
    query: str = "",
    config: Optional[PipelineConfig] = None,
) -> PipelineOutput:
    """Run the 8-stage deterministic pipeline.

    No LLM, no network, no wall clock, no randomness. Identical arguments yield
    a byte-identical ``to_dict()``.
    """
    from .core.tokenizer import count_tokens

    cfg = config or PipelineConfig()
    stages: List[StageReport] = []
    kind = token_count_kind(cfg.model)

    # ── stage 1: input analysis ──────────────────────────────────────────
    analysis = analyze_input(messages, system_prompt=system_prompt, model=cfg.model)
    original_messages = [{"role": t.role, "content": t.content} for t in analysis.turns]
    tokens_before = estimate_messages_tokens(
        [{"role": "system", "content": analysis.system_text}] + original_messages if analysis.system_text
        else original_messages,
        cfg.model,
    )
    stages.append(StageReport("1_input_analyzer", {
        "messages_in": len(messages or []),
        "turns": len(analysis.turns),
        "system_chars": len(analysis.system_text),
        "developer_chars": len(analysis.developer_text),
        "instruction_fragments": len(analysis.instruction_texts),
        "errors": len(analysis.error_texts),
        "kinds": {k: sum(1 for t in analysis.turns if t.cls.kind == k)
                  for k in sorted({t.cls.kind for t in analysis.turns})},
        "tokens_before_%s" % kind: tokens_before,
    }))

    # ── stage 2/3: format + classification are per-turn (already in cls) ──
    formats = {t.cls.fmt for t in analysis.turns if t.cls.fmt}
    stages.append(StageReport("2_format_detector", {
        "formats": {f: sum(1 for t in analysis.turns if t.cls.fmt == f) for f in sorted(formats)},
        "classified": len(analysis.turns),
        "mandatory": sum(1 for t in analysis.turns if t.cls.is_mandatory),
    }))

    # ── stage 4: duplicate detection ─────────────────────────────────────
    turns = list(analysis.turns)
    duplicates = 0
    if cfg.dedupe:
        turns, duplicates = drop_duplicates(turns)
    stages.append(StageReport("4_duplicate_detector", {
        "candidates": len(analysis.turns), "exact_duplicates_removed": duplicates,
        "kept": len(turns),
        # per-stage token ledger (Phase 2.5 §4 asks for the cost after each step)
        "tokens_after_duplicates_estimated": estimate_messages_tokens(
            [{"role": t.role, "content": t.content} for t in turns], cfg.model,
        ),
    }))

    # ── stage 6: memory (before selection: it supplies entities/decisions) ─
    memory = MemoryView()
    memory_stats: Dict[str, Any] = {"enabled": False}
    if cfg.use_memory:
        memory, memory_stats = build_memory(
            original_messages or [], conversation_id=cfg.conversation_id, model=cfg.model,
            query=query or "",
        )
        memory_stats["enabled"] = True
    stages.append(StageReport("6_memory_retriever", memory_stats))

    entity_names = [e for e in memory.entities]
    decision_values = [v for v, _s, _i in memory.decisions]

    # ── stage 5: selection + stage 9 budget ──────────────────────────────
    last_user_index = -1
    for t in turns:
        if t.role == "user":
            last_user_index = t.index

    scored: List[ScoredTurn] = []
    for t in turns:
        position_from_end = len(turns) - 1 - turns.index(t)
        scored.append(score_turn(
            t, query=query or "", entities=entity_names, decisions=decision_values,
            position_from_end=position_from_end, last_user_index=last_user_index,
        ))
    # deterministic order: mandatory first (chronological), then score desc, then index
    mandatory = [s for s in scored if s.reason == "mandatory"]
    others = sorted(
        (s for s in scored if s.reason != "mandatory"),
        key=lambda s: (-s.score, s.turn.index),
    )

    history_share = BUDGET_LEVELS[cfg.level]
    history_budget = int(cfg.max_tokens * history_share)
    mandatory_tokens = sum(s.turn.tokens for s in mandatory)
    selected: List[ScoredTurn] = list(mandatory)
    used = mandatory_tokens
    dropped: List[Dict[str, Any]] = []
    for cand in others:
        # the current user request is already mandatory; never drop a mandatory one
        if used + cand.turn.tokens <= history_budget:
            selected.append(cand)
            used += cand.turn.tokens
        else:
            dropped.append({"mid": cand.turn.mid, "score": round(cand.score, 6),
                            "tokens": cand.turn.tokens, "reason": "budget"})

    selected_sorted = sorted(selected, key=lambda s: s.turn.index)   # chronological
    stages.append(StageReport("5_information_selector", {
        "scored": len(scored),
        "mandatory": len(mandatory),
        "selected": len(selected_sorted),
        "dropped": len(dropped),
        "history_budget_%s" % kind: history_budget,
        "history_budget_fraction": history_share,
        "selected_tokens_%s" % kind: used,
        "selection": [s.to_dict() for s in selected_sorted],
    }))
    stages.append(StageReport("9_token_budget_manager", {
        "level": cfg.level, "max_tokens_%s" % kind: cfg.max_tokens,
        "history_share": history_share, "mandatory_tokens_%s" % kind: mandatory_tokens,
        "count_kind": kind,
        "note": ("exact tokenizer" if kind == "exact"
                 else "no tokenizer installed: counts are ESTIMATES (chars/4)"),
    }))

    # ── stage 8: compression of the selected fragments ───────────────────
    compressed_turns: List[Dict[str, Any]] = []
    methods: Dict[str, int] = {}
    for s in selected_sorted:
        text, method = (s.turn.content, "untouched")
        if cfg.compress_outputs:
            text, method = compress_fragment(s.turn.content, s.turn.cls, level=cfg.level)
        methods[method] = methods.get(method, 0) + 1
        compressed_turns.append({
            "role": s.turn.role, "content": text, "mid": s.turn.mid,
            "tokens": count_tokens(text, cfg.model),
        })

    # ── stage 7: reconstruction + final assembly ─────────────────────────
    # The block gets its own allowance (the history share of the budget) so it
    # cannot outgrow the request; entries that do not fit are reported.
    memory_allowance = int(cfg.max_tokens * BUDGET_LEVELS[cfg.level])
    context_block = ""
    block_stats: Dict[str, Any] = {}
    if cfg.use_memory and (memory.facts or memory.decisions or memory.episodes or memory.relations):
        context_block, block_stats = build_context_block(
            memory, query=query or "", level=cfg.level, reconstruct=cfg.reconstruct,
            max_tokens=memory_allowance, model=cfg.model,
        )
        context_block = _repair_partial_words(context_block, memory.source_text or context_block)

    # The system prompt is the STABLE prefix (KV-cache friendliness, Phase 2.4
    # §10): instructions first, then the volatile context block. The block is
    # never injected as a fake "user" message.
    system_sections: List[str] = []
    if analysis.system_text:
        system_sections.append(analysis.system_text)
    if analysis.developer_text:
        system_sections.append(f"[DEVELOPER]\n{analysis.developer_text}\n[/DEVELOPER]")
    if context_block:
        system_sections.append(context_block)
    final_system = "\n\n".join(system_sections)

    # The context block SUBSTITUTES for the older turns: keeping both the block
    # and the messages it summarises would count the same tokens twice. Only the
    # mandatory fragments and the newest turns stay verbatim.
    if cfg.use_memory:
        keep_recent = KEEP_RECENT_BY_LEVEL[cfg.level]
        verbatim_mids = {s.turn.mid for s in selected_sorted[-keep_recent:]} if keep_recent else set()
        verbatim_mids |= {s.turn.mid for s in selected_sorted if s.reason == "mandatory"}
    else:
        verbatim_mids = {m["mid"] for m in compressed_turns}

    final_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in compressed_turns if m["mid"] in verbatim_mids
    ]
    represented = [m["mid"] for m in compressed_turns if m["mid"] not in verbatim_mids]

    def _count(system_text: str, msgs: List[Dict[str, Any]]) -> int:
        head = [{"role": "system", "content": system_text}] if system_text else []
        return estimate_messages_tokens(head + msgs, cfg.model)

    core_sections = [s for s in system_sections if s != context_block]
    core_system = "\n\n".join(core_sections)
    all_verbatim = [{"role": m["role"], "content": m["content"]} for m in compressed_turns]

    # Deterministic ladder: among the candidates that LOSE NOTHING (each one is
    # validated), take the smallest, and never return something bigger than the
    # input. On a short conversation the reconstructed block costs more than it
    # saves, so the pipeline falls back to selection-only, then to the untouched
    # context. A budget that cannot be met is reported, not paid for with lost
    # content: measured, a "minimal" level used to truncate 12 mandatory rules.
    candidates = (
        (final_system, final_messages, _count(final_system, final_messages), "reconstructed"),
        (core_system, all_verbatim, _count(core_system, all_verbatim), "selection+compression"),
        (core_system, [dict(m) for m in original_messages], tokens_before, "original"),
    )
    active_decisions = [v for v, status, _i in memory.decisions if status == "active"] or decision_values

    def _lossless(system_text: str, msgs: List[Dict[str, Any]], count: int) -> ValidationReport:
        return validate_output(
            analysis=analysis,
            final_text=system_text + "\n" + "\n".join(str(m["content"]) for m in msgs),
            decision_values=active_decisions,
            within_budget=count <= cfg.max_tokens,
            tokens_after=count, max_tokens=cfg.max_tokens,
        )

    chosen: Optional[Tuple[str, List[Dict[str, Any]], int, str, ValidationReport]] = None
    rejected: List[Dict[str, Any]] = []
    for candidate_system, candidate_messages, candidate_tokens, candidate_variant in candidates:
        if candidate_tokens > tokens_before:
            rejected.append({"variant": candidate_variant, "tokens": candidate_tokens,
                             "why": "bigger than the input"})
            continue
        report = _lossless(candidate_system, candidate_messages, candidate_tokens)
        # the budget may be exceeded (and is reported); losing content may not
        lossy = [problem for problem in report.problems if "budget" not in problem]
        if lossy:
            rejected.append({"variant": candidate_variant, "tokens": candidate_tokens,
                             "why": "; ".join(lossy)})
            continue
        if chosen is None or candidate_tokens < chosen[2]:
            chosen = (candidate_system, candidate_messages, candidate_tokens,
                      candidate_variant, report)

    if chosen is None:
        # nothing survived validation: the untouched context is the honest answer
        msgs = [dict(m) for m in original_messages]
        chosen = (core_system, msgs, tokens_before, "original",
                  _lossless(core_system, msgs, tokens_before))

    final_system, final_messages, tokens_after, variant, validation = chosen
    if variant != "reconstructed":
        represented = []
    fallback = "" if variant != "original" else "no gain on this input: original context returned unchanged"

    # A hard cap must be honoured; report it when it cannot be.
    over_budget = tokens_after > cfg.max_tokens

    stages.append(StageReport("7_context_reconstructor", {
        "context_block_chars": len(context_block),
        "context_block_tokens_estimated": count_tokens(context_block, cfg.model) if context_block else 0,
        "memory_allowance_tokens": memory_allowance,
        "block": block_stats,
        "reconstructed": bool(cfg.reconstruct),
        "variant": variant,
        "fallback": fallback,
        "rejected": rejected,
        "system_sections": len(system_sections),
        "final_messages": len(final_messages),
        "represented_in_memory": len(represented),
        "roles": {r: sum(1 for m in final_messages if m["role"] == r)
                  for r in sorted({m["role"] for m in final_messages})},
        "system_role_messages": 0,      # the block is part of the system prompt
        "tokens_after_%s" % kind: tokens_after,
        "within_budget": not over_budget,
    }))
    stages.append(StageReport("8_context_compressor", {
        "methods": {m: methods[m] for m in sorted(methods)},
        "compressed_fragments": len([m for m in methods if not m.startswith("untouched")]),
        "tokens_after_compression_estimated": estimate_messages_tokens(
            [{"role": m["role"], "content": m["content"]} for m in compressed_turns], cfg.model,
        ),
    }))

    # ── stage 10: validation ─────────────────────────────────────────────
    # The report was already produced while choosing the candidate: the context
    # that is returned is the one that was validated.
    if not cfg.validate:
        validation = None
    if validation is not None:
        stages.append(StageReport("10_integrity_validator", validation.to_dict()))

    return PipelineOutput(
        system_prompt=final_system,
        messages=final_messages,
        config=cfg,
        analysis=analysis,
        memory=memory,
        selected=selected_sorted,
        dropped=dropped,
        stages=stages,
        validation=validation,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        count_kind=kind,
    )
