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

# 3. Run the benchmark (uses canonical bug catalogs, resumes on rate limit)
python scripts/run_benchmark.py --catalog-dir bugs_canonical

# 4. Run a single batch manually
uv run python -m benchmarks.runner \
    --catalog ../stylebench-data/bugs_canonical/humanize-original.json \
    --repo ../stylebench-data/original/humanize \
    --repo-name humanize \
    --agent claude --mode with_tests
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
│  Output: stylebench-data/bugs_canonical/{repo}-{style}.json              │
└──────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: AGENT TESTING                                                  │
│  Run coding agents on bugs, evaluate fix success                         │
│  Output: stylebench-data/results/benchmark_{agent}_{repo}_{mode}/                      │
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

---

## Stage 2: Style Transformation

Transform code to different naming conventions and formatting styles.

### Available Styles

| Style | Description | Example |
|-------|-------------|---------|
| `camelcase` | snake_case → camelCase | `get_user_name` → `getUserName` |
| `badnames` | Local vars → single letters | `result = x + y` → `a = x + y` |
| `formatting` | Ruff default formatting | 88-char lines, double quotes |
| `nodocstrings` | Remove all docstrings | Module/class/function docstrings stripped |
| `nodocs_full` | Remove all documentation | Docstrings + inline comments stripped |

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

# Apply ruff formatting
python scripts/transform.py formatting \
    ../stylebench-data/original/humanize \
    ../stylebench-data/formatting/humanize

# Remove docstrings
python scripts/transform.py nodocstrings \
    ../stylebench-data/original/humanize \
    ../stylebench-data/nodocstrings/humanize

# Remove all documentation (docstrings + comments)
python scripts/transform.py nodocs_full \
    ../stylebench-data/original/humanize \
    ../stylebench-data/nodocs_full/humanize
```

### Pre-Generated Style Variants

All 24 variants (4 repos × 6 styles) are already in `stylebench-data/`:

```
stylebench-data/
├── original/           # Unmodified source
├── camelcase/          # snake_case → camelCase
├── badnames/           # Single-letter local variables
├── formatting/         # Ruff default formatting
├── nodocstrings/       # Docstrings removed
└── nodocs_full/        # All documentation removed
```

---

## Stage 3: Bug Generation

Inject semantic mutations and validate they cause test failures.

### Mutation Types

| Type | Mutation | Example |
|------|----------|---------|
| `eq_ne` | `==` ↔ `!=` | `x == 0` → `x != 0` |
| `lt_gt` | `<` ↔ `>` | `x < 10` → `x > 10` |
| `le_ge` | `<=` ↔ `>=` | `x <= 10` → `x >= 10` |
| `var_swap` | Swap variables | `return x` → `return y` |
| `add_sub` | `+` ↔ `-` | `x + 1` → `x - 1` |
| `mul_div` | `*` ↔ `/` | `x * 2` → `x / 2` |
| `and_or` | `and` ↔ `or` | `a and b` → `a or b` |
| `if_else_swap` | Swap if/else | Invert branch logic |
| `in_not_in` | `in` ↔ `not in` | `x in lst` → `x not in lst` |
| `is_is_not` | `is` ↔ `is not` | `x is None` → `x is not None` |
| `plus_one` | `n` → `n+1` | `range(10)` → `range(11)` |
| `minus_one` | `n` → `n-1` | `range(10)` → `range(9)` |
| `true_false` | `True` ↔ `False` | `return True` → `return False` |
| `return_none` | Return None | `return val` → `return None` |

### Canonical Bug Catalogs

For the benchmark, we use **canonical catalogs** (`bugs_canonical/`) where the same logical mutation is applied consistently across all 6 style variants. This ensures fair comparison across styles.

- 24 catalogs (4 repos × 6 styles), 40 bugs each = **960 bugs**
- All bugs have `line_number` and `context` for precise application
- 14 mutation types across all repos: eq_ne, lt_gt, le_ge, var_swap, add_sub, mul_div, and_or, if_else_swap, in_not_in, is_is_not, plus_one, minus_one, true_false, return_none
- Bugs 001-020: original set; bugs 021-040: naming-changed set (bug lines guaranteed different in camelcase/badnames vs original)
- Doc styles (nodocstrings, nodocs_full) extended via `scripts/extend_catalogs.py`

```bash
# Generate canonical catalogs for 5 naming/formatting styles (already done)
python scripts/generate_canonical_bugs.py --all

# Extend to new style variants (e.g., doc styles)
python scripts/extend_catalogs.py --styles nodocstrings nodocs_full

# Generate ad-hoc bugs for a single repo/style
python scripts/generate_bugs.py humanize camelcase --count 50
```

### Legacy Bug Catalogs

The `bugs/` directory contains **762 ad-hoc validated bugs** (used for development/testing, not the benchmark):

| Repo | Bugs (across 4 styles) |
|------|------------------------|
| humanize | 196 |
| validators | 168 |
| python-markdown | 198 |
| more-itertools | 200 |

---

## Stage 4: Agent Testing

Run coding agents on bugs and evaluate fix success.

### Running the Full Benchmark

The benchmark runner handles rate limiting, resumption, and per-bug progress tracking:

```bash
# Run the full 1920-trial benchmark (40 bugs × 6 styles × 4 repos × 2 modes)
python scripts/run_benchmark.py --catalog-dir bugs_canonical

# Resume after rate limiting (automatically picks up where it left off)
python scripts/run_benchmark.py --catalog-dir bugs_canonical

# Run with a specific agent/model
python scripts/run_benchmark.py --catalog-dir bugs_canonical --agent claude --model haiku

