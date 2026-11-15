# Boster Security and License Report

**Date:** 2026-11-15

---

## 1. LICENSE STATUS

### Current State
| Item | Status |
|------|--------|
| LICENSE file | **MISSING** |
| License in pyproject.toml | Not declared |
| License in README.md | Claims MIT but no legal text |

### Risk Assessment
**CRITICAL:** Project claims MIT license but has no LICENSE file. This creates:
- Legal ambiguity for users
- Potential copyright issues
- Inability to properly distribute on PyPI (requires LICENSE)

### Required Action
Add MIT LICENSE file before any public release:

```
MIT License

Copyright (c) 2026 TinTin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 2. CODE PROVENANCE

### Extracted Modules
| Module | Source | License Compatibility |
|--------|--------|----------------------|
| `output_compressor.py` | `Life_mean_realm_STOP/code_lab/compression` | MIT (original project) |
| `history_compressor.py` | `Life_mean_realm_STOP/code_lab/compression` | MIT (original project) |
| `context_budget.py` | `Life_mean_realm_STOP/code_lab/compression` | MIT (original project) |
| `pipeline.py` | `Life_mean_realm_STOP/code_lab/compression` | MIT (original project) |
| `adaptive.py` | `Life_mean_realm_STOP/code_lab/compression` | MIT (original project) |

**Status:** All extracted modules appear to be from the same project (CaptN-BRAIN/Life_mean) with MIT license. Compatible with new MIT license.

### New Modules
| Module | Source | License |
|--------|--------|---------|
| `core/tokenizer.py` | Newly written | MIT |
| `core/token_metrics.py` | Newly written | MIT |
| `core/policies.py` | Newly written | MIT |
| `core/budget.py` | Newly written | MIT |
| `core/optimizer.py` | Newly written | MIT |
| `core/engine.py` | Newly written | MIT |
| `compression/*.py` | Newly written | MIT |
| `memory/*.py` | Newly written | MIT |
| `context/*.py` | Newly written | MIT |
| `adapters/*.py` | Newly written | MIT |

**Status:** All new modules are originally written for this project. No licensing concerns.

---

## 3. SECURITY FINDINGS

### 3.1 Network Security

#### Finding: HF Tokenizer Makes HTTP Requests
**Severity:** MEDIUM
**Location:** `core/tokenizer.py:93`
```python
url = f"https://huggingface.co/{self.tokenizer_name}/raw/main/tokenizer.json"
r = requests.get(url, timeout=10)
```

**Impact:**
- Network dependency in core module
- Fails in offline environments
- Potential DNS hijacking (low risk for public tokenizer configs)

**Recommendation:** 
- Cache tokenizer configs locally
- Make network optional with `offline=True` flag
- Document offline usage requirements

#### Finding: No Authentication Required
**Status:** OK — Public HuggingFace repos don't require auth.

### 3.2 Input Validation

#### Finding: No Path Traversal Protection
**Severity:** LOW
**Location:** `cli.py:_read()`
```python
def _read(path):
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()
```

**Impact:**
- Path traversal possible (`../../etc/passwd`)
- Unlikely in local-only use case
- No data exfiltration risk (read-only)

**Recommendation:**
- Add path validation for file operations
- Log suspicious paths
- Consider sandboxing in future

#### Finding: JSON Input Parsed Safely
**Status:** OK — Uses `json.loads()` which is safe from injection.

### 3.3 Data Exposure

#### Finding: Memory Persistence Saves to Local File
**Severity:** LOW
**Location:** `memory/persistence.py`
```python
def save(self, path: str) -> None:
    with open(path, "w") as f:
        json.dump(self._entries, f, indent=2)
```

**Impact:**
- Sensitive conversation data saved to disk
- User-controlled path (no sandboxing)
- No encryption

**Recommendation:**
- Document that memory is saved in plaintext
- Add optional encryption flag
- Consider ephemeral-only mode

### 3.4 Cryptographic Concerns

#### Finding: MD5 Used for Memory IDs
**Severity:** LOW
**Location:** `memory/manager.py:67`
```python
h = hashlib.md5(content.encode()).hexdigest()[:12]
```

**Impact:**
- Collision-resistant enough for local memory
- Not cryptographically secure
- Acceptable for this use case (identifier generation, not security)

**Recommendation:** No action needed. MD5 is sufficient for non-security identifier generation.

---

## 4. DEPENDENCY SECURITY

### Transitive Dependencies
| Package | License | Known Vulnerabilities |
|---------|---------|----------------------|
| tiktoken | MIT | None reported |
| tokenizers | Apache-2.0 | None reported |
| rich | MIT | None reported |
| pytest | MIT | None reported |
| ruff | MIT | None reported |

**Status:** All dependencies are widely used, permissively licensed, and have no known critical vulnerabilities.

---

## 5. SECURITY BEST PRACTICES

### Current Compliance
| Practice | Status |
|----------|--------|
| No hardcoded secrets | PASS |
| No sensitive data in logs | PASS |
| Input validation (JSON) | PASS |
| Path traversal protection | FAIL (LOW severity) |
| Network security | PARTIAL (HF tokenizer) |
| License compliance | FAIL (missing LICENSE file) |

### Recommended Improvements
1. Add MIT LICENSE file immediately
2. Add path validation for file operations
3. Cache HF tokenizer configs locally
4. Document offline usage requirements
5. Add optional encryption for memory persistence

---

## 6. SECURITY POLICY TEMPLATE

For public repository, add `SECURITY.md`:

```markdown
# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes       |
| < 0.2.0 | No        |

## Reporting a Vulnerability

Please report security vulnerabilities to:
- GitHub Issues (private)
- Email: [TBD]

## Security Considerations

### Offline Operation
Boster is designed for offline operation. The only network dependency
is optional HuggingFace tokenizer loading, which can be disabled by:
1. Pre-downloading tokenizer configs
2. Using `offline=True` flag (future)

### Data Privacy
Memory persistence saves to local JSON files. No data is sent
externally. Users control storage location and retention.

### Dependencies
All dependencies are permissively licensed (MIT/Apache-2.0).
No proprietary or restrictive licenses.
```

---

## 7. LICENSE COMPLIANCE CHECKLIST

- [x] Core code is originally written or properly attributed
- [ ] LICENSE file added (REQUIRED before release)
- [ ] Copyright notice added to all source files
- [ ] Dependency licenses verified (all MIT/Apache-2.0)
- [ ] No copyleft licenses detected
- [ ] PyPI LICENSE field set correctly

---

## 8. CONCLUSION

**Security Score: 7/10**

### Critical Issues
1. Missing LICENSE file — blocks public release

### Medium Issues
1. Network dependency in core (HF tokenizer)
2. No path traversal protection

### Low Issues
1. Plaintext memory persistence
2. MD5 for identifiers (acceptable but not ideal)

### Recommendations
1. Add MIT LICENSE immediately
2. Document offline usage
3. Add path validation
4. Cache HF tokenizers locally

---

**End of Security and License Report**
