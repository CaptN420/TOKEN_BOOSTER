# Boster Migration Plan

**Date:** 2026-11-15

---

## 1. MIGRATION OVERVIEW

### Current State
- Dual module structure (legacy + v1.0)
- 74 tests passing
- Core functionality working
- Missing LICENSE file

### Target State
- Single unified module structure
- Clean stdlib-only core
- No network dependencies in core
- Token-based savings reporting
- Complete documentation

---

## 2. MIGRATION PHASES

### Phase 1: Foundation (Estimated: 2-3 days)

#### 1.1 Add LICENSE File
**File:** `LICENSE`
**Action:** Create MIT LICENSE file with copyright notice.

```bash
cat > LICENSE << 'EOF'
MIT License

Copyright (c) 2026 TinTin

Permission is hereby granted, free of charge, ...
EOF
```

#### 1.2 Fix Version Inconsistency
**Files:** `boster/__init__.py`, `pyproject.toml`
**Action:** Set version to `1.0.0` consistently.

```toml
# pyproject.toml
[project]
version = "1.0.0"
```

```python
# boster/__init__.py
__version__ = "1.0.0"
```

#### 1.3 Add Deprecation Warnings to Legacy Modules
**Files:** `output_compressor.py`, `context_budget.py`, `cli.py`

```python
# output_compressor.py
import warnings

def compress(text, fmt=None, max_lines=None, max_chars=None):
    warnings.warn(
        "output_compressor.compress is deprecated, use compression.terminal.compress_terminal",
        DeprecationWarning,
        stacklevel=2
    )
    from .compression.terminal import compress_terminal
    return compress_terminal(text, level=2)
```

### Phase 2: Consolidation (Estimated: 3-4 days)

#### 2.1 Unify Token Counting
**Files:** `context_budget.py`, `core/tokenizer.py`
**Action:** Route all token counting through `core/tokenizer.py`.

```python
# context_budget.py — remove direct tiktoken import
from ..core.tokenizer import count_tokens

def estimate_provider_tokens(text: str, model: str = "") -> int:
    return count_tokens(text, model)
```

#### 2.2 Add Token-Based Savings Reporting
**Files:** `core/token_metrics.py`
**Action:** Add `token_savings_report()` function.

```python
def token_savings_report(text: str, compressed: str, model: str = "") -> dict:
    """Return token-based savings metrics."""
    orig_tokens = count_tokens(text, model)
    comp_tokens = count_tokens(compressed, model)
    return {
        "original_tokens": orig_tokens,
        "compressed_tokens": comp_tokens,
        "savings_pct": round(((orig_tokens - comp_tokens) / max(orig_tokens, 1)) * 100, 1),
        "original_chars": len(text),
        "compressed_chars": len(compressed),
    }
```

#### 2.3 Remove Network Dependency from Tokenizer
**Files:** `core/tokenizer.py`
**Action:** Cache HF tokenizer configs locally or make optional.

```python
def get_tokenizer(model: str = "", offline: bool = False) -> TokenizerAdapter:
    """Get tokenizer adapter.
    
    Args:
        model: Model name.
        offline: If True, skip network calls and use fallback.
    
    Returns:
        TokenizerAdapter instance.
    """
    if offline:
        return CharEstimateAdapter()
    
    # Normal path with network fallback
    ...
```

### Phase 3: Testing (Estimated: 1-2 days)

#### 3.1 Add Missing Tests
**Files:** `tests/test_v1.py`
**Action:** Add tests for new functionality.

```python
def test_token_savings_report():
    from boster.core.token_metrics import token_savings_report
    text = "hello world " * 100
    compressed = "hw " * 100
    report = token_savings_report(text, compressed, model="gpt-4")
    assert report["original_tokens"] > 0
    assert report["compressed_tokens"] < report["original_tokens"]

def test_offline_tokenizer():
    from boster.core.tokenizer import get_tokenizer
    adapter = get_tokenizer("gpt-4", offline=True)
    assert adapter.count("hello") > 0

def test_memory_expiration():
    from boster.memory.manager import MemoryManager
    import time
    mgr = MemoryManager(expiration_hours=0.0001)
    mgr.add("test content", importance=1.0)
    time.sleep(1)
    expired = mgr.expire()
    assert expired == 1
```

