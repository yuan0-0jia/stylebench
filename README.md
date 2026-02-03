# StyleBench

Benchmarking coding agents across code style variants.

## Research Question

Do coding agents (Claude, GPT-4, Gemini, etc.) perform differently when fixing bugs in code written with different styles? StyleBench investigates:

1. **Style Sensitivity**: Do agents perform better on certain code styles?
2. **Style Consistency**: Do agents maintain or drift from original code style?
3. **Bug Detectability**: Are certain bugs easier to detect in specific styles?

## Quick Start

```bash
# 1. Clone repositories
git clone https://github.com/yuan0-0jia/stylebench.git
git clone https://github.com/yuan0-0jia/stylebench-data.git
cd stylebench

# 2. Install dependencies
uv sync

# 3. Run a coding agent on a pre-generated bug (coming Week 5)
# python benchmarks/runner.py --repo humanize --style camelcase --agent claude
```

## End-to-End Workflow

StyleBench has 4 stages. Stages 1-3 are complete; pre-generated data is in `stylebench-data/`.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: SETUP                                                          │
│  Clone target repos, verify tests pass                                   │
│  Output: stylebench-data/original/{repo}/                                │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: STYLE TRANSFORMATION                                           │
│  Transform code to different naming/formatting styles                    │
│  Output: stylebench-data/{style}/{repo}/                                 │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: BUG GENERATION                                                 │
│  Inject mutations, validate they cause test failures                     │
│  Output: stylebench-data/bugs/{repo}-{style}.json (991 total bugs)       │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: AGENT TESTING (Week 5)                                         │
│  Run coding agents on bugs, evaluate fix success                         │
│  Output: results/{agent}/{repo}-{style}-results.json                     │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Clone and Install

```bash
# Clone both repositories side by side
git clone https://github.com/yuan0-0jia/stylebench.git
git clone https://github.com/yuan0-0jia/stylebench-data.git

cd stylebench
uv sync
uv sync --extra dev  # For pytest, ruff
```

### Target Projects

StyleBench uses these Python projects (already cloned in `stylebench-data/original/`):

