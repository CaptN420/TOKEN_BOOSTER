# PERFORMANCE REPORT

**Date:** 2026-11-15  
**Method:** 20 repeated trials, warm-up included  

---

## Processing Time

| Size | Exchanges | Original Tokens | Avg | Min | Max | Median | P95 |
|------|-----------|----------------|-----|-----|-----|--------|-----|
| short | 10 | 225 | 0.016 ms | 0.016 ms | 0.019 ms | 0.016 ms | 0.019 ms |
| medium | 21 | 365 | 0.020 ms | 0.019 ms | 0.027 ms | 0.019 ms | 0.027 ms |
| long | 54 | 1,046 | 0.036 ms | 0.030 ms | 0.049 ms | 0.031 ms | 0.049 ms |
| very_long | 86 | 1,747 | 0.047 ms | 0.042 ms | 0.061 ms | 0.044 ms | 0.061 ms |

---

## Scalability

Processing time scales approximately **linearly** with conversation size:
- 10 → 21 exchanges: +25% time
- 21 → 54 exchanges: +80% time
- 54 → 86 exchanges: +31% time

The selector is O(n) where n is the number of messages, with constant-time token estimation.

---

## Breakdown

The measured time includes:
1. Token estimation for all messages (O(n))
2. Relevance scoring (O(n))
3. Budget-constrained selection (O(n log n) due to sorting)
4. Output assembly

All operations are in-memory with no I/O.

---

## Comparison to Earlier Measurements

Earlier benchmarks reported <0.1ms for all sizes. This confirms those measurements:
- The full selection pipeline completes in <0.07ms even for 86-exchange conversations
- The sub-0.1ms measurement includes the complete context preparation, not just selection

---

## Known Limitations

- Timing includes Python interpreter overhead (not bare C speed)
- No memory profiling was performed (would require tracemalloc)
- Single-threaded execution; parallel selection could improve large-convo performance
