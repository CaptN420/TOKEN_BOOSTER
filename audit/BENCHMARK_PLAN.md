# Boster Benchmark Plan

**Date:** 2026-11-15

---

## 1. BENCHMARK OBJECTIVES

Measure Boster's performance across four dimensions:
1. **Token reduction** — How many tokens are saved?
2. **Processing latency** — How fast is compression?
3. **Memory usage** — How much RAM is consumed?
4. **Information preservation** — What is lost during compression?

---

## 2. BENCHMARK DATASET

### 2.1 Terminal Output (50KB each)
| Source | Description | Expected Savings |
|--------|-------------|------------------|
| pip install | `pip install requests pandas numpy` | 60-80% |
| pytest run | Full test suite output | 40-60% |
| docker build | Multi-stage Docker build | 50-70% |

### 2.2 Conversation History (100KB)
| Source | Description | Expected Savings |
|--------|-------------|------------------|
| Hermes session | 100-turn conversation | 30-50% |
| Code review | PR discussion | 20-40% |
| Debugging session | Error investigation | 25-45% |

### 2.3 Code Output (30KB each)
| Source | Description | Expected Savings |
|--------|-------------|------------------|
| Git diff | Large feature branch | 50-70% |
| LOC report | Project metrics | 30-50% |
| Dependency graph | Complex project | 40-60% |

### 2.4 JSON API Responses (20KB each)
| Source | Description | Expected Savings |
|--------|-------------|------------------|
| GitHub API | Repository search results | 70-90% |
| HuggingFace model card | Model metadata | 50-70% |
| Weather API | Multi-day forecast | 60-80% |

---

## 3. METRICS TO MEASURE

### 3.1 Token Metrics
```python
from boster.core.tokenizer import count_tokens

original_tokens = count_tokens(raw_input, model="gpt-4")
compressed_tokens = count_tokens(compressed_output, model="gpt-4")
token_savings_pct = (original_tokens - compressed_tokens) / original_tokens * 100
```

### 3.2 Character Metrics
```python
original_chars = len(raw_input)
compressed_chars = len(compressed_output)
char_savings_pct = (original_chars - compressed_chars) / original_chars * 100
```

### 3.3 Latency Metrics
```python
import time

start = time.perf_counter()
compressed = boster.compress(raw_input)
elapsed_ms = (time.perf_counter() - start) * 1000
```

### 3.4 Memory Metrics
```python
import tracemalloc

tracemalloc.start()
compressed = boster.compress(raw_input)
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
```

### 3.5 Information Preservation
```python
# Count errors in original vs compressed
original_errors = raw_input.count("ERROR") + raw_input.count("Exception")
compressed_errors = compressed_output.count("ERROR") + compressed_output.count("Exception")
error_retention_pct = compressed_errors / original_errors * 100
```

---

## 4. COMPARISON TARGETS

### 4.1 Boster vs Raw
| Metric | Boster | Raw |
|--------|--------|-----|
| Tokens | Compressed count | Original count |
| Chars | Compressed length | Original length |
| Latency | Compression time | N/A |

### 4.2 Boster vs LLMLingua (Future)
| Metric | Boster | LLMLingua |
|--------|--------|-----------|
| Token reduction | TBD | TBD |
| Latency | TBD | TBD |
| Memory usage | TBD | TBD |

### 4.3 Boster vs RTK (Reference)
| Metric | Boster | RTK |
|--------|--------|-----|
| Token reduction | TBD | ~60-90% |
| Latency | TBD | TBD |

---

## 5. BENCHMARK EXECUTION PLAN

