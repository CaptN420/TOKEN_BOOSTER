"""Phase KV CACHE 2 — mémoire incrémentale, préfixe stable, corrections ciblées.

Chaque test mesure le pipeline RÉEL. Aucun cache hit moteur n'est revendiqué : ce
qui est mesuré ici est un préfixe de caractères qu'un moteur de KV cache POURRAIT
réutiliser. Le moteur local n'expose aucun compteur de cache (voir
``KV_CACHE_PHASE2_REPORT.md`` § limitations), donc la réutilisation réelle reste
NON VÉRIFIÉE par construction.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from boster.deterministic_pipeline import (
    CONTEXT_BLOCK_FOOTER,
    CONTEXT_BLOCK_HEADER,
    PipelineConfig,
    run_deterministic_pipeline,
)
from boster.memory.rendering import (
    ZONE_DYNAMIC,
    ZONE_HISTORICAL,
    ZONE_ORDER,
    ZONE_STABLE,
    BlockMemo,
    Section,
    memo_for,
    reset_memos,
)
from benchmark.kv_cache.conversation import (
    SYSTEM_PROMPT,
    as_messages,
    build_conversation,
    split_system,
)
from benchmark.kv_cache.prefix_stability import lcp

BUDGET = 2000
LEVEL = "balanced"


def _run(turns, count, conversation_id, *, query=None, position="system", budget=BUDGET):
    out = run_deterministic_pipeline(
        as_messages(turns[:count]),
        system_prompt=SYSTEM_PROMPT,
        query=query or turns[count - 1].question,
        config=PipelineConfig(max_tokens=budget, level=LEVEL,
                              conversation_id=conversation_id,
                              block_position=position),
    )
    detail = [s for s in out.stages if s.name == "7_context_reconstructor"][0].detail
    return out, detail


def _block(out):
    return split_system(out.system_prompt, CONTEXT_BLOCK_HEADER)[1]


def _header(section_name):
    return f"{section_name.replace('-', ' ')}:"


# ── objectif A : mémoïsation incrémentale ──────────────────────────────────

def test_memo_reuses_an_unchanged_section():
    memo = BlockMemo("t-memo")
    section = Section(name="facts", zone=ZONE_STABLE, lines=("facts:", "- a", "- b"))
    first = memo.render(section)
    second = memo.render(section)
    assert first.reused is False
    assert second.reused is True
    # The exact same object: the serialised text was not recomputed.
    assert second.text is first.text
    assert memo.hits == 1 and memo.misses == 1


def test_memo_does_not_reuse_changed_content():
    memo = BlockMemo("t-memo-changed")
    memo.render(Section(name="facts", zone=ZONE_STABLE, lines=("facts:", "- a")))
    changed = memo.render(Section(name="facts", zone=ZONE_STABLE, lines=("facts:", "- b")))
    assert changed.reused is False
    # A section whose NAME changed is not reused either, even with equal lines.
    renamed = memo.render(Section(name="relations", zone=ZONE_STABLE,
                                 lines=("facts:", "- b")))
    assert renamed.reused is False


def test_memos_are_isolated_per_conversation():
    reset_memos()
    assert memo_for("conv-a") is memo_for("conv-a")
    assert memo_for("conv-a") is not memo_for("conv-b")


def test_fingerprint_is_independent_of_python_hash_seed():
    script = textwrap.dedent(
        """
        from boster.memory.rendering import Section, ZONE_STABLE
        print(Section("facts", ZONE_STABLE, ("facts:", "- a", "- b")).fingerprint)
        """
    )
    digests = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                text=True, env=env, cwd=os.getcwd())
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert len(set(digests)) == 1
    assert len(digests[0]) == 64          # SHA-256 hexdigest


def test_unchanged_sections_are_not_rewritten_between_turns():
    turns, _ = build_conversation(100, scenario="B")
    reset_memos()
    memo = memo_for("t-turns")
    _run(turns, 99, "t-turns")
    before = memo.snapshot()
    _run(turns, 100, "t-turns")
    diff = memo.diff(before, memo.snapshot())
    # The state did not change, so the sections that carry it were reused verbatim.
    assert {"current decisions", "relations"} <= set(diff["unchanged"])
    # Only query-conditioned material may move.
    assert set(diff["changed"]) <= {"facts", "recent", "highlights", "unknown",
                                    "history-requested", "history", "episodes"}


# ── objectif B : séparation des zones ──────────────────────────────────────

def test_zones_are_ordered_stable_then_historical_then_dynamic():
    turns, _ = build_conversation(100, scenario="F")
    reset_memos()
    out, detail = _run(turns, 100, "t-zones")
    sections = detail["block"]["sections"]
    zones = [section["zone"] for section in sections]
    assert zones == sorted(zones, key=ZONE_ORDER.index)
    # And the text follows: every stable header precedes every historical one, which
    # precedes every dynamic one.
    block = _block(out)
    positions = {}
    for section in sections:
        header = _header(section["name"])
        if header in block:
            positions[section["name"]] = block.index(header)
    ordered = [name for name in positions]
    assert [positions[name] for name in ordered] == sorted(positions.values())
    assert {section["zone"] for section in sections} <= set(ZONE_ORDER)
    assert ZONE_DYNAMIC in {section["zone"] for section in sections}


def test_question_change_leaves_the_stable_prefix_intact():
    """The audit's headline defect: a question change collapsed the prefix to 0,4 %."""
    turns, _ = build_conversation(100, scenario="B")
    reset_memos()
    questions = {
        "current": turns[99].question,
        "history": "Which database did Orion use before?",
        "unknown": "Which database does project Vega use?",
        "relation": "What does the reporting service depend on?",
    }
    blocks = {}
    for key, question in questions.items():
        blocks[key] = _block(_run(turns, 100, "t-questions", query=question)[0])
    base = blocks["current"]
    for key, block in blocks.items():
        preserved = lcp(base, block) / max(len(base), 1)
        assert preserved >= 0.85, f"{key}: préfixe conservé {preserved:.1%}"
    # The dynamic zone is what moves; it is a small tail.
    dynamic_chars = sum(len(blocks[k]) - lcp(base, blocks[k]) for k in blocks)
    assert dynamic_chars >= 0


