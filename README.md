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
├── transformers/     # Code style transformation tools
├── bugs/             # Bug injection and validation
├── benchmarks/       # Agent harness and evaluation
├── data/
│   ├── original/     # Source Python projects
│   ├── transformed/  # Style-transformed variants
│   └── results/      # Agent performance data
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

*Coming soon* - Bug injection MVP and agent harness are under development.

## License

MIT
