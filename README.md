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
├── bugs/                  # Bug injection and validation
│   ├── injector.py        # Tree-sitter based mutation injector
│   └── validator.py       # Batch mutation testing
├── transformers/          # Code style transformation tools
│   ├── base.py            # Base transformer class
│   ├── naming.py          # Naming convention transformers
│   └── formatting.py      # Code formatting transformer
├── benchmarks/            # Agent harness and evaluation (planned)
├── data/
│   ├── original/          # Symlink to stylebench-data/original
│   ├── camelcase/         # Symlink to stylebench-data/camelcase
│   ├── badnames/          # Symlink to stylebench-data/badnames
│   ├── transformed/       # Working directory for transformations
│   └── results/           # Validation and agent performance data
├── analysis/              # Statistical analysis and visualization
└── tests/                 # Test suite
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repositories
git clone https://github.com/yuan0-0jia/stylebench.git
git clone https://github.com/yuan0-0jia/stylebench-data.git
cd stylebench

# Install dependencies
uv sync

# Install dev dependencies (pytest, ruff)
uv sync --extra dev

# Run tests
uv run pytest
```

## Data Repository

Pre-transformed style variants are available in [stylebench-data](https://github.com/yuan0-0jia/stylebench-data):

```
stylebench-data/
├── original/           # Unmodified source repositories
├── camelcase/          # snake_case → camelCase naming
├── snakecase/          # camelCase → snake_case (roundtrip)
├── badnames/           # Descriptive → single-letter local variables
└── formatting/         # Compact formatting (79 chars, single quotes)
```

The code repo has symlinks to the data repo for easy access:

```bash
# After cloning both repos side by side
cd stylebench
ls data/original/       # Points to ../stylebench-data/original
```

## Usage

> **Full guide**: See [docs/BENCHMARKING.md](docs/BENCHMARKING.md) for the complete benchmarking workflow.

### CLI: Transform Projects

Use the CLI script to transform entire projects:

```bash
# Transform to camelCase (requires --packages to identify project code)
python scripts/transform.py camelcase data/original/humanize output/humanize-camel --packages humanize

# Transform to snake_case (reverses camelCase)
python scripts/transform.py snakecase output/humanize-camel output/humanize-snake --packages humanize

# Transform to bad naming (single-letter locals)
python scripts/transform.py badnames data/original/humanize output/humanize-bad

# Apply compact formatting
python scripts/transform.py formatting data/original/humanize output/humanize-fmt --style compact

# Dry run (show changes without writing)
python scripts/transform.py camelcase data/original/humanize output/ --packages humanize --dry-run

# Transform in place (modifies input directory)
python scripts/transform.py formatting output/humanize --in-place --style wide
```

**Formatting styles**: `default`, `compact` (79 chars, single quotes), `wide` (120 chars), `pep8_strict`

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

### Code Style Transformation

Transform code between different naming conventions and formatting styles:

```python
from transformers import CamelCaseTransformer, SnakeCaseTransformer, BadNamingTransformer

# Transform snake_case to camelCase
transformer = CamelCaseTransformer(project_packages={'myproject'})
result = transformer.transform(source_code)
print(result.transformed)

# Transform a directory
transformer.transform_directory('src/', 'output/')
```

**Naming transformers:**

| Transformer | Conversion | Example |
|-------------|------------|---------|
| `CamelCaseTransformer` | snake_case → camelCase | `get_user_name` → `getUserName` |
| `SnakeCaseTransformer` | camelCase → snake_case | `getUserName` → `get_user_name` |
| `BadNamingTransformer` | Local vars → single-letter | `result = x + y` → `a = x + y` |

**BadNaming transformer details:**
- Only renames local variables within function scopes
- Preserves parameters (visible to callers) and class attributes
- Handles closures: renames outer variables referenced in nested functions
- Prevents collisions: nested functions avoid parent scope's new names

**Formatting transformer:**

```python
from transformers import FormattingTransformer

# Apply ruff formatting with different profiles
transformer = FormattingTransformer(profile='compact')  # or 'pep8_strict', 'wide'
result = transformer.transform(source_code)
```

**Transformer features:**
- Two-pass transformation for cross-file consistency
- Preserves external imports, builtins, and dunder methods
- Syncs format string placeholders with kwargs
- Detects `**kwargs` functions to preserve caller kwargs
- Preserves submodule names in attribute chains

**Validation results on target projects:**

| Project | Original | CamelCase | SnakeCase | BadNaming | Formatting |
|---------|----------|-----------|-----------|-----------|------------|
| humanize | 684 pass | 99.6% | 100% | 100% | 100% |
| validators | 878 pass | 98.1% | 100% | 100% | 100% |
| python-markdown | 776 pass | 100% | 100% | 100% | 100% |
| more-itertools | 701 pass | 98.9% | 99.9% | 100% | 100% |

*Note: Pass rates compare transformed code test results to original. CamelCase/SnakeCase minor failures are due to dynamic imports (`__import__`) that can't be tracked statically.*

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