# Run specific repos or modes only
python scripts/run_benchmark.py --catalog-dir bugs_canonical --repos humanize validators
python scripts/run_benchmark.py --catalog-dir bugs_canonical --mode without_tests

# Reset progress and start fresh
python scripts/run_benchmark.py --catalog-dir bugs_canonical --reset
```

### Running Individual Trials

```bash
# Run a single batch via the runner module
uv run python -m benchmarks.runner \
    --catalog ../stylebench-data/bugs_canonical/humanize-original.json \
    --repo ../stylebench-data/original/humanize \
    --repo-name humanize \
    --agent claude \
    --mode with_tests \
    --bugs humanize-original-001 humanize-original-002

# Use gemini agent
uv run python -m benchmarks.runner \
    --catalog ../stylebench-data/bugs_canonical/validators-camelcase.json \
    --repo ../stylebench-data/camelcase/validators \
    --repo-name validators \
    --agent gemini \
    --mode without_tests
```

### Test Access Modes

| Mode | Description |
|------|-------------|
| `with_tests` | Agent receives test failure output and can read (but not modify) test files |
| `without_tests` | Agent receives test failure output but test files are hidden from the repo |

### Rate Limit Handling

The harness detects rate-limited API responses and handles them cleanly:

1. Rate-limited trials are **not** saved to result files
2. The `hit_rate_limit` metadata flag signals the script to stop
3. Completed bugs are saved to state; rate-limited bugs remain pending
4. Re-running the script resumes from where it left off

### Evaluation Flow

1. Create working copy of styled repo (excludes `.git` to prevent diff cheating)
2. Apply mutation from catalog (line-number-based, with context fallback)
3. Run tests to verify bug is active
4. Protect tests (hide for `without_tests`, lock read-only for `with_tests`)
5. Run agent with test failure output
6. Restore tests, run tests on agent's fix
7. Score: PASS / FAIL / ERROR / TIMEOUT / NO_FIX

### Full Benchmark Results (1920 trials, Claude Haiku 4.5)

| Metric | Value |
|--------|-------|
| Overall pass rate | 84.9% (1631/1920) |
| with_tests | 89.3% (857/960) |
| without_tests | 80.6% (774/960) |
| Mode gap | 8.7pp |

**By repo** (combined modes): validators 96%, humanize 92%, more-itertools 84%, python-markdown 68%

**By style** (combined modes, 320 trials each):

| Style | with_tests | without_tests | Combined |
|-------|-----------|---------------|---------|
| original | 89.4% | 81.2% | 85.3% |
| camelcase | 91.9% | 78.8% | 85.3% |
| badnames | 88.8% | 82.5% | 85.6% |
| formatting | 88.1% | 81.2% | 84.7% |
| nodocstrings | 88.1% | 80.6% | 84.4% |
| nodocs_full | 89.4% | 79.4% | 84.4% |

**By mutation type** (combined modes, all 14 types):

| Mutation | Combined | Mutation | Combined |
|----------|---------|----------|---------|
| `var_swap` | 99% | `le_ge` | 85% |
| `plus_one` | 96% | `mul_div` | 83% |
| `eq_ne` | 91% | `lt_gt` | 82% |
| `and_or` | 92% | `minus_one` | 82% |
| `return_none` | 89% | `if_else_swap` | 80% |
| `true_false` | 89% | `is_is_not` | 76% |
| `in_not_in` | 88% | `add_sub` | 70% |

**Key findings**:
- Style has **no statistically significant effect** (p = 0.998, Cramer's V = 0.012; 1pp range across all styles)
- Repository difficulty is the dominant factor (28pp range: python-markdown 68% → validators 96%)
- Mutation type spans 29pp: `var_swap` 99% → `add_sub` 70%
- Mode gap is repo-dependent: python-markdown 22pp, more-itertools 7pp, humanize 4pp, validators 1pp
- Badnames (single-letter variables) does **not** hurt fix rates vs original

---

## Project Structure

```
stylebench/
├── bugs/                  # Bug injection and validation
│   ├── injector.py        # Tree-sitter mutation injector
│   ├── validator.py       # Batch mutation testing
│   ├── catalog.py         # Bug catalog generator
│   └── repo_config.py     # Per-repo test configuration
├── transformers/          # Code style transformers
│   ├── base.py            # Base transformer class
│   ├── naming.py          # CamelCase, SnakeCase, BadNaming
│   ├── formatting.py      # Ruff formatting
│   └── docs.py            # Docstring/comment removal
├── benchmarks/            # Agent harness
│   ├── agents/            # Agent implementations
│   │   ├── base.py        # Agent ABC, BugContext, FixResult, TrialResult
│   │   ├── claude.py      # Claude Code CLI agent
│   │   ├── codex.py       # Codex CLI agent
│   │   └── gemini.py      # Gemini CLI agent
│   ├── evaluator.py       # Test running, bug application, file hashing
│   ├── harness.py         # Trial orchestration, manifest mode
│   └── runner.py          # CLI for running benchmark batches
├── scripts/
│   ├── run_benchmark.py   # Full benchmark with rate-limit handling + resumption
│   ├── transform.py       # CLI for style transformation
│   ├── generate_bugs.py   # CLI for ad-hoc bug generation
│   ├── generate_canonical_bugs.py  # Canonical bug generation (mapped across styles)
│   └── extend_catalogs.py # Extend catalogs to new style variants
├── tests/                 # Test suite (85+ tests)
└── docs/
    └── BENCHMARKING.md    # Quick command reference
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
