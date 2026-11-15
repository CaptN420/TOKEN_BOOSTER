# Boster Dependency Report

**Date:** 2026-11-15

---

## 1. CURRENT DEPENDENCIES

### Core Dependencies
```toml
[project]
dependencies = []
```

**Status:** Core is stdlib-only. No mandatory external dependencies.

### Optional Dependencies
```toml
[project.optional-dependencies]
tokenizers = ["tiktoken", "tokenizers"]
cli = ["rich"]
dev = ["pytest", "pytest-cov", "ruff", "build"]
```

---

## 2. DEPENDENCY ANALYSIS

### 2.1 tiktoken (Optional)

| Attribute | Value |
|-----------|-------|
| Purpose | OpenAI tokenizer (cl100k_base, gpt2, p50k_base) |
| License | MIT |
| Size | ~2MB |
| Network Required | No (after install) |
| Used By | `core/tokenizer.py`, `context_budget.py` |

**Status:** OPTIONAL — Works without network after installation.

### 2.2 tokenizers (Optional)

| Attribute | Value |
|-----------|-------|
| Purpose | HuggingFace tokenizer (DeepSeek, Llama, etc.) |
| License | Apache-2.0 |
| Size | ~10MB |
| Network Required | YES (during first load) |
| Used By | `core/tokenizer.py` |

**Status:** OPTIONAL but makes network call on first load. **ISSUE.**

### 2.3 rich (Optional)

| Attribute | Value |
|-----------|-------|
| Purpose | Terminal presentation (progress bars, colors) |
| License | MIT |
| Size | ~500KB |
| Network Required | No |
| Used By | None currently (declared but not imported) |

**Status:** DECLARED but NOT USED. Can be removed or kept for future CLI enhancements.

### 2.4 requests (Not Declared)

| Attribute | Value |
|-----------|-------|
| Purpose | HTTP client for HF tokenizer load |
| License | Apache-2.0 |
| Size | ~50KB |
| Network Required | YES |
| Used By | `core/tokenizer.py` (indirectly via `tokenizers`) |

**Status:** NOT DECLARED in pyproject.toml. Transitive dependency from `tokenizers`. **ISSUE.**

---

## 3. DEPENDENCY ISSUES

### ISSUE 1: Network Dependency in Core (HIGH)

**Location:** `core/tokenizer.py:93`
```python
r = requests.get(url, timeout=10)
```

**Impact:**
- Violates "local first" principle
- Fails in offline environments
- Non-deterministic behavior

**Fix Options:**
1. Cache HF tokenizer configs locally
2. Require pre-downloaded tokenizer files
3. Make network optional with `offline=True` flag

### ISSUE 2: Undeclared Transitive Dependency (MEDIUM)

**Location:** `core/tokenizer.py` → `tokenizers` → `requests`

**Impact:**
- Users may not have `requests` installed
- Build may fail in minimal environments

**Fix:** Add `requests` to optional dependencies or remove HF tokenizer support.

### ISSUE 3: Version Pins Missing (LOW)

**Current:**
```toml
tokenizers = ["tiktoken", "tokenizers"]
```

**Recommended:**
```toml
tokenizers = ["tiktoken>=0.7.0", "tokenizers>=0.19.0"]
```

---

## 4. PROPOSED DEPENDENCY STRUCTURE

### Option A: Minimal (Recommended)
```toml
[project]
dependencies = []

[project.optional-dependencies]
tokenizers = ["tiktoken>=0.7.0"]
# tokenizers (HF) removed to eliminate network dependency
cli = ["rich>=13.0.0"]
dev = ["pytest>=8.0.0", "pytest-cov>=4.0.0", "ruff>=0.2.0", "build>=1.0.0"]
```

### Option B: Full (With HF Support)
```toml
[project]
dependencies = []

[project.optional-dependencies]
tokenizers = [
    "tiktoken>=0.7.0",
    "tokenizers>=0.19.0",
    "requests>=2.31.0",  # Explicitly declare transitive dep
]
cli = ["rich>=13.0.0"]
dev = ["pytest>=8.0.0", "pytest-cov>=4.0.0", "ruff>=0.2.0", "build>=1.0.0"]
```

### Option C: Offline-First (Best for "Local First" Principle)
```toml
[project]
dependencies = []

[project.optional-dependencies]
tokenizers = ["tiktoken>=0.7.0"]
# HF tokenizer support via separate package
hf = ["boster-hf-tokenizers>=0.1.0"]
cli = ["rich>=13.0.0"]
dev = ["pytest>=8.0.0", "pytest-cov>=4.0.0", "ruff>=0.2.0", "build>=1.0.0"]
```

---

## 5. DEPENDENCY LICENSE COMPLIANCE

| Dependency | License | Compatible with MIT? |
|------------|---------|---------------------|
| tiktoken | MIT | YES |
| tokenizers | Apache-2.0 | YES (permissive) |
| rich | MIT | YES |
| pytest | MIT | YES |
| ruff | MIT | YES |

**All dependencies are compatible with MIT license.**

---

## 6. RECOMMENDATIONS

### Immediate Actions
1. Add `requests` to optional dependencies (if keeping HF support)
2. Pin version numbers for all optional dependencies
3. Document network dependency in README

### Future Actions
1. Consider removing HF tokenizer support to eliminate network dependency
2. Add `offline=True` flag to tokenizer API
3. Cache tokenizer configs in `~/.cache/boster/`

---

## 7. DEPENDENCY VERIFICATION

### Current Environment
```bash
$ uv pip list | grep -E "tiktoken|tokenizers|rich|requests"
tiktoken          0.7.0
tokenizers        0.19.1
rich              13.7.1
requests          2.32.3
```

### Build Verification
```bash
$ python -m build
Successfully built dist/boster-0.2.0.tar.gz
Successfully built dist/boster-0.2.0-py3-none-any.whl
```

---

**End of Dependency Report**