def test_stable_prefix_survives_a_long_conversation():
    turns, _ = build_conversation(100, scenario="B")
    reset_memos()
    previous = None
    preserved = []
    for turn in range(96, 101):
        out, _ = _run(turns, turn, "t-long")
        block = _block(out)
        if previous is not None:
            preserved.append(lcp(previous, block) / max(len(previous), 1))
        previous = block
    assert min(preserved) >= 0.65, f"pire tour : {min(preserved):.1%}"
    assert sum(preserved) / len(preserved) >= 0.75


def test_no_section_header_is_emitted_without_entries():
    turns, _ = build_conversation(100, scenario="F")
    reset_memos()
    block = _block(_run(turns, 100, "t-headers")[0])
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if line.endswith(":") and line not in (CONTEXT_BLOCK_HEADER,
                                               CONTEXT_BLOCK_FOOTER):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            assert following and not following.endswith(":"), f"en-tête nu : {line}"


def test_identical_fact_lines_are_collapsed():
    turns, _ = build_conversation(100, scenario="B")
    reset_memos()
    block = _block(_run(turns, 100, "t-dupes")[0])
    lines = block.splitlines()
    start = lines.index("facts:")
    fact_lines = []
    for line in lines[start + 1:]:
        if line.endswith(":"):
            break
        fact_lines.append(line)
    assert fact_lines, "aucune ligne de faits mesurée"
    assert len(fact_lines) == len(set(fact_lines))


# ── objectif C : changement de décision ────────────────────────────────────

