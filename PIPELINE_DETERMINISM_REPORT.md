# BOSTER — Pipeline determinism report

**Scope.** The consolidation pipeline (`consolidate_exchanges`,
`consolidate_to_episodes`) → persisted state. The pipeline claimed to be
deterministic ("déterministe pour une même entrée") while it was not.

**Verdict: deterministic now.** Verified, not asserted — see Evidence.

---

## 1. The defect, reproduced

```python
s1 = snap(); time.sleep(0.02); s2 = snap()
# before:  two runs identical : False
```

Same conversation, same ids everywhere (`ep_2f0783f92bc5`,
`dec_aec3fac3dc3a`, …) — only the timestamps moved:

```
run1: 'created_at': 1794766241.763629
run2: 'created_at': 1794766241.784451
```

Root cause: every artifact was stamped by the wall clock. Six sites in the
pipeline output:

| Artifact | Field | Source of the value |
|---|---|---|
| `MemoryItem` | `created_at`, `updated_at` | `field(default_factory=time.time)` |
| `Entity` | `created_at` | idem |
| `Relation` | `created_at` | idem |
| `Contradiction` | `created_at` | idem |
| `Episode` | `created_at` | idem |
| `Episode` | `updated_at` | `Episode.touch()` → `time.time()` |

A seventh, latent one: `from_dict` used `data.get("created_at", time.time())`,
so **loading** a legacy document without timestamps invented one, and the
re-saved file differed from the first save.

All identifiers (`mid_`, `ep_`, `dec_`, `contra_`, `item_`) were already
content hashes — deterministic. Only time was not.

---

## 2. Fix

One concept: **a memory pipeline never invents a time it does not have.**

- `models.UNKNOWN_TIME = 0.0` — the deterministic stand-in for "unknown time".
- `consolidation.pipeline_stamp(exchanges) -> float` — one stamp per run: the
  **newest source timestamp**, or `UNKNOWN_TIME` when no message carries one.
  Booleans and non-numbers are ignored (`True` is not a timestamp).
- The pipeline stamps every artifact it creates with that stamp; a decision
  keeps its **own message** date (`_message_timestamp`, `None` if absent).
- `MemoryItem.touch(at=None)` / `Episode.touch(at=None)` — the refresh is
  injectable, so a merge records the source stamp instead of "now".
- Every `from_dict` fallback is now `UNKNOWN_TIME`, so a load is reproducible.
- `MemoryPersistence.save_state(..., saved_at=None)` — the write's own metadata
  defaults to the wall clock but can be pinned for a byte-identical file.
- `MemoryManager.retrieve(..., now=None)` — the recency term is time-dependent
  by design, but the ranking is reproducible when a clock is supplied.
- `import time` was removed from `consolidation.py` entirely: the module can no
  longer reach a clock. A test asserts this (`assert not hasattr(module, "time")`).

### Deliberately NOT done
- **Model defaults unchanged.** `created_at: float = field(default_factory=time.time)`
  stays as-is on the models. Setting `0.0` there would have leaked into the
  legacy `MemoryManager`: `expire()` deletes entries where `created_at < cutoff`
  (every synthetic item would be expired instantly) and `retrieve()`'s recency
  term would saturate. The determinism belongs to the **pipeline**, which stamps
  explicitly — not to the general-purpose model.
- **No public interface change.** A `created_at=` keyword was added to both
  consolidation functions for a while; it was reverted. The project pins those
  signatures (`test_consolidate_exchanges_signature_unchanged`), derivation from
  the sources already covers the real case, and an optional convenience
  parameter is not a demonstrated reason to widen a public contract.

---

## 3. Evidence

```
benchmark/memory_determinism/            21 passed
suite complète (11 suites)               363 passed, 2 xfailed, 0 failed
```

New suite `benchmark/memory_determinism/test_pipeline_determinism.py` covers:
the stamp (newest source / unknown / ignores booleans / stable), absence of a
clock (`hasattr` check, no timestamp resembling `now()`), one shared stamp
across all six artifact types, undated runs, byte-identity across two runs in
one process, across **processes**, across `PYTHONHASHSEED` ∈ {0, 1, 42, random}
and across timezones, `save → load → save` byte-identity, legacy timestamp-less
documents, idempotent re-consolidation, and reproducible retrieval.

Independent shell sweep (outside pytest), hash of
`json([episodic.to_dict(), semantic.to_dict()], sort_keys=True)`:

```
5 runs, same process      -> 1 distinct hash : 2fc9f2db4ce46b85
PYTHONHASHSEED 0/1/42/12345/random -> 2fc9f2db4ce46b85  (identical)
TZ UTC / America-New-York / Asia-Tokyo -> 2fc9f2db4ce46b85  (identical)
grep 'time.time()' boster/memory/consolidation.py -> only the docstring
```

---

## 4. Remaining, documented (out of scope)

- `MemoryPersistence.save()` (the legacy v1 `_in_memory` store, not the
  three-tier state) still writes `saved_at: time.time()` and has no injectable
  clock.
- `MemoryManager.add()` refreshes `entry.updated_at = time.time()` on update,
  and `expire()` reads the wall clock. That subsystem is not part of the
  consolidation pipeline.
- `manager.retrieve()` is reproducible **only** when `now=` is passed; with no
  clock it is time-dependent on purpose (recency).

No file outside `boster/memory/` was modified for this work; the new tests live
in `benchmark/memory_determinism/`.