### 5.1 Single-Run Benchmark
```python
# benchmarks/run_benchmark.py
import json
import time
from pathlib import Path
from boster import Boster

def run_benchmark(dataset_path: str, model: str = "gpt-4"):
    engine = Boster(model=model)
    
    # Load dataset
    with open(dataset_path) as f:
        data = json.load(f)
    
    results = []
    for item in data:
        raw = item["input"]
        
        # Measure tokens
        orig_tokens = engine.count_tokens(raw)
        
        # Measure compression
        start = time.perf_counter()
        compressed = engine.compress(raw)
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        comp_tokens = engine.count_tokens(compressed)
        
        # Measure preservation
        errors_preserved = raw.count("ERROR") == compressed.count("ERROR")
        
        results.append({
            "input_size_chars": len(raw),
            "output_size_chars": len(compressed),
            "original_tokens": orig_tokens,
            "compressed_tokens": comp_tokens,
            "token_savings_pct": (orig_tokens - comp_tokens) / orig_tokens * 100,
            "char_savings_pct": (len(raw) - len(compressed)) / len(raw) * 100,
            "latency_ms": elapsed_ms,
            "errors_preserved": errors_preserved,
        })
    
    return results

if __name__ == "__main__":
    import sys
    dataset = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/dataset.json"
    results = run_benchmark(dataset)
    print(json.dumps(results, indent=2))
```

### 5.2 Reproducible Benchmark Suite
```python
# benchmarks/benchmark_suite.py
import pytest
from boster import Boster

@pytest.fixture
def engine():
    return Boster(model="gpt-4")

def test_pip_output_compression(engine):
    raw = "Requirement already satisfied: requests in /usr/lib/python..." * 50
    compressed = engine.compress(raw)
    
    assert len(compressed) < len(raw)
    assert engine.count_tokens(compressed) < engine.count_tokens(raw)

def test_pytest_output_compression(engine):
    raw = "test_session ... PASSED ... FAILED ..." * 30
    compressed = engine.compress(raw)
    
    assert "PASSED" in compressed
    assert "FAILED" in compressed

def test_large_conversation_budget(engine):
    messages = [{"role": "user", "content": f"message {i}"} for i in range(100)]
    result = engine.optimize(messages)
    
    assert result.tokens_after < result.tokens_before
```

---

## 6. EXPECTED RESULTS

### Terminal Output Compression
| Input Size | Expected Token Savings | Expected Latency |
|------------|----------------------|------------------|
| 10KB | 40-60% | <10ms |
| 50KB | 50-70% | <50ms |
| 100KB | 60-80% | <100ms |

### Conversation History Compression
| Input Size | Expected Token Savings | Expected Latency |
|------------|----------------------|------------------|
| 10KB | 20-40% | <10ms |
| 50KB | 30-50% | <50ms |
| 100KB | 40-60% | <100ms |

### JSON Compression
| Input Size | Expected Token Savings | Expected Latency |
|------------|----------------------|------------------|
| 10KB | 60-80% | <5ms |
| 50KB | 70-90% | <20ms |
| 100KB | 80-90% | <50ms |

---

## 7. REPORTING FORMAT

### Benchmark Report Template
```markdown
# Boster Benchmark Report

## Test Configuration
- Model: gpt-4
- Compression level: 2
- Python version: 3.11.16

## Results Summary
| Dataset | Original Tokens | Compressed Tokens | Savings | Latency |
|---------|----------------|-------------------|---------|---------|
| pip output (50KB) | 12,500 | 4,375 | 65% | 12ms |
| pytest output (30KB) | 7,500 | 3,750 | 50% | 8ms |
| Docker build (50KB) | 12,500 | 4,000 | 68% | 15ms |

## Information Preservation
- Errors preserved: 100%
- Warnings preserved: 100%
- Stack traces preserved: 100%

## Peak Memory Usage
- Maximum: 45MB
- Average: 12MB
```

---

## 8. NEXT STEPS

1. Create benchmark dataset (1-2 days)
2. Implement benchmark runner (1 day)
3. Run benchmarks (0.5 days)
4. Generate report (0.5 days)
5. Publish results (ongoing)

---

**End of Benchmark Plan**
