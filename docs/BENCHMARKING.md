# Benchmarking Guide

This guide explains how to run the full StyleBench benchmarking pipeline.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│ 1. SETUP - Clone target repo, install dependencies         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. INJECT - Find mutation sites, create buggy versions     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. VALIDATE - Run tests, check if mutations are detected   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. REPORT - Generate mutation score and JSON results       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. AGENT FIX - (Coming soon) Run agents on buggy code      │
└─────────────────────────────────────────────────────────────┘
```

## Step 1: Setup Target Project

Clone one of the target repositories into `data/original/`:

```bash
cd ~/stylebench/data/original

# Clone humanize (recommended for testing - fast, good coverage)
git clone --depth 1 https://github.com/python-humanize/humanize.git

# Or clone other targets:
# git clone --depth 1 https://github.com/python-validators/validators.git
# git clone --depth 1 https://github.com/more-itertools/more-itertools.git
# git clone --depth 1 https://github.com/Python-Markdown/markdown.git
```

Install the project's dependencies:

```bash
cd humanize
uv venv --python 3.11
uv pip install -e ".[tests]"

# Verify tests pass
uv run pytest -q
```

## Step 2: Find Mutation Sites

Use the injector to find all mutable locations in the source code:

```python
from pathlib import Path
from bugs.injector import list_mutation_sites, MutationType

# Load source file
code = Path('data/original/humanize/src/humanize/time.py').read_text()

# Find all mutation sites
sites = list_mutation_sites(code)
print(f"Found {len(sites)} mutation sites")

# View mutations by type
for site in sites[:10]:
    print(f"Line {site.start_point[0]+1}: {site.mutation_type.value}")
    print(f"  '{site.original_text}' → '{site.mutated_text}'")
    print(f"  Context: {site.context[:60]}...")
```

### Mutation Types

| Type | Mutation | Example |
|------|----------|---------|
| `lt_gt` | `<` ↔ `>` | `if x < y` → `if x > y` |
| `le_ge` | `<=` ↔ `>=` | `if x <= y` → `if x >= y` |
| `eq_ne` | `==` ↔ `!=` | `if x == 0` → `if x != 0` |
| `and_or` | `and` ↔ `or` | `if a and b` → `if a or b` |
| `plus_one` | `n` → `n+1` | `range(10)` → `range(11)` |
| `minus_one` | `n` → `n-1` | `range(10)` → `range(9)` |

## Step 3: Apply a Single Mutation

```python
from bugs.injector import list_mutation_sites, apply_mutation

code = Path('data/original/humanize/src/humanize/time.py').read_text()
sites = list_mutation_sites(code)

# Apply first mutation
mutated_code = apply_mutation(code, sites[0])

# Show the diff
print(f"Original: ...{sites[0].original_text}...")
print(f"Mutated:  ...{sites[0].mutated_text}...")
```

## Step 4: Batch Validation

The validator automatically tests multiple mutations:

```python
from bugs.validator import validate_mutations

report = validate_mutations(
    repo_path='data/original/humanize',
    source_dir='src/humanize',
    max_mutations=50,      # Limit mutations to test
    verbose=True           # Print progress
)

print(report.summary())
```

### Understanding Results

- **Killed**: Mutation caused test failure (good - tests detect the bug)
- **Survived**: Mutation did NOT cause test failure (bad - bug went undetected)
- **Mutation Score**: `killed / (killed + survived)` - higher is better

Example output:
```
Mutation Validation Report
==========================
Repository: /path/to/humanize
Total mutations tested: 50
  Killed: 48
  Survived: 2
Mutation score: 96.0%

By mutation type:
  and_or: 6/6 killed (100%)
  eq_ne: 4/5 killed (80%)
  lt_gt: 3/3 killed (100%)
  minus_one: 17/18 killed (94%)
  plus_one: 18/18 killed (100%)
```

## Step 5: Save Results

Results are automatically saved to `data/results/`:

```python
from pathlib import Path

# Save JSON report
results_file = Path('data/results/humanize_validation.json')
results_file.write_text(report.to_json())

# Load later
import json
data = json.loads(results_file.read_text())
```

## Advanced: Custom Validation

Use the `Validator` class for more control:

```python
from bugs.validator import Validator
from bugs.injector import MutationType

validator = Validator(
    repo_path='data/original/humanize',
    test_command=['uv', 'run', 'pytest', '-x', '-q'],  # Custom test command
    timeout=120  # Timeout per test run
)

# Validate specific file
results = validator.validate_file(
    'src/humanize/time.py',
    mutation_types=[MutationType.COMPARISON_LT_GT, MutationType.BOOLEAN_AND_OR],
    max_mutations=10
)

# Validate entire repo
report = validator.validate_repo(
    source_dir='src/humanize',
    file_pattern='*.py',
    max_mutations_per_file=20,
    max_total_mutations=100
)
```

## Target Projects

| Project | GitHub | LOC | Tests | Runtime |
|---------|--------|-----|-------|---------|
| humanize | [python-humanize/humanize](https://github.com/python-humanize/humanize) | 1,650 | 737 | 0.7s |
| validators | [python-validators/validators](https://github.com/python-validators/validators) | 3,144 | 895 | 0.5s |
| more-itertools | [more-itertools/more-itertools](https://github.com/more-itertools/more-itertools) | 6,822 | 701 | 9s |
| python-markdown | [Python-Markdown/markdown](https://github.com/Python-Markdown/markdown) | 8,293 | 775 | 1.1s |

## Next Steps

The agent harness (coming soon) will:
1. Inject a validated bug
2. Capture test failure output
3. Send to coding agent (Claude, GPT-4, etc.)
4. Apply agent's fix
5. Re-run tests to score success
