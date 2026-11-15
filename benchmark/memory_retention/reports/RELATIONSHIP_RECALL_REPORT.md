# RELATIONSHIP RECALL REPORT

**Date:** 2026-11-15  
**Method:** Entity co-occurrence check in selected messages  

---

## Results

| Size | Total Relations | Retained | Rate |
|------|----------------|----------|------|
| short | 3 | 0 | **0%** |
| medium | 5 | 0 | **0%** |
| long | 3 | 0 | **0%** |
| very_long | 6 | 0 | **0%** |
| multi_project | 6 | 0 | **0%** |

---

## Analysis

**Zero relationship retention across all conversation sizes.**

The compressor drops all messages containing entity names (e.g., "Alex", "Orion", "SQLite") because:
1. The user query keywords don't match entity names
2. Entity-only messages have low relevance scores
3. The budget floor (~32 tokens) leaves no room for relationship content

---

## Root Cause

The `_select_history` function scores messages by:
1. Recency (recent messages get higher priority)
2. Query relevance (keyword matching)

Entity relationships like "Alex owns Orion" are typically expressed in single messages that:
- Appear early in the conversation (low recency score)
- Don't contain query keywords (low relevance score)

Result: they are the first messages dropped when budget is tight.

---

## Recommendation

Add **entity co-occurrence scoring** to the selector:
- When a message contains entities also present in high-scoring messages, boost its relevance
- Track entity graphs separately from text compression
- Consider a two-pass approach: first select key entities, then ensure their context is preserved
