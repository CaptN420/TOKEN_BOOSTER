# TOKEN EFFICIENCY REPORT

**Date:** 2026-11-15  
**Tokenizer:** cl100k_base (tiktoken)  
**Method:** Deterministic selection, no LLM calls  

---

## Overview

This report measures token efficiency across four compression strategies on conversations of increasing length.

---

## Token Reduction by Size

| Size | Original | Selected | Reduction | Messages Kept |
|------|----------|----------|-----------|---------------|
| short (10 exch) | 177 | 36 | 79.7% | 1 |
| medium (21 exch) | 267 | 32 | 88.0% | 1 |
| long (54 exch) | 799 | 34 | 95.7% | 1 |
| very_long (86 exch) | 1,346 | 38 | 97.2% | 1 |

---

## Strategy Comparison

All four strategies produce **identical output**:

| Strategy | Budget Total | Short | Medium | Long | Very Long |
|----------|-------------|-------|--------|------|-----------|
| no_compression | 999,999 | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| default | 2,000 | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| conservative | 4,096 | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |
| aggressive | 500 | 36 (84%) | 32 (91%) | 34 (97%) | 38 (98%) |

**Finding:** The budget configuration has no observable effect on the selection result. The selector always converges to ~32–38 tokens.

---

## Processing Time by Strategy

| Strategy | Short | Medium | Long | Very Long |
|----------|-------|--------|------|-----------|
| no_compression | 0.035 ms | 0.029 ms | 0.042 ms | 0.053 ms |
| default | 0.028 ms | 0.035 ms | 0.039 ms | 0.055 ms |
| conservative | 0.036 ms | 0.028 ms | 0.055 ms | 0.074 ms |
| aggressive | 0.025 ms | 0.028 ms | 0.039 ms | 0.055 ms |

**Finding:** No meaningful performance difference between strategies. All are sub-millisecond.

---

## Information Loss per Token

| Size | Tokens Saved | Facts Lost | Relationships Lost | Decisions Lost |
|------|-------------|------------|-------------------|----------------|
| short | 141 | 1 (17%) | 3 (100%) | 2 (100%) |
| medium | 233 | 8 (67%) | 5 (100%) | 4 (100%) |
| long | 765 | 14 (78%) | 3 (100%) | 5 (100%) |
| very_long | 1,308 | 13 (81%) | 6 (100%) | 0 (N/A) |

**Finding:** Each token "saved" corresponds to ~0.01 facts lost. The compression is efficient in raw tokens but destructive to semantic content.

---

## Known Limitations

- Token counts are estimates (cl100k_base), not provider-exact.
- No comparison against alternative selection strategies beyond the 4 tested.
- Snapshot-based and relationship-aware selection were not implemented (planned).
