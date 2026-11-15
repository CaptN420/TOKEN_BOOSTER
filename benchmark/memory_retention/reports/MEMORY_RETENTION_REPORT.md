# MEMORY RETENTION REPORT

**Date:** 2026-11-15  
**Repository:** https://github.com/CaptN420/TOKEN_BOOSTER  
**Tokenizer:** cl100k_base (tiktoken) fallback to CharEstimateAdapter  
**Hardware:** Windows 11, Python 3.14  

---

## Executive Summary

Boster achieves **extreme token reduction** (80–98%) but at the cost of **severe information loss** for relationships, decisions, and contradictions. The compression engine prioritizes recency over semantic relevance, causing early conversation facts to be dropped disproportionately.

---

## Results by Metric

### Fact Retention

| Size | Exchanges | Original Tokens | Selected Tokens | Reduction | Fact Retention |
|------|-----------|----------------|-----------------|-----------|---------------|
| short | 10 | 177 | 36 | 79.7% | **83%** (5/6) |
| medium | 21 | 267 | 32 | 88.0% | **33%** (4/12) |
| long | 54 | 799 | 34 | 95.7% | **22%** (4/18) |
| very_long | 86 | 1,346 | 38 | 97.2% | **19%** (3/16) |

**Finding:** Fact retention degrades linearly with conversation length. Short conversations retain ~83% of facts; very long ones drop to ~19%.

### Relationship Retention

| Size | Relationships | Retained | Rate |
|------|--------------|----------|------|
| short | 3 | 0 | **0%** |
| medium | 5 | 0 | **0%** |
| long | 3 | 0 | **0%** |
| very_long | 6 | 0 | **0%** |
| multi_project | 6 | 0 | **0%** |

**Finding:** Zero relationship retention across all sizes. The compressor drops all messages containing entity names (Alex, Orion, SQLite) because they don't match the user query keywords.

### Decision Retention

| Size | Decisions | Retained | Rate |
|------|-----------|----------|------|
| short | 2 | 0 | **0%** |
| medium | 4 | 0 | **0%** |
| long | 5 | 0 | **0%** |
| very_long | 0 | 0 | N/A |

**Finding:** Decisions are completely lost. The compressor's keyword matching doesn't capture decision-related content.

### Contradiction Preservation

| Size | Contradictions | Preserved | Rate |
|------|---------------|-----------|------|
| short | 0 | 0 | N/A |
| medium | 0 | 0 | N/A |
| long | 4 | 0 | **0%** |
| very_long | 1 | 0 | **0%** |

**Finding:** All contradictions are lost. The resolution text (e.g., "gRPC confirmed, REST rejected") is not present in the selected output.

### Temporal Accuracy

| Size | Current Events | Retained | Rate |
|------|---------------|----------|------|
| short | 0 | 0 | N/A |
| medium | 0 | 0 | N/A |
| long | 4 | 0 | **0%** |
| very_long | 2 | 0 | **0%** |

**Finding:** Temporal state (current vs superseded) is not preserved. The compressor cannot distinguish between old and new requirements.

### Long-Range Recall

| Size | Early Facts | Retained | Rate |
|------|------------|----------|------|
| short | 6 | 5 | **83%** |
| medium | 12 | 4 | **33%** |
| long | 18 | 4 | **22%** |
| very_long | 16 | 3 | **19%** |

**Finding:** Long-range recall degrades with conversation length. The first 10 messages retain ~83% in short convos but only ~19% in very long ones.

---

## Token Efficiency Across Strategies

All strategies produce identical output — the selector is budget-driven, not strategy-driven:

| Strategy | Short | Medium | Long | Very Long |
|----------|-------|--------|------|-----------|
| no_compression | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| default | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| conservative | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| aggressive | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |

**Finding:** The budget config has no effect on output — the selector always produces ~32–38 tokens regardless of budget. This suggests the selector is hitting a minimum floor.

---

## Performance

| Size | Avg | Min | Max | P95 |
|------|-----|-----|-----|-----|
| short | 0.016 ms | 0.016 ms | 0.019 ms | 0.019 ms |
| medium | 0.020 ms | 0.019 ms | 0.027 ms | 0.027 ms |
| long | 0.036 ms | 0.030 ms | 0.049 ms | 0.049 ms |
| very_long | 0.047 ms | 0.042 ms | 0.061 ms | 0.061 ms |

**Finding:** Processing is sub-millisecond across all sizes. The earlier <0.1ms measurements are confirmed — they include the full selection pipeline.

---

## Cache Awareness

| Size | Prefix Changed | Lines Invalidated | Recompute Est. | Recommendation |
|------|---------------|-------------------|----------------|----------------|
| short | Yes | 1 | 10 | proceed |
| medium | Yes | 1 | 10 | proceed |
| long | Yes | 1 | 10 | proceed |
| very_long | Yes | 1 | 10 | proceed |

**Finding:** Compression always changes the prefix (system prompt is stripped), invalidating 1 cache line. The impact is minimal (~10 recompute tokens).

---

## Key Findings

1. **Extreme compression, extreme information loss.** 97% token reduction comes with 81% fact loss in long conversations.
2. **Relationships are completely lost.** 0% retention across all sizes — the compressor cannot preserve entity relationships.
3. **Decisions and contradictions are lost.** No decision or contradiction resolution is preserved.
4. **Temporal state is not preserved.** The system cannot distinguish current from superseded requirements.
5. **Budget config has no effect.** All strategies produce identical output (~32–38 tokens).
6. **Performance is excellent.** Sub-millisecond processing even for 100+ exchange conversations.
7. **Cache impact is minimal.** Only 1 line invalidated regardless of conversation size.

---

## Known Limitations

- Evaluation uses substring matching, not semantic similarity — may undercount retention.
- Synthetic data is deterministic but simplified compared to real conversations.
- No LLM-based evaluation was performed (deterministic core only).
- Token counts use cl100k_base estimate; actual provider tokens may differ.

---

## Recommendations

1. **Add relationship-aware selection** — entity co-occurrence should boost message relevance.
2. **Preserve decision markers** — decisions should be flagged as high-importance regardless of budget.
3. **Implement temporal awareness** — superseded vs current state should be tracked explicitly.
4. **Investigate the budget floor** — all strategies producing identical output suggests a bug or design limitation.
5. **Add semantic evaluation** — substring matching is insufficient for measuring true retention quality.
