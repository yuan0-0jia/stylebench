#!/usr/bin/env python3
"""Filter bug catalogs to only include bugs that cause test failures."""

import json
import shutil
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.evaluator import (
    apply_bug,
    create_working_copy,
    load_bug_catalog,
    run_tests,
)


def validate_bug(repo_path: Path, repo_name: str, hidden: dict, timeout: int = 60) -> bool:
    """Check if a bug causes test failures."""
    work_dir = create_working_copy(repo_path)
    try:
        if not apply_bug(work_dir, hidden):
            return False
        result = run_tests(work_dir, repo_name, timeout=timeout)
        return result.exit_code != 0 and result.failed > 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def filter_catalog(catalog_path: Path, repo_path: Path, repo_name: str, output_path: Path) -> dict:
    """Filter a catalog to only include valid bugs."""
    catalog = load_bug_catalog(catalog_path)

    valid_bugs = []
    valid_hidden = []
    invalid_count = 0

    total = len(catalog["bugs"])
    for i, (bug, hidden) in enumerate(zip(catalog["bugs"], catalog["_hidden"])):
        bug_id = bug["bug_id"]
        print(f"  [{i + 1}/{total}] Checking {bug_id}...", end=" ", flush=True)

        if validate_bug(repo_path, repo_name, hidden):
            valid_bugs.append(bug)
            valid_hidden.append(hidden)
            print("VALID")
        else:
            invalid_count += 1
            print("INVALID")

    # Create filtered catalog
    filtered = {
        "bugs": valid_bugs,
        "_hidden": valid_hidden,
    }

    # Save filtered catalog
    with open(output_path, "w") as f:
        json.dump(filtered, f, indent=2)

    return {
        "total": total,
        "valid": len(valid_bugs),
        "invalid": invalid_count,
    }


def main():
    """Filter all original catalogs."""
    configs = [
        ("humanize", "humanize-original.json"),
        ("validators", "validators-original.json"),
        ("python-markdown", "python-markdown-original.json"),
        ("more-itertools", "more-itertools-original.json"),
    ]

    bugs_dir = Path("/Users/yuan/stylebench-data/bugs")
    repos_dir = Path("/Users/yuan/stylebench-data/original")

    results = {}

    for repo_name, catalog_file in configs:
        print(f"\n=== {repo_name} ===")

        catalog_path = bugs_dir / catalog_file
        repo_path = repos_dir / repo_name
        output_path = bugs_dir / catalog_file.replace(".json", "-validated.json")

        if not catalog_path.exists():
            print(f"  Catalog not found: {catalog_path}")
            continue

        stats = filter_catalog(catalog_path, repo_path, repo_name, output_path)
        results[repo_name] = stats

        print(f"  Result: {stats['valid']}/{stats['total']} valid bugs")
        print(f"  Saved to: {output_path}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for repo_name, stats in results.items():
        pct = 100 * stats["valid"] / stats["total"] if stats["total"] > 0 else 0
        print(f"  {repo_name}: {stats['valid']}/{stats['total']} ({pct:.0f}%)")


if __name__ == "__main__":
    main()
