# Boster Architecture Review

**Date:** 2026-11-15

---

## 1. CURRENT ARCHITECTURE

### 1.1 Module Layout

```
boster/
├── __init__.py          # v1.0 API entry (Boster class, re-exports)
├── cli.py               # Legacy CLI (original extraction)
├── output_compressor.py # Core compression (original)
├── history_compressor.py# History summarization (original)
├── context_budget.py    # Context budgeting (original, heavy deps)
├── pipeline.py          # Pipeline orchestrator (original)
├── adaptive.py          # Adaptive compression (original)
├── _tool_domains.py     # Hermes plugin hooks
├── __main__.py          # CLI entry point
│
├── core/                # v1.0 new modules
│   ├── tokenizer.py     # Tokenizer abstraction
│   ├── token_metrics.py # Cost estimation
│   ├── policies.py      # Safety/integrity rules
│   ├── budget.py        # Dynamic allocation
│   ├── optimizer.py     # Prompt optimization
│   └── engine.py        # Main Boster API class
│
├── compression/         # v1.0 new modules
│   ├── json.py
│   ├── markdown.py
│   ├── code.py
│   ├── logs.py
│   ├── terminal.py      # Levels 0-4
│   └── structured.py
│
├── memory/              # v1.0 new modules
│   ├── models.py
│   ├── manager.py       # CRUD + classification
│   ├── summarizer.py
│   ├── consolidation.py
│   ├── retrieval.py     # Lexical search
│   └── persistence.py
│
├── context/             # v1.0 new modules
│   ├── assembler.py
│   ├── allocator.py
│   ├── relevance.py
│   └── deduplication.py
│
├── adapters/            # v1.0 new modules
│   └── __init__.py      # Provider stubs
│
├── analytics/           # v1.0 new modules
│   └── __init__.py
│
└── cli/                 # v1.0 new CLI commands
    └── commands.py
```

### 1.2 Dependency Graph

```
core/engine.py
├── core/tokenizer.py
├── core/token_metrics.py
├── core/budget.py
│   ├── core/tokenizer.py
│   └── core/token_metrics.py
├── core/policies.py
├── core/optimizer.py
├── compression/terminal.py
│   └── output_compressor.py (legacy)
├── context/assembler.py
│   └── core/tokenizer.py
├── memory/manager.py
├── memory/retrieval.py
│   └── core/tokenizer.py
└── memory/summarizer.py
```

**No circular imports detected.**

---

## 2. ARCHITECTURAL ISSUES

### 2.1 Dual Module Structure (HIGH)

**Problem:** Legacy modules coexist with v1.0 modules, creating confusion.

| Legacy | v1.0 Equivalent | Status |
|--------|-----------------|--------|
| `output_compressor.py` | `compression/terminal.py` | Both exist |
| `context_budget.py` | `core/budget.py` | Both exist |
| `cli.py` | `cli/commands.py` | Both exist |
| `history_compressor.py` | `memory/summarizer.py` | Partial overlap |

**Impact:**
- Import paths are ambiguous
- Documentation is split
- Maintenance burden doubled
- Users may import from wrong module

**Recommendation:** Consolidate legacy modules into v1.0 structure or add deprecation warnings.

### 2.2 Network Dependency in Core (HIGH)

**Problem:** `context_budget.py` imports `requests` and makes HTTP calls during tokenizer load.

```python
# core/tokenizer.py line 93
r = requests.get(url, timeout=10)
```

**Impact:**
- Violates "local first" principle
- Fails in offline environments
- Non-deterministic behavior (network timeouts)

**Recommendation:** Remove network dependency from tokenizer. Cache HF tokenizer configs locally or use pre-downloaded models.

### 2.3 Token Counting Inconsistency (MEDIUM)

**Problem:** Two token counting implementations exist:
- `context_budget.py::estimate_provider_tokens()` — legacy, uses tiktoken directly
- `core/tokenizer.py::count_tokens()` — v1.0, pluggable adapters

**Impact:**
- Inconsistent token counts across the codebase
- Savings reports measure character reduction, not token reduction

**Recommendation:** Route all token counting through `core/tokenizer.py`. Update legacy modules to use new API.

### 2.4 Missing Token-Based Savings (MEDIUM)

**Problem:** `savings_report()` measures character reduction:
```python
def savings_report(text: str, compressed: str) -> dict:
    orig_chars = len(text)
    comp_chars = len(compressed)
    return {
        "savings_pct": round(((orig_chars - comp_chars) / max(orig_chars, 1)) * 100, 1),
        ...
    }
```

**Impact:**
- Misleading metrics (character reduction ≠ token reduction)
- Users cannot verify actual token savings

**Recommendation:** Add token-based savings reporting:
```python
def token_savings_report(text: str, compressed: str, model: str = "") -> dict:
    orig_tokens = count_tokens(text, model)
    comp_tokens = count_tokens(compressed, model)
    return {
        "original_tokens": orig_tokens,
        "compressed_tokens": comp_tokens,
        "savings_pct": round(((orig_tokens - comp_tokens) / max(orig_tokens, 1)) * 100, 1),
        ...
    }
```

---

## 3. ARCHITECTURAL RECOMMENDATIONS

### 3.1 Module Consolidation Plan

**Phase 1: Deprecate Legacy Modules**
```python
# output_compressor.py (add deprecation warning)
import warnings
warnings.warn(
    "output_compressor is deprecated, use compression.terminal instead",
    DeprecationWarning,
    stacklevel=2
)
from compression.terminal import compress_terminal as compress
from compression.terminal import safe_compress
```

