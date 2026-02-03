# Quick Reference

Command reference for StyleBench. See [README.md](../README.md) for the full workflow.

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

# Options
--dry-run       # Preview without writing
--in-place      # Modify input directory
```

## Bug Generation

```bash
# Single repo/style
python scripts/generate_bugs.py REPO STYLE --count 50

# All 20 combinations
python scripts/generate_bugs.py --all --output ../stylebench-data/bugs/

# Options
--workers N     # Parallel workers (default: 2)
--no-parallel   # Serial execution
```

## Repository Config

| Repo | Source Dir | Test Command |
|------|------------|--------------|
| humanize | `src/humanize` | `uv run pytest tests/ -x -q` |
| validators | `src/validators` | `uv run pytest tests/ -x -q` |
| python-markdown | `markdown` | `uv run pytest tests/ -x -q` |
| more-itertools | `more_itertools` | `uv run pytest tests/ -x -q` |

## Mutation Types

| Type | Priority | Kill Rate |
|------|----------|-----------|
| `eq_ne` | 1 | Very high |
| `var_swap` | 2 | High |
| `true_false` | 3 | High |
| `lt_gt` | 4 | Medium |
| `le_ge` | 5 | Medium |
| `and_or` | 6 | Medium |
| `plus_one` | 7 | Medium |
| `minus_one` | 8 | Medium |

## Bug Catalog Files

```
stylebench-data/bugs/
├── humanize-original.json        # Full details (for scoring)
├── humanize-original-agent.json  # Agent-visible only (no diff)
├── humanize-camelcase.json
├── humanize-camelcase-agent.json
└── ...
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
```
