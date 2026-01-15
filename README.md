# StyleBench

Benchmarking coding agents across code style variants.

## Research Question

Do coding agents (Claude, GPT-4, Gemini, etc.) perform differently when fixing bugs in code written with different styles? StyleBench investigates:

1. **Style Sensitivity**: Do agents perform better on certain code styles?
2. **Style Consistency**: Do agents maintain or drift from original code style?
3. **Bug Detectability**: Are certain bugs easier to detect in specific styles?

## Project Structure

```
stylebench/
├── bugs/             # Bug injection and validation
│   ├── injector.py   # Tree-sitter based mutation injector
│   └── validator.py  # Batch mutation testing
├── transformers/     # Code style transformation tools (planned)
├── benchmarks/       # Agent harness and evaluation (planned)
├── data/
│   ├── original/     # Source Python projects (gitignored)
│   ├── transformed/  # Style-transformed variants
│   └── results/      # Validation and agent performance data
├── analysis/         # Statistical analysis and visualization
└── tests/            # Test suite
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repository
git clone https://github.com/yuan0-0jia/stylebench.git
cd stylebench

# Install dependencies
uv sync

# Install dev dependencies (pytest, ruff)
uv sync --extra dev

# Run tests
uv run pytest
```

## Usage

### Bug Injection

The injector finds mutation sites in Python code and applies semantic mutations:

```python
from bugs.injector import list_mutation_sites, apply_mutation

code = """
def check_value(x, y):
    if x < y and y <= 10:
        return x == 0
    return False
"""

# Find all mutation sites
sites = list_mutation_sites(code)
print(f"Found {len(sites)} mutation sites")

for site in sites:
    print(f"  Line {site.start_point[0]+1}: '{site.original_text}' → '{site.mutated_text}'")

# Apply a mutation
mutated_code = apply_mutation(code, sites[0])
```

**Mutation types supported:**

| Type | Mutation | Example |
|------|----------|---------|
| `lt_gt` | `<` ↔ `>` | `x < y` → `x > y` |
| `le_ge` | `<=` ↔ `>=` | `x <= y` → `x >= y` |
| `eq_ne` | `==` ↔ `!=` | `x == 0` → `x != 0` |
| `and_or` | `and` ↔ `or` | `a and b` → `a or b` |
| `plus_one` | `n` → `n+1` | `range(10)` → `range(11)` |
| `minus_one` | `n` → `n-1` | `range(10)` → `range(9)` |

### Mutation Validation

The validator tests mutations against a project's test suite:

```python
from bugs.validator import validate_mutations

# Validate mutations on a repository
report = validate_mutations(
    repo_path='path/to/repo',
    source_dir='src',
    max_mutations=50,
    verbose=True
)

print(report.summary())
# Mutation score: % of mutations that cause test failures (higher = better test coverage)
```

**Example output:**
```
Mutation Validation Report
==========================
Repository: /path/to/repo
Total mutations tested: 50
  Killed: 48
  Survived: 2
Mutation score: 96.0%
```

### Target Projects

StyleBench uses these Python projects for benchmarking:

| Project | LOC | Tests | Description |
|---------|-----|-------|-------------|
| [humanize](https://github.com/python-humanize/humanize) | 1,650 | 737 | String humanization |
| [validators](https://github.com/python-validators/validators) | 3,144 | 895 | Input validation |
| [more-itertools](https://github.com/more-itertools/more-itertools) | 6,822 | 701 | Extended itertools |
| [python-markdown](https://github.com/Python-Markdown/markdown) | 8,293 | 775 | Markdown parser |

## Development

```bash
# Run linter
uv run ruff check .

# Run tests
uv run pytest -v

# Run tests with coverage
uv run pytest --cov=bugs
```

## License

MIT