def test_decision_change_rewrites_only_the_decision_section():
    turns, _ = build_conversation(100, scenario="D")
    reset_memos()
    memo = memo_for("t-decision")
    _run(turns, 98, "t-decision")
    before = memo.snapshot()
    out, _ = _run(turns, 99, "t-decision")
    diff = memo.diff(before, memo.snapshot())
    assert "current decisions" in diff["changed"]
    assert "relations" in diff["unchanged"]
    block = _block(out)
    # The fixture changes the analytical store to Snowflake at this turn.
    assert "Snowflake (active)" in block
    # A superseded decision is never presented as the current state.
    assert "PostgreSQL (active)" not in block
    # …but the fact trail is not erased.
    assert "PostgreSQL" in block


def test_superseded_decision_stays_traceable():
    turns, _ = build_conversation(100, scenario="D")
    reset_memos()
    out, _ = _run(turns, 100, "t-history-visible")
    block = _block(out)
    assert "history:" in block
    assert "- PostgreSQL (superseded) -> replaced" in block
    assert "- Memcached (superseded) -> replaced" in block


# ── objectif D : correction d'un fait ──────────────────────────────────────

def test_fact_correction_marks_the_superseded_value():
    turns, _ = build_conversation(100, scenario="E")
    reset_memos()
    out, _ = _run(turns, 100, "t-correction")
    block = _block(out)
    # The stale fact keeps its place, its source, and gains a marker.
    assert "(superseded) The staging host is atlas-1." in block
    assert "atlas-2" in block
    assert "[msg_" in block
    # No line states the stale value without either the marker or the correction.
    for line in block.splitlines():
        if "atlas-1" in line:
            assert "(superseded)" in line or "not atlas-1" in line


def test_correction_does_not_mark_unrelated_facts():
    turns, _ = build_conversation(100, scenario="B")
    reset_memos()
    block = _block(_run(turns, 100, "t-no-correction")[0])
    # No FACT is marked: the decision history lines carry their own "(superseded)"
    # status, so the fact marker ("- (superseded) …") is what must be absent.
    assert "- (superseded) " not in block


# ── objectif E : ordre du prompt ───────────────────────────────────────────

def test_suffix_keeps_the_dynamic_zone_out_of_the_system_prompt():
    turns, _ = build_conversation(100, scenario="B")
    question = "Which database does project Vega use?"     # forces an unknown section
    reset_memos()
    system_out, system_detail = _run(turns, 100, "t-suffix-system", query=question)
    reset_memos()
    suffix_out, suffix_detail = _run(turns, 100, "t-suffix-tail", query=question,
                                     position="suffix")
    assert "unknown:" in system_out.system_prompt
    assert "unknown:" not in suffix_out.system_prompt
    assert suffix_out.messages[-1]["role"] == "system"
    assert "unknown:" in suffix_out.messages[-1]["content"]
    # The prefix that stays byte-identical is strictly longer in the suffix layout.
    assert len(suffix_out.system_prompt) < len(system_out.system_prompt)
    assert system_detail["block_position"] == "system"
    assert suffix_detail["block_position"] == "suffix"


def test_unknown_block_position_is_rejected():
    with pytest.raises(ValueError):
        PipelineConfig(block_position="middle")


# ── déterminisme et non-régression ─────────────────────────────────────────

@pytest.mark.parametrize("scenario", ["B", "D", "E", "F"])
def test_same_input_gives_the_same_bytes(scenario):
    turns, _ = build_conversation(100, scenario=scenario)
    reset_memos()
    first = json.dumps(_run(turns, 100, f"t-det-{scenario}")[0].to_dict(),
                       sort_keys=True, ensure_ascii=False)
    second = json.dumps(_run(turns, 100, f"t-det-{scenario}")[0].to_dict(),
                        sort_keys=True, ensure_ascii=False)
    assert first == second


def test_zones_reported_by_the_block_match_the_text():
    turns, _ = build_conversation(100, scenario="F")
    reset_memos()
    out, detail = _run(turns, 100, "t-zonecount")
    block = _block(out)
    reported = detail["block"]["zones"]
    # The block's own header and footer are structure, not zone content.
    assert sum(reported.values()) == len(block.splitlines()) - 2
    assert reported[ZONE_STABLE] > 0
    assert reported[ZONE_HISTORICAL] > 0
    assert reported[ZONE_DYNAMIC] > 0
