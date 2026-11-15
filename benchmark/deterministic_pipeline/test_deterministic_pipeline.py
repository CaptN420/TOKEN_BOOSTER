"""Phase 2.4 invariants for the deterministic pipeline.

Every test asserts a property the brief makes non-negotiable. All of them run
real code on the frozen fixtures — nothing here is asserted from reading source.

Run:
    PYTHONPATH="." uv run pytest benchmark/deterministic_pipeline/ -q
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from boster import deterministic_pipeline as dp
from boster.deterministic_pipeline import (
    PipelineConfig,
    analyze_input,
    classify_content,
    drop_duplicates,
    run_deterministic_pipeline,
    token_count_kind,
)

from .fixtures import (
    ALL_FIXTURES,
    CODE_HEAVY,
    CONTRADICTORY,
    LOGS_AND_ERRORS,
    LONG_CONVERSATION,
    SHORT_CONVERSATION,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

LEVELS = ("minimal", "balanced", "complete")


def _run(gt, **overrides):
    config = PipelineConfig(
        max_tokens=overrides.pop("max_tokens", 20_000),
        level=overrides.pop("level", "balanced"),
        model="",
        conversation_id=gt.conversation_id,
        **overrides,
    )
    return run_deterministic_pipeline(
        gt.conversation, system_prompt=gt.system_prompt, query=gt.query, config=config,
    )


def _full_text(out) -> str:
    return out.system_prompt + "\n" + "\n".join(m["content"] for m in out.messages)


# ── determinism ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
def test_byte_identical_across_runs(gt):
    first = json.dumps(_run(gt).to_dict(), sort_keys=True, ensure_ascii=False)
    second = json.dumps(_run(gt).to_dict(), sort_keys=True, ensure_ascii=False)
    assert first == second


def test_byte_identical_across_hash_seeds_and_timezone():
    """Different PYTHONHASHSEED / TZ must not change a single byte."""
    script = textwrap.dedent(
        """
        import hashlib, json
        from boster.deterministic_pipeline import PipelineConfig, run_deterministic_pipeline
        from benchmark.deterministic_pipeline.fixtures import ALL_FIXTURES

        dumps = {}
        for gt in ALL_FIXTURES:
            out = run_deterministic_pipeline(
                gt.conversation, system_prompt=gt.system_prompt, query=gt.query,
                config=PipelineConfig(max_tokens=20000, level="balanced",
                                      conversation_id=gt.conversation_id),
            )
            dumps[gt.name] = out.to_dict()
        blob = json.dumps(dumps, sort_keys=True, ensure_ascii=False)
        print(hashlib.sha256(blob.encode()).hexdigest())
        """
    )
    digests = []
    for seed, tz in (("0", "UTC"), ("1", "UTC"), ("12345", "UTC"), ("0", "Etc/GMT-9")):
        env = {**os.environ, "PYTHONHASHSEED": seed, "TZ": tz, "PYTHONPATH": str(REPO_ROOT)}
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              env=env, cwd=str(REPO_ROOT), timeout=300)
        assert proc.returncode == 0, proc.stderr[-2000:]
        digests.append(proc.stdout.strip())
    assert len(set(digests)) == 1, f"non-deterministic digests: {digests}"


def test_pipeline_reads_no_clock_or_randomness():
    """A deterministic transformer may not consult a clock or a PRNG."""
    source = Path(dp.__file__).read_text(encoding="utf-8")
    forbidden = re.findall(r"^\s*(?:import|from)\s+(time|random|datetime|secrets)\b", source, re.M)
    assert forbidden == [], f"clock/random imports found: {forbidden}"


# ── mandatory content is never sacrificed ──────────────────────────────────

@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
@pytest.mark.parametrize("level", LEVELS)
def test_mandatory_fragments_survive_every_level(gt, level):
    out = _run(gt, level=level)
    text = _full_text(out)
    missing = [f for f in gt.mandatory_fragments if f not in text]
    assert missing == [], f"lost mandatory content at {level}: {missing}"


@pytest.mark.parametrize("gt", [LONG_CONVERSATION, LOGS_AND_ERRORS], ids=lambda g: g.name)
@pytest.mark.parametrize("level", LEVELS)
def test_errors_are_never_dropped(gt, level):
    out = _run(gt, level=level, max_tokens=400)
    text = _full_text(out)
    assert gt.errors, "fixture must declare errors"
    for error in gt.errors:
        assert error in text


def test_system_and_developer_messages_are_never_silently_deleted():
    messages = [
        {"role": "system", "content": "SYSTEM RULE: never drop this."},
        {"role": "developer", "content": "DEVELOPER RULE: also never drop this."},
        {"role": "user", "content": "irrelevant chatter " * 200},
        {"role": "user", "content": "last question?"},
    ]
    out = run_deterministic_pipeline(
        messages, system_prompt="PROMPT RULE: stay here.",
        config=PipelineConfig(max_tokens=200, level="minimal", conversation_id="conv-sd"),
    )
    text = _full_text(out)
    assert "SYSTEM RULE" in text
    assert "DEVELOPER RULE" in text
    assert "PROMPT RULE" in text
    kinds = {m["role"] for m in out.messages}
    assert "system" not in kinds and "developer" not in kinds, \
        "preserved system/developer text must not become a fake user message"


# ── nothing is invented ───────────────────────────────────────────────────

@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
@pytest.mark.parametrize("use_memory,reconstruct", [(False, False), (True, False), (True, True)])
def test_no_unsupported_claim(gt, use_memory, reconstruct):
    from .compare_variants import unsupported_claim_check

    out = _run(gt, use_memory=use_memory, reconstruct=reconstruct)
    sources = [gt.system_prompt] + [str(m.get("content", "")) for m in gt.conversation]
    unsupported, checked = unsupported_claim_check(_full_text(out), sources)
    assert unsupported == 0, f"{unsupported}/{checked} lines contain a value absent from the source"


@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
def test_no_message_role_is_invented(gt):
    out = _run(gt)
    allowed = {str(m.get("role", "user")) for m in gt.conversation}
    produced = {m["role"] for m in out.messages}
    assert produced <= allowed, f"invented role(s): {produced - allowed}"


# ── budget and growth ─────────────────────────────────────────────────────

@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
@pytest.mark.parametrize("level", LEVELS)
def test_never_returns_more_than_it_received(gt, level):
    """The ladder guarantees the output is never bigger than the input."""
    out = _run(gt, level=level)
    assert out.tokens_after <= out.tokens_before, \
        f"{gt.name}/{level}: {out.tokens_before} -> {out.tokens_after} (inflation)"


@pytest.mark.parametrize("gt", ALL_FIXTURES, ids=lambda g: g.name)
def test_budget_is_measured_and_reported(gt):
    out = _run(gt, max_tokens=300, level="minimal")
    budget = next(s.detail for s in out.stages if s.name == "9_token_budget_manager")
    recon = next(s.detail for s in out.stages if s.name == "7_context_reconstructor")
    assert budget["max_tokens_estimated"] == 300
    assert budget["history_share"] == 0.25
    assert "within_budget" in recon
    if not recon["within_budget"]:
        # an impossible budget must be reported, never hidden
        assert out.validation is not None and any("budget" in p for p in out.validation.problems)


def test_levels_are_ordered_by_how_much_they_keep():
    """complete keeps at least as much as balanced, which keeps at least as much as minimal."""
    tokens = {level: _run(LONG_CONVERSATION, level=level, use_memory=True,
                          reconstruct=True).tokens_after for level in LEVELS}
    assert tokens["minimal"] <= tokens["balanced"] <= tokens["complete"], tokens


# ── selection and duplicates ──────────────────────────────────────────────

def test_repetitive_turns_are_deduplicated():
    repeated = {"role": "user", "content": "same status line, verbatim" * 3}
    messages = [dict(repeated) for _ in range(30)] + [{"role": "user", "content": "done?"}]
    analysis = analyze_input(messages)
    kept, removed = drop_duplicates(analysis.turns)
    assert removed >= 25, f"only {removed} duplicates removed"
    assert len({t.content for t in kept}) == len(kept)


def test_selection_keeps_the_questions_and_the_recent_turns():
    out = _run(gt=LONG_CONVERSATION)
    reasons = {s.reason for s in out.selected}
    assert "mandatory" in reasons
    assert any(r.startswith("query") for r in reasons), f"no query-aware selection: {sorted(reasons)}"


def test_no_message_is_silently_lost_without_memory():
    """Without structured memory nothing may be represented 'elsewhere'."""
    out = _run(CODE_HEAVY, use_memory=False, reconstruct=False)
    accounted = {s.turn.mid for s in out.selected}
    for entry in out.dropped:
        assert entry["mid"] not in accounted
        assert entry["reason"], "a dropped turn must carry a reason"
    stage = next(s.detail for s in out.stages if s.name == "5_information_selector")
    assert stage["selected"] + stage["dropped"] == stage["scored"]


# ── memory and reconstruction ─────────────────────────────────────────────

def test_superseded_decision_is_history_not_current():
    """Asserted on the block itself: on a 6-turn input the ladder may legitimately
    pick a cheaper variant, but the block is what must separate current from
    history, and that split is what is verified here."""
    memory, _stats = dp.build_memory(CONTRADICTORY.conversation,
                                     conversation_id=CONTRADICTORY.conversation_id)
    block = dp.reconstruct_context(memory, query=CONTRADICTORY.query,
                                   level="balanced", reconstruct=True)
    assert "current decisions:" in block
    current = block.split("current decisions:", 1)[1]
    for stop in ("history:", "facts:", "relations:", "episodes:", "[/BOSTER CONTEXT]"):
        current = current.split(stop, 1)[0]
    assert "Redis" in current
    assert "Memcached" not in current, "a superseded decision must not read as current"


def test_reconstruction_marks_provenance_and_query_awareness():
    memory, stats = dp.build_memory(LONG_CONVERSATION.conversation,
                                    conversation_id=LONG_CONVERSATION.conversation_id)
    assert stats["items"] > 0, "fixture must produce memory items"
    block = dp.reconstruct_context(memory, query=LONG_CONVERSATION.query,
                                   level="balanced", reconstruct=True)
    assert "[BOSTER CONTEXT]" in block and "[/BOSTER CONTEXT]" in block
    assert re.search(r"\[msg_[0-9a-f]{6,}\]", block), "entries must carry provenance"


def test_memory_does_not_duplicate_mandatory_content():
    """A mandatory turn kept verbatim must not also be re-listed as memory."""
    out = _run(LOGS_AND_ERRORS, use_memory=True, reconstruct=True)
    log_line = LOGS_AND_ERRORS.errors[0]
    assert log_line in _full_text(out)
    memory, _stats = dp.build_memory(LOGS_AND_ERRORS.conversation,
                                     conversation_id=LOGS_AND_ERRORS.conversation_id)
    block = dp.reconstruct_context(memory, query=LOGS_AND_ERRORS.query,
                                   level="balanced", reconstruct=True)
    assert log_line not in block, "already-verbatim content must not be duplicated in the block"


def test_reconstruct_is_idempotent_on_its_own_output():
    """Feeding the optimised conversation back must not lose mandatory content."""
    first = _run(LONG_CONVERSATION)
    second = run_deterministic_pipeline(
        [{"role": "system", "content": first.system_prompt}]
        + [{"role": m["role"], "content": m["content"]} for m in first.messages],
        system_prompt=LONG_CONVERSATION.system_prompt, query=LONG_CONVERSATION.query,
        config=PipelineConfig(max_tokens=20_000, level="balanced",
                              conversation_id=LONG_CONVERSATION.conversation_id),
    )
    for fragment in LONG_CONVERSATION.mandatory_fragments:
        assert fragment in _full_text(second), f"lost on the second pass: {fragment}"


# ── classification and validation ────────────────────────────────────────

def test_error_line_in_the_middle_of_a_log_is_detected():
    """Measured regression: '2026-11-15 10:00:57 ERROR ...' was not classified."""
    content = "\n".join(["2026-11-15 10:00:00 INFO batch ok"] * 30
                        + ["2026-11-15 10:00:57 ERROR connection refused"]
                        + ["2026-11-15 10:01:00 INFO batch ok"] * 30)
    cls = classify_content(content, "user")
    assert cls.is_mandatory, "a fragment carrying an ERROR marker is mandatory"


def test_validation_is_stateless_and_reproducible():
    out_a = _run(LONG_CONVERSATION)
    out_b = _run(CONTRADICTORY)
    assert out_a.validation is not None and out_b.validation is not None
    same_input = _run(LONG_CONVERSATION).validation.to_dict()
    assert out_a.validation.to_dict() == same_input


def test_validation_flags_a_lost_instruction():
    """The validator must actually be able to fail (not a rubber stamp)."""
    analysis = analyze_input(
        [{"role": "system", "content": "SYSTEM RULE: keep me."},
         {"role": "user", "content": "hello?"}],
    )
    report = dp.validate_output(
        analysis=analysis, final_text="nothing of the sort",
        decision_values=[], within_budget=True, tokens_after=1, max_tokens=10,
    )
    assert not report.ok
    assert any("system" in p or "instruction" in p for p in report.problems)


def test_token_count_is_labelled_and_never_overstated():
    kind = token_count_kind("")
    assert kind in ("exact", "estimated")
    if kind == "estimated":
        # the char estimate must not be presented as a measured token count
        out = _run(SHORT_CONVERSATION)
        assert out.count_kind == "estimated"


def test_score_and_selection_are_defensive_on_empty_input():
    out = run_deterministic_pipeline([], system_prompt="only a prompt",
                                     config=PipelineConfig(max_tokens=1000))
    assert out.messages == []
    assert out.tokens_after <= out.tokens_before


def test_partial_word_repair_is_source_grounded():
    """A half word at a cut boundary is removed; a complete word is kept."""
    source = "return process(request, worker=7)"
    assert "pro" not in dp._repair_partial_words("    return pro", source)
    assert dp._repair_partial_words("    return process", source).strip() == "return process"
