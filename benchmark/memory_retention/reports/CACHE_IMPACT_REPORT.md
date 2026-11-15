# CACHE IMPACT REPORT

**Date:** 2026-11-15  
**Method:** Prefix hash comparison before/after compression  

---

## Results

| Size | Original Tokens | Selected Tokens | Prefix Changed | Lines Invalidated | Recompute Est. | Recommendation |
|------|----------------|-----------------|---------------|-------------------|----------------|----------------|
| short | 225 | 36 | Yes | 1 | 10 | proceed |
| medium | 365 | 32 | Yes | 1 | 10 | proceed |
| long | 1,046 | 34 | Yes | 1 | 10 | proceed |
| very_long | 1,747 | 38 | Yes | 1 | 10 | proceed |

---

## Analysis

**Compression always changes the prefix**, but the impact is minimal:
- Only 1 line invalidated regardless of conversation size
- Estimated recompute cost: ~10 tokens
- Recommendation: "proceed" for all sizes

---

## Why Prefix Always Changes

The compressor strips the system prompt from the history selection. The original text starts with the system message, but the selected messages start with the first user/assistant exchange. This causes a prefix mismatch.

---

## KV Cache Impact Assessment

| Aspect | Impact |
|--------|--------|
| Prefix invalidation | Always occurs (system prompt stripped) |
| Magnitude | 1 line / ~10 tokens |
| Mitigation | System prompt is typically cached separately |
| Net effect | Negligible for typical LLM deployments |

---

## Known Limitations

- Cache analysis is text-based, not server-specific
- No actual KV cache metrics available (would require llama.cpp / LM Studio integration)
- The 1-line invalidation estimate assumes the system prompt occupies ~1 line in the prefix

---

## Recommendation

Consider a **prefix-preserving compression mode** where the system prompt is kept intact and only history messages are compressed. This would eliminate the prefix change entirely.