#### 3.2 Add Performance Benchmarks
**Files:** `benchmarks/benchmark.py`
**Action:** Create reproducible benchmark suite.

```python
import time
from boster import Boster

def benchmark_compression():
    engine = Boster()
    text = "x" * 100_000
    
    start = time.perf_counter()
    compressed = engine.compress(text)
    elapsed = time.perf_counter() - start
    
    print(f"Compression: {elapsed:.3f}s")
    print(f"Savings: {(1 - len(compressed)/len(text))*100:.1f}%")

if __name__ == "__main__":
    benchmark_compression()
```

### Phase 4: Documentation (Estimated: 1 day)

#### 4.1 Update README.md
**File:** `README.md`
**Action:** Add v1.0 features, installation, usage examples.

#### 4.2 Add CONTRIBUTING.md
**File:** `CONTRIBUTING.md`
**Action:** Document contribution guidelines.

#### 4.3 Add CHANGELOG.md
**File:** `CHANGELOG.md`
**Action:** Document version history.

---

## 3. FILE MIGRATION MAP

### Legacy → v1.0 Consolidation

| Legacy File | Target File | Action | Risk |
|-------------|-------------|--------|------|
| `output_compressor.py` | `compression/terminal.py` | Add deprecation warning | LOW |
| `context_budget.py` | `core/budget.py` | Route through new API | MEDIUM |
| `cli.py` | `cli/commands.py` | Extend, don't replace | LOW |
| `history_compressor.py` | `memory/summarizer.py` | Merge functionality | MEDIUM |
| `adaptive.py` | `compression/structured.py` | Unify adaptive logic | LOW |

### New Files to Create

| File | Purpose |
|------|---------|
| `LICENSE` | MIT license text |
| `CONTRIBUTING.md` | Contribution guidelines |
| `CHANGELOG.md` | Version history |
| `SECURITY.md` | Security policy |
| `ROADMAP.md` | Development roadmap |
| `benchmarks/benchmark.py` | Performance benchmarks |
| `.github/workflows/ci.yml` | CI workflow |

---

## 4. AFFECTED IMPORTS

### Public API Changes

```python
# Before (v0.2.0)
from boster import compress_output, compress_history, ContextBudget
from boster.context_budget import BudgetConfig

# After (v1.0.0)
from boster import Boster, count_tokens, compress_terminal
from boster.core.budget import BudgetConfig
```

### Internal Import Changes

| File | Current Import | New Import |
|------|---------------|------------|
| `context_budget.py` | `import tiktoken` | `from ..core.tokenizer import count_tokens` |
| `cli.py` | `from . import compress_output` | `from .compression.terminal import compress_terminal` |
| `pipeline.py` | `from .context_budget import ContextBudget` | `from .core.budget import BudgetConfig` |

---

## 5. REGRESSION RISKS

### High Risk
- **Token counting changes:** Ensure all callers use new API
- **CLI command changes:** Test all CLI commands end-to-end

### Medium Risk
- **Memory consolidation:** Test persistence round-trip
- **Budget selection:** Test with large conversations

### Low Risk
- **Compression levels:** Test all 5 levels (0-4)
- **Error handling:** Test malformed inputs

---

## 6. ROLLBACK PLAN

If migration fails:

1. **Revert git changes:**
   ```bash
   git revert <commit-hash>
   ```

2. **Restore from backup:**
   ```bash
   git checkout HEAD~1 -- .
   ```

3. **Verify tests pass:**
   ```bash
   python -m pytest tests/ -q
   ```

---

## 7. TIMELINE

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| Foundation | 2-3 days | LICENSE, version fix, deprecation warnings |
| Consolidation | 3-4 days | Unified APIs, token savings, offline mode |
| Testing | 1-2 days | New tests, benchmarks |
| Documentation | 1 day | README, CONTRIBUTING, CHANGELOG |
| **Total** | **7-10 days** | **v1.0.0 ready for release** |

---

## 8. SUCCESS CRITERIA

- [ ] All 74 existing tests pass
- [ ] 10 new tests added (token savings, offline, expiration, concurrent)
- [ ] LICENSE file present
- [ ] README.md updated with v1.0 features
- [ ] No network dependencies in core
- [ ] Token-based savings reporting works
- [ ] CLI commands functional
- [ ] Documentation complete

---

**End of Migration Plan**
