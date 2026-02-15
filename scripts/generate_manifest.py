#!/usr/bin/env python3
"""Generate a trial manifest for controlled benchmarking.

Creates a JSON manifest that defines exactly which bugs to run, with
pre-captured test outputs. All agent/model runs use the same manifest
to ensure controlled, reproducible comparisons.

Usage:
    # Generate pilot manifest (10 bugs per style)
    python scripts/generate_manifest.py --limit 10

    # Generate full manifest (all validated bugs)
    python scripts/generate_manifest.py

    # Custom output path
    python scripts/generate_manifest.py --limit 10 --output my_manifest.json
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("/Users/yuan/stylebench-data")

ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
ALL_STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]


def load_catalog_bugs(repo: str, style: str, limit: int | None = None, catalog_dir: str = "bugs") -> list[dict]:
    """Load bugs from a catalog file."""
    catalog_path = DATA_DIR / catalog_dir / f"{repo}-{style}.json"
    if not catalog_path.exists():
        return []

    with open(catalog_path) as f:
        data = json.load(f)

    bugs = data.get("bugs", [])
    if limit:
        bugs = bugs[:limit]
    return bugs


def main():
    parser = argparse.ArgumentParser(description="Generate trial manifest")
    parser.add_argument(
        "--limit", type=int, default=None, help="Bugs per repo-style combo (default: all)"
    )
    parser.add_argument(
        "--repos", nargs="+", default=ALL_REPOS, help="Repos to include"
    )
    parser.add_argument(
        "--styles", nargs="+", default=ALL_STYLES, help="Styles to include"
    )
    parser.add_argument(
        "--catalog-dir",
        default="bugs",
        help="Bug catalog subdirectory under data dir (default: bugs, use bugs_canonical for canonical)",
    )
    parser.add_argument(
        "--output", type=str, default=None, help="Output path (default: auto-named in stylebench-data)"
    )
    args = parser.parse_args()

    manifest = {
        "version": 1,
        "created": datetime.now().isoformat(),
        "config": {
            "repos": args.repos,
            "styles": args.styles,
            "bugs_per_style": args.limit or "all",
        },
        "trials": [],  # List of {bug_id, repo, style, test_output, failing_tests}
    }

    total = 0
    for repo in args.repos:
        for style in args.styles:
            bugs = load_catalog_bugs(repo, style, args.limit, args.catalog_dir)
            for bug in bugs:
                manifest["trials"].append({
                    "bug_id": bug["bug_id"],
                    "repo": repo,
                    "style": style,
                    "test_output": bug["test_output"],
                    "failing_tests": bug.get("failing_tests", []),
                })
                total += 1

    manifest["total_bugs"] = total
    manifest["total_trials_per_agent"] = total * 2  # with_tests + without_tests

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        limit_str = f"_{args.limit}per" if args.limit else "_full"
        output_path = DATA_DIR / "manifests" / f"manifest{limit_str}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Manifest generated: {output_path}")
    print(f"  Repos: {args.repos}")
    print(f"  Styles: {args.styles}")
    print(f"  Bugs per style: {args.limit or 'all'}")
    print(f"  Total bugs: {total}")
    print(f"  Total trials per agent (×2 modes): {total * 2}")

    # Summary table
    print(f"\n  {'Repo':<20} {'Style':<12} {'Bugs':>5}")
    print(f"  {'-'*20} {'-'*12} {'-'*5}")
    for repo in args.repos:
        for style in args.styles:
            count = sum(1 for t in manifest["trials"] if t["repo"] == repo and t["style"] == style)
            if count:
                print(f"  {repo:<20} {style:<12} {count:>5}")


if __name__ == "__main__":
    main()
