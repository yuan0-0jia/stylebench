#!/usr/bin/env python3
"""
Transform Python projects with different code styles.

Usage:
    python scripts/transform.py camelcase src/ output/ --packages mypackage
    python scripts/transform.py snakecase src/ output/ --packages mypackage
    python scripts/transform.py badnames src/ output/
    python scripts/transform.py formatting src/ output/ --style compact
"""

import argparse
import shutil
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from transformers import (
    BadNamingTransformer,
    CamelCaseTransformer,
    FormattingTransformer,
    SnakeCaseTransformer,
)
from transformers.naming import NameAnalyzer


def collect_project_context(src_dir: Path, project_packages: set[str]):
    """First pass: collect definitions and kwargs functions across all files."""
    all_definitions = set()
    kwargs_functions = set()

    temp_transformer = CamelCaseTransformer(project_packages=project_packages)

    for py_file in src_dir.rglob("*.py"):
        if should_skip(py_file):
            continue
        try:
            source = py_file.read_text()
            analyzer = NameAnalyzer(source.encode(), project_packages)
            root = temp_transformer.parse(source)
            ctx = analyzer.analyze(root)
            all_definitions.update(ctx.local_definitions)
            kwargs_functions.update(ctx.kwargs_functions)
        except Exception as e:
            print(f"  Warning: Could not analyze {py_file}: {e}")

    return all_definitions, kwargs_functions


def should_skip(py_file: Path) -> bool:
    """Check if file should be skipped."""
    skip_patterns = [".venv", "__pycache__", ".git", "node_modules", ".tox"]
    return any(p in str(py_file) for p in skip_patterns)


def transform_directory(transformer, src_dir: Path, dry_run: bool = False) -> int:
    """Transform all Python files in directory. Returns count of changed files."""
    files_changed = 0

    for py_file in src_dir.rglob("*.py"):
        if should_skip(py_file):
            continue
        try:
            result = transformer.transform_file(py_file)
            if result.changes_made > 0:
                files_changed += 1
                if not dry_run:
                    py_file.write_text(result.transformed_code)
        except Exception as e:
            print(f"  Warning: Could not transform {py_file}: {e}")

    return files_changed


def main():
    parser = argparse.ArgumentParser(
        description="Transform Python code styles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "transformer",
        choices=["camelcase", "snakecase", "badnames", "formatting"],
        help="Which transformer to use",
    )
    parser.add_argument("input", type=Path, help="Input directory")
    parser.add_argument("output", type=Path, help="Output directory")
    parser.add_argument(
        "--packages",
        nargs="+",
        default=[],
        help="Project package names (required for camelcase/snakecase)",
    )
    parser.add_argument(
        "--style",
        choices=["default", "compact", "wide", "pep8_strict"],
        default="compact",
        help="Formatting style (for formatting transformer)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without writing",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Transform input directory in place (no copy)",
    )

    args = parser.parse_args()

    # Validate input
    if not args.input.exists():
        print(f"Error: Input directory does not exist: {args.input}")
        sys.exit(1)

    if args.transformer in ["camelcase", "snakecase"] and not args.packages:
        print(f"Error: --packages required for {args.transformer} transformer")
        print("Example: --packages humanize")
        sys.exit(1)

    project_packages = set(args.packages)

    # Prepare output directory
    if args.in_place:
        work_dir = args.input
        print(f"Transforming in place: {work_dir}")
    else:
        if args.output.exists():
            print(f"Removing existing output: {args.output}")
            shutil.rmtree(args.output)
        print(f"Copying {args.input} -> {args.output}")
        shutil.copytree(
            args.input,
            args.output,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "*.pyc"),
        )
        work_dir = args.output

    # Create transformer
    print(f"\nApplying {args.transformer} transformer...")

    if args.transformer == "camelcase":
        definitions, kwargs_funcs = collect_project_context(work_dir, project_packages)
        print(f"  Collected {len(definitions)} definitions, {len(kwargs_funcs)} kwargs functions")
        transformer = CamelCaseTransformer(
            project_packages=project_packages,
            project_definitions=definitions,
            project_kwargs_functions=kwargs_funcs,
        )

    elif args.transformer == "snakecase":
        definitions, kwargs_funcs = collect_project_context(work_dir, project_packages)
        print(f"  Collected {len(definitions)} definitions, {len(kwargs_funcs)} kwargs functions")
        transformer = SnakeCaseTransformer(
            project_packages=project_packages,
            project_definitions=definitions,
            project_kwargs_functions=kwargs_funcs,
        )

    elif args.transformer == "badnames":
        transformer = BadNamingTransformer()

    elif args.transformer == "formatting":
        transformer = FormattingTransformer(style=args.style)
        print(f"  Using style: {args.style}")

    # Transform
    files_changed = transform_directory(transformer, work_dir, dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDry run: would transform {files_changed} files")
    else:
        print(f"\nTransformed {files_changed} files")
        print(f"Output: {work_dir}")


if __name__ == "__main__":
    main()
