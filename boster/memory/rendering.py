"""Zone-aware, memoised rendering of the context block (Phase KV CACHE 2).

The KV-cache audit (``KV_CACHE_AUDIT_REPORT.md``) measured that the block was
reusable from one turn to the next at 26 % in the steady regime — and at 0,4–4,5 %
as soon as the question changed, because the retriever's ranking reordered the
fact list *inside the prefix*. This module gives the block a stable structure:

* three zones, always emitted in the same order — **stable**, **historical**,
  **dynamic** (query-conditioned last, so per-turn volatility leaves the prefix);
* a per-section **SHA-256 fingerprint** over the exact rendered lines;
* a **content-addressed memo**: a section whose fingerprint is unchanged keeps the
  previous text object, byte for byte.

Why content-addressing is safe (and why it cannot inflate the reusable prefix
artificially): the reuse decision is taken on the hash of the *exact lines* being
rendered. A section is only ever reused for content that produced the same hash,
so the memo can never substitute one section's text for another's. Reuse is a
property of the content, not of the caller.

Nothing here is imported by the memory engine's own modules: this is a rendering
layer on top of the structures the pipeline already computes.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "ZONE_STABLE", "ZONE_HISTORICAL", "ZONE_DYNAMIC", "ZONE_ORDER",
    "Section", "SectionRender", "BlockMemo", "memo_for", "reset_memos",
]

ZONE_STABLE = "stable"
ZONE_HISTORICAL = "historical"
ZONE_DYNAMIC = "dynamic"

#: Emission order. Anything query-conditioned belongs to the last zone: a section
#: that changes earlier in the string invalidates every token after it.
ZONE_ORDER: Tuple[str, ...] = (ZONE_STABLE, ZONE_HISTORICAL, ZONE_DYNAMIC)

_FIELD_SEP = "\x1f"     # unit separator: cannot occur in rendered conversation text
_LINE_SEP = "\n"


def _fingerprint(name: str, zone: str, lines: Sequence[str]) -> str:
    """SHA-256 over the section's identity and its exact rendered lines."""
    payload = _FIELD_SEP.join([zone, name, *lines])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Section:
    """One named group of rendered lines, belonging to exactly one zone."""

    name: str
    zone: str
    lines: Tuple[str, ...]

    def __post_init__(self) -> None:
        if self.zone not in ZONE_ORDER:
            raise ValueError(f"zone must be one of {ZONE_ORDER}, got {self.zone!r}")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.name, self.zone, self.lines)

    @property
    def text(self) -> str:
        return _LINE_SEP.join(self.lines)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "zone": self.zone, "lines": len(self.lines),
                "fingerprint": self.fingerprint[:12]}


@dataclass
class SectionRender:
    """The outcome of rendering one section through the memo."""

    name: str
    zone: str
    fingerprint: str
    text: str
    reused: bool
    previous_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        # ``reused`` is deliberately absent: it depends on what was rendered before,
        # so reporting it in the output would break the pipeline's
        # byte-identical-across-runs contract. It stays readable as an attribute
        # (tests) and through ``BlockMemo.stats()`` (observability).
        return {"name": self.name, "zone": self.zone,
                "fingerprint": self.fingerprint[:12]}


