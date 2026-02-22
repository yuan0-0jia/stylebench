# Quick Reference

Command reference for StyleBench. See [README.md](../README.md) for the full workflow.

## Running the Benchmark

```bash
# Full 1120-trial benchmark (20 bugs × 7 styles × 4 repos × 2 modes)
python scripts/run_benchmark.py --catalog-dir bugs_canonical

# Resume after rate limiting (automatic)
python scripts/run_benchmark.py --catalog-dir bugs_canonical

# Specific agent/model
python scripts/run_benchmark.py --catalog-dir bugs_canonical --agent claude --model haiku
python scripts/run_benchmark.py --catalog-dir bugs_canonical --agent gemini

# Specific repos or modes
python scripts/run_benchmark.py --catalog-dir bugs_canonical --repos humanize validators
python scripts/run_benchmark.py --catalog-dir bugs_canonical --mode without_tests

# Reset progress
python scripts/run_benchmark.py --catalog-dir bugs_canonical --reset

# Skip confirmation prompts
python scripts/run_benchmark.py --catalog-dir bugs_canonical --yes

# Run in parallel (8 instances, one per repo/mode)
python scripts/run_benchmark.py --catalog-dir bugs_canonical --repos humanize --mode with_tests --run-suffix _humanize_wt
python scripts/run_benchmark.py --catalog-dir bugs_canonical --repos humanize --mode without_tests --run-suffix _humanize_wot
# (each instance maintains its own state in benchmark_{agent}_{suffix}/)
```

## Running Individual Batches

```bash
# Single batch via runner module
uv run python -m benchmarks.runner \
    --catalog ../stylebench-data/bugs_canonical/humanize-original.json \
    --repo ../stylebench-data/original/humanize \
    --repo-name humanize \
    --agent claude \
    --mode with_tests

# Specific bugs
uv run python -m benchmarks.runner ... --bugs humanize-original-001 humanize-original-002

# Options
--timeout 300        # Agent timeout (seconds)
--test-timeout 120   # Test timeout (seconds)
--max-turns 10       # Max agentic turns
--model haiku        # Specific model
--trial-delay 30     # Delay between trials (for rate limiting)
--manifest FILE      # Use pre-captured test output
--quiet              # Suppress progress output
```

## Style Transformation

```bash
# camelCase (requires --packages)
python scripts/transform.py camelcase INPUT OUTPUT --packages PKG

# snakeCase (requires --packages)
python scripts/transform.py snakecase INPUT OUTPUT --packages PKG

# badnames (no --packages needed)
python scripts/transform.py badnames INPUT OUTPUT

# formatting
python scripts/transform.py formatting INPUT OUTPUT --style compact|wide|pep8_strict

# nodocstrings (remove docstrings)
python scripts/transform.py nodocstrings INPUT OUTPUT

# nodocs_full (remove docstrings + comments)
python scripts/transform.py nodocs_full INPUT OUTPUT

# Options
--dry-run       # Preview without writing
--in-place      # Modify input directory
```

## Bug Generation

```bash
# Single repo/style
python scripts/generate_bugs.py humanize camelcase --count 50

# All 5 styles for one repo
python scripts/generate_bugs.py humanize --all-styles --count 50

# All 20 combinations
python scripts/generate_bugs.py --all --output ../stylebench-data/bugs/

# Options
--workers N     # Parallel workers (default: 2)
--no-parallel   # Serial execution
--count N       # Target bug count (default: 50)
--output DIR    # Output directory
```

**Repos**: `humanize`, `validators`, `python-markdown`, `more-itertools`

**Styles**: `original`, `camelcase`, `snakecase`, `badnames`, `formatting`, `nodocstrings`, `nodocs_full`

## Repository Config

| Repo | Source Dir | Test Command |
|------|------------|--------------|
| humanize | `src/humanize` | `uv run pytest tests/ -x -q` |
| validators | `src/validators` | `uv run pytest tests/ -x -q` |
| python-markdown | `markdown` | `uv run pytest tests/ -x -q` |
| more-itertools | `more_itertools` | `uv run pytest tests/ -x -q` |

## Mutation Types

| Type | Mutation | Example |
|------|----------|---------|
| `eq_ne` | `==` ↔ `!=` | `x == 0` → `x != 0` |
| `var_swap` | Swap variables | `return x` → `return y` |
| `add_sub` | `+` ↔ `-` | `x + 1` → `x - 1` |
| `and_or` | `and` ↔ `or` | `a and b` → `a or b` |
| `if_else_swap` | Swap if/else | Invert branch logic |
| `in_not_in` | `in` ↔ `not in` | `x in lst` → `x not in lst` |
| `plus_one` | `n` → `n+1` | `range(10)` → `range(11)` |
| `true_false` | `True` ↔ `False` | `return True` → `return False` |
| `return_none` | Return None | `return val` → `return None` |

## Bug Catalog Files

```
stylebench-data/
├── bugs/                  # Ad-hoc catalogs (872 bugs, for development)
│   ├── humanize-original.json
│   └── ...
└── bugs_canonical/        # Canonical catalogs (560 bugs, for benchmark)
    ├── humanize-original.json
    ├── humanize-camelcase.json
    ├── humanize-nodocstrings.json
    └── ...                # 28 catalogs (4 repos × 7 styles), 20 bugs each
```

## Python API

```python
# Bug injection
from bugs.injector import list_mutation_sites, apply_mutation
sites = list_mutation_sites(code)
mutated = apply_mutation(code, sites[0])

# Validation
from bugs.validator import validate_mutations
report = validate_mutations(repo_path, source_dir, max_mutations=50)

# Catalog generation
from bugs.catalog import BugCatalogGenerator
gen = BugCatalogGenerator(repo_path, source_dir, test_cmd)
catalog = gen.generate(target_count=50)
catalog.save('output.json')

# Style transformation
from transformers import CamelCaseTransformer
t = CamelCaseTransformer(project_packages={'pkg'})
t.transform_directory('src/', 'output/')

# Benchmarking
from benchmarks import BenchmarkHarness, ClaudeAgent
harness = BenchmarkHarness(catalog_path, repo_path, repo_name)
agent = ClaudeAgent(timeout=300, max_turns=10, model="haiku")
result = harness.run_trial(agent, bug_id="humanize-original-001", mode="with_tests")
```