**Phase 2: Remove Legacy Modules (v1.1)**
- Delete `output_compressor.py`
- Delete `context_budget.py` (or move to `examples/`)
- Delete `cli.py` (or merge into `cli/commands.py`)
- Delete `history_compressor.py` (merge into `memory/summarizer.py`)

### 3.2 Network Dependency Removal

**Option A: Local Cache**
```python
# core/tokenizer.py
_HF_TOKENIZER_CACHE = os.path.join(os.getenv("XDG_CACHE_HOME", "~/.cache"), "boster", "tokenizers")

def _load_hf_tokenizer(name: str) -> Tokenizer:
    cache_path = os.path.join(_HF_TOKENIZER_CACHE, name)
    if os.path.exists(cache_path):
        return Tokenizer.from_file(os.path.join(cache_path, "tokenizer.json"))
    
    # Download and cache
    url = f"https://huggingface.co/{name}/raw/main/tokenizer.json"
    ...
```

**Option B: Configurable Offline Mode**
```python
# core/tokenizer.py
def get_tokenizer(model: str = "", offline: bool = False) -> TokenizerAdapter:
    if offline:
        return CharEstimateAdapter()  # No network, pure estimation
    
    # Normal path with network fallback
    ...
```

### 3.3 Unified Token Counting

**Update all modules to use `core/tokenizer.py`:**
```python
# context_budget.py (legacy) — remove direct tiktoken import
from ..core.tokenizer import count_tokens

def estimate_provider_tokens(text: str, model: str = "") -> int:
    return count_tokens(text, model)
```

---

## 4. DATA FLOW DIAGRAM

### 4.1 Compression Pipeline

```
Input Text
    │
    ▼
┌─────────────────┐
│ Format Detect   │──→ JSON / Diff / Docker / Tree / Markdown / Generic
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Format-Specific │──→ JSON: compact re-serialize
│ Compressor      │──→ Diff: keep headers, dedupe hunks
│                 │──→ Docker: keep steps, drop cache lines
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Noise Stripping │──→ Progress bars, banners, timestamps
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Deduplication   │──→ Remove consecutive duplicate lines
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Blank Collapse  │──→ 3+ blank lines → 2
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Hard Caps       │──→ max_lines / max_chars (head + tail)
└─────────────────┘
    │
    ▼
Compressed Output
```

### 4.2 Context Budget Pipeline

```
Messages + Query
    │
    ▼
┌─────────────────┐
│ Token Estimation│──→ count_tokens() for each message
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Priority Score  │──→ System (1.0) > Query (0.9) > Recent (0.7) > Old (0.3)
│ Calculation     │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Budget Check    │──→ If total > budget, truncate lowest priority
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Emergency Mode  │──→ If >120% budget, aggressive truncation
└─────────────────┘
    │
    ▼
Selected Messages + Report
```

---

## 5. API STABILITY ASSESSMENT

### Public API Surface (v1.0)

```python
# boster/__init__.py
from .core.engine import Boster, OptimizeResult
from .core.tokenizer import count_tokens
from .core.token_metrics import cost_estimate, savings_report
from .core.budget import BudgetConfig, SelectionResult
from .compression.terminal import compress_terminal
from .memory.manager import MemoryManager
```

**Stability:** HIGH — Core API is stable and well-tested.

### Internal API (Not Public)

```python
# These are implementation details, subject to change
boster.core.optimizer
boster.context.assembler
boster.context.allocator
boster.context.relevance
boster.context.deduplication
boster.memory.summarizer
boster.memory.consolidation
boster.memory.persistence
boster.memory.retrieval
boster.adapters
boster.analytics
```

**Stability:** LOW — Internal modules may change without notice.

---

## 6. EXTENSIBILITY ASSESSMENT

### Easy to Extend
- **Tokenizers:** Add new adapter by subclassing `TokenizerAdapter`
- **Compressors:** Add new format by registering in `_FORMAT_COMPRESSORS`
- **Memory Categories:** Add new patterns to `CATEGORY_PATTERNS`

### Hard to Extend
- **Cost Estimation:** Limited model coverage, requires manual updates
- **Provider Adapters:** Stub implementations only, no real API calls

---

## 7. PERFORMANCE CHARACTERIZATION

| Operation | Complexity | Notes |
|-----------|------------|-------|
| Token counting | O(n) | Linear in text length |
| Compression | O(n) | Single pass through lines |
| Budget selection | O(n log n) | Sorting by priority score |
| Memory search | O(n) | Lexical overlap scoring |
| Context assembly | O(n) | Linear concatenation |

**Bottlenecks:**
- Large conversations (>1000 messages) — budget selection dominates
- Very long text (>100KB) — compression dominates

---

## 8. SECURITY ASSESSMENT

### Input Validation
- File paths: No sanitization (path traversal possible)
- JSON input: Parsed with `json.loads()`, safe from injection
- Text input: No validation, passed through directly

### Network Security
- HF tokenizer load: HTTP GET to huggingface.co (not critical)
- No authentication required for public repos

### Data Exposure
- Memory persistence: Saves to local JSON file (user-controlled path)
- No sensitive data in logs (debug logging only)

---

## 9. CONCLUSION

**Architecture Score: 7/10**

### Strengths
- Clean separation of concerns (core, compression, memory, context)
- Pluggable tokenizer abstraction
- Comprehensive test coverage
- Deterministic output

### Weaknesses
- Dual module structure creates maintenance burden
- Network dependency in core violates "local first" principle
- Token savings measured as character reduction (misleading)

### Recommendations
1. Consolidate legacy modules into v1.0 structure
2. Remove network dependency from tokenizer
3. Add token-based savings reporting
4. Document public vs internal API clearly

---

**End of Architecture Review**