class BlockMemo:
    """Per-conversation memo of rendered sections.

    Thread-safe: several requests may render for the same conversation at once.
    """

    def __init__(self, conversation_id: str = "") -> None:
        self.conversation_id = conversation_id
        self._lock = threading.RLock()
        self._renders: Dict[str, SectionRender] = {}
        self._flags: Dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def stable_flag(self, item: str, sources: "Sequence[str]") -> str:
        """The citation flag to print for ``item``, fixed at its first rendering.

        A deduplicated fact is re-observed every turn, and the memory layer then
        reports its LATEST source. Printing that source directly rewrote the line —
        and every byte after it — on every turn: measured on a 100-turn
        conversation, the per-turn reusable prefix was 32 % with the live source and
        86 % with a source fixed once. The first source seen is the fact's origin,
        which is also the more useful citation, and nothing is lost: the flag still
        names a real message, and the current source set stays in the memory layer.
        """
        with self._lock:
            flag = self._flags.get(item)
            if flag is None:
                flag = "|".join(sources)
                self._flags[item] = flag
            return flag

    def render(self, section: Section) -> SectionRender:
        """Render ``section``, reusing the previous text when its fingerprint matches."""
        fingerprint = section.fingerprint
        with self._lock:
            previous = self._renders.get(section.name)
            if previous is not None and previous.fingerprint == fingerprint:
                self.hits += 1
                render = SectionRender(name=section.name, zone=section.zone,
                                       fingerprint=fingerprint, text=previous.text,
                                       reused=True, previous_fingerprint=fingerprint)
            else:
                self.misses += 1
                render = SectionRender(
                    name=section.name, zone=section.zone, fingerprint=fingerprint,
                    text=section.text, reused=False,
                    previous_fingerprint=None if previous is None else previous.fingerprint)
            self._renders[section.name] = render
            return render

    def snapshot(self) -> Dict[str, str]:
        """Current section name → fingerprint, for a turn-to-turn diff."""
        with self._lock:
            return {name: render.fingerprint for name, render in self._renders.items()}

    @staticmethod
    def diff(previous: Dict[str, str], current: Dict[str, str]) -> Dict[str, List[str]]:
        """Classify sections between two snapshots."""
        added = sorted(name for name in current if name not in previous)
        removed = sorted(name for name in previous if name not in current)
        changed = sorted(name for name in current
                         if name in previous and previous[name] != current[name])
        unchanged = sorted(name for name in current
                           if name in previous and previous[name] == current[name])
        return {"added": added, "changed": changed, "removed": removed, "unchanged": unchanged}

    def stats(self) -> Dict[str, object]:
        with self._lock:
            return {"conversation_id": self.conversation_id,
                    "sections": len(self._renders),
                    "sections_reused": sum(1 for r in self._renders.values() if r.reused),
                    "sections_rewritten": sum(1 for r in self._renders.values() if not r.reused),
                    "render_hits": self.hits, "render_misses": self.misses,
                    "fingerprints": {name: r.fingerprint[:12]
                                     for name, r in sorted(self._renders.items())}}

    def reset(self) -> None:
        with self._lock:
            self._renders.clear()
            self.hits = 0
            self.misses = 0


# ── registry: one memo per conversation, bounded ────────────────────────────

_REGISTRY_LIMIT = 64
_registry: "OrderedDict[str, BlockMemo]" = OrderedDict()
_registry_lock = threading.RLock()


def memo_for(conversation_id: str = "") -> BlockMemo:
    """The memo for a conversation, created on demand.

    Bounded LRU so a long-lived process cannot accumulate memos for forgotten
    conversations. Keying is only about bounding memory: reuse itself is decided by
    content fingerprint, so a wrong or missing key can never cause wrong reuse.
    """
    key = conversation_id or ""
    with _registry_lock:
        memo = _registry.get(key)
        if memo is None:
            memo = BlockMemo(key)
            _registry[key] = memo
        _registry.move_to_end(key)
        while len(_registry) > _REGISTRY_LIMIT:
            _registry.popitem(last=False)
        return memo


def reset_memos() -> None:
    """Drop every memo (tests, and any caller that wants a cold start)."""
    with _registry_lock:
        _registry.clear()


def render_zones(sections: Iterable[Section], memo: BlockMemo
                 ) -> Tuple[Dict[str, List[str]], List[SectionRender]]:
    """Render sections, grouped by zone, in ``ZONE_ORDER``.

    Returns ``(lines_by_zone, renders)``. Sections given out of zone order are
    grouped safely: the zone decides the position, not the caller's ordering.
    """
    by_zone: Dict[str, List[str]] = {zone: [] for zone in ZONE_ORDER}
    renders: List[SectionRender] = []
    for section in sections:
        render = memo.render(section)
        renders.append(render)
        if section.lines:
            by_zone[section.zone].extend(section.lines)
    return by_zone, renders