| Project | LOC | Tests | Description |
|---------|-----|-------|-------------|
| [humanize](https://github.com/python-humanize/humanize) | 1,650 | 684 | String humanization |
| [validators](https://github.com/python-validators/validators) | 3,144 | 878 | Input validation |
| [python-markdown](https://github.com/Python-Markdown/markdown) | 8,293 | 776 | Markdown parser |
| [more-itertools](https://github.com/more-itertools/more-itertools) | 6,822 | 701 | Extended itertools |

### Verify Tests Pass

```bash
cd ../stylebench-data/original/humanize
uv sync && uv run pytest tests/ -q
# Expected: 684 passed
```

---

## Stage 2: Style Transformation

Transform code to different naming conventions and formatting styles.

### Available Styles

| Style | Description | Example |
|-------|-------------|---------|
| `camelcase` | snake_case → camelCase | `get_user_name` → `getUserName` |
| `snakecase` | camelCase → snake_case | `getUserName` → `get_user_name` |
| `badnames` | Local vars → single letters | `result = x + y` → `a = x + y` |
| `formatting` | Compact ruff formatting | 79 chars, single quotes |

### Transform a Project

```bash
cd stylebench

# Transform to camelCase (--packages identifies project code vs dependencies)
python scripts/transform.py camelcase \
    ../stylebench-data/original/humanize \
    ../stylebench-data/camelcase/humanize \
    --packages humanize

# Transform to bad naming (no --packages needed)
python scripts/transform.py badnames \
    ../stylebench-data/original/humanize \
    ../stylebench-data/badnames/humanize

# Apply compact formatting
python scripts/transform.py formatting \
    ../stylebench-data/original/humanize \
    ../stylebench-data/formatting/humanize \
    --style compact
```

### Verify Transformation

```bash
cd ../stylebench-data/camelcase/humanize
uv sync && uv run pytest tests/ -q
# Expected: 681 passed (99.6% - minor failures from dynamic imports)
```

### Pre-Generated Style Variants

All 20 variants (4 repos × 5 styles) are already in `stylebench-data/`:

```
stylebench-data/
├── original/           # Unmodified source
├── camelcase/          # snake_case → camelCase
├── snakecase/          # camelCase → snake_case
├── badnames/           # Single-letter local variables
└── formatting/         # Compact formatting
```

---

## Stage 3: Bug Generation

Inject semantic mutations and validate they cause test failures.

### Mutation Types

| Type | Mutation | Example |
|------|----------|---------|
| `eq_ne` | `==` ↔ `!=` | `x == 0` → `x != 0` |
| `var_swap` | Swap variables | `return x` → `return y` |
| `lt_gt` | `<` ↔ `>` | `x < y` → `x > y` |
| `le_ge` | `<=` ↔ `>=` | `x <= y` → `x >= y` |
| `and_or` | `and` ↔ `or` | `a and b` → `a or b` |
| `plus_one` | `n` → `n+1` | `range(10)` → `range(11)` |
| `minus_one` | `n` → `n-1` | `range(10)` → `range(9)` |

### Generate Bug Catalog

```bash
cd stylebench

# Generate bugs for a single repo/style (30-50 validated bugs)
python scripts/generate_bugs.py humanize camelcase --count 50

# Generate for all 20 combinations
python scripts/generate_bugs.py --all --output ../stylebench-data/bugs/

# Serial execution (less memory)
python scripts/generate_bugs.py --all --workers 1
```

### Pre-Generated Bug Catalogs

**991 validated bugs** are already in `stylebench-data/bugs/`:

| Repo | Bugs per Style | Total |
|------|----------------|-------|
| humanize | 30 | 150 |
| validators | 30 | 150 |
| python-markdown | 50 | 250 |
| more-itertools | 30 | 150 |

**Mutation distribution**: eq_ne (45%), var_swap (16%), boundary (23%), other (16%)

### Bug Catalog Format

Each catalog has two files:

- `{repo}-{style}.json` - Full details (for scoring)
- `{repo}-{style}-agent.json` - Agent-visible only (no diff leakage)

Agent-visible data contains only test failure output:

```json
{
  "bugs": [
    {
      "bug_id": "humanize-camel-001",
      "test_output": "FAILED tests/test_time.py::test_naturaldelta - AssertionError...",
      "failing_tests": ["tests/test_time.py::test_naturaldelta"]
    }
  ]
}
```

The agent never sees: mutation location, original code, or the diff.

---

## Stage 4: Agent Testing (Coming Week 5)

Run coding agents on bugs and evaluate fix success.

### Planned Usage

```bash
# Run Claude on 10 bugs from humanize/camelcase
python benchmarks/runner.py \
    --repo humanize \
    --style camelcase \
    --agent claude \
    --count 10

# Run multiple agents for comparison
python benchmarks/runner.py \
    --repo humanize \
    --style original \
    --agent claude,gpt4,gemini \
    --count 50
```

### Evaluation Flow

1. Load bug from catalog
2. Apply mutation to create buggy repo state
3. Run agent with test failure output (no diff)
4. Apply agent's proposed fix
5. Run tests to score: PASS / FAIL / ERROR
6. Restore original code, repeat

---

## Project Structure

```
stylebench/
├── bugs/                  # Bug injection and validation
│   ├── injector.py        # Tree-sitter mutation injector
│   ├── validator.py       # Batch mutation testing
│   └── catalog.py         # Bug catalog generator
├── transformers/          # Code style transformers
│   ├── base.py            # Base transformer class
│   ├── naming.py          # CamelCase, SnakeCase, BadNaming
│   └── formatting.py      # Ruff formatting
├── benchmarks/            # Agent harness (Week 5)
├── scripts/
│   ├── transform.py       # CLI for style transformation
│   └── generate_bugs.py   # CLI for bug generation
├── data/                  # Symlinks to stylebench-data
└── tests/                 # Test suite
```

---

## API Reference

See [docs/BENCHMARKING.md](docs/BENCHMARKING.md) for a quick command reference.

### Bug Injection

```python
from bugs.injector import list_mutation_sites, apply_mutation

code = """
def check(x, y):
    if x < y:
        return x == 0
    return False
"""

# Find mutation sites
sites = list_mutation_sites(code)
for site in sites:
    print(f"Line {site.start_point[0]+1}: {site.original_text} → {site.mutated_text}")

# Apply a mutation
mutated = apply_mutation(code, sites[0])
```

### Style Transformation

```python
from transformers import CamelCaseTransformer, BadNamingTransformer

# Transform snake_case to camelCase
transformer = CamelCaseTransformer(project_packages={'humanize'})
result = transformer.transform(source_code)

# Transform a directory
transformer.transform_directory('src/', 'output/')
```

### Mutation Validation

```python
from bugs.validator import validate_mutations

report = validate_mutations(
    repo_path='path/to/repo',
    source_dir='src',
    max_mutations=50,
    verbose=True
)
print(report.summary())
```

---

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
