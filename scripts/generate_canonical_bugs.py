#!/usr/bin/env python3
"""Generate canonical (consistent) bugs across all style variants.

Discovers mutation candidates in the 'original' style, maps each to all
other style variants, validates that the mutation kills tests in every
variant, and outputs one catalog per repo-style with identical bug IDs.

This ensures cross-style comparisons are valid: the only variable is
code style, not the bug itself.

Usage:
    # Generate canonical bugs for one repo
    python scripts/generate_canonical_bugs.py humanize --count 20

    # Generate for all repos
    python scripts/generate_canonical_bugs.py --all --count 20

    # Verbose output
    python scripts/generate_canonical_bugs.py humanize --count 10 --verbose

    # Control parallelism
    python scripts/generate_canonical_bugs.py humanize --count 20 --workers 2
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add project root to path so we can import our modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bugs.catalog import (
    MUTATION_PRIORITY,
    BugCatalog,
    BugEntry,
    HiddenMetadata,
    run_with_cleanup,
)
from bugs.injector import Injector, MutationSite, MutationType
from bugs.mapper import MappedMutation, map_mutation
from bugs.repo_config import get_config

DATA_DIR = Path("/Users/yuan/stylebench-data")

# Mutation type -> category mapping for diversity caps
MUTATION_CATEGORY = {
    MutationType.COMPARISON_EQ_NE: "comparison",
    MutationType.COMPARISON_LT_GT: "comparison",
    MutationType.COMPARISON_LE_GE: "comparison",
    MutationType.BOOLEAN_AND_OR: "boolean",
    MutationType.BOOL_TRUE_FALSE: "boolean",
    MutationType.MEMBERSHIP_IN: "membership",
    MutationType.IDENTITY_IS: "membership",
    MutationType.ARITHMETIC_ADD_SUB: "arithmetic",
    MutationType.ARITHMETIC_MUL_DIV: "arithmetic",
    MutationType.BOUNDARY_PLUS_ONE: "boundary",
    MutationType.BOUNDARY_MINUS_ONE: "boundary",
    MutationType.VARIABLE_SWAP: "var_swap",
    MutationType.RETURN_NONE: "return_none",
    MutationType.IF_ELSE_SWAP: "if_else_swap",
}
ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
ALL_STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]
VARIANT_STYLES = ["camelcase", "snakecase", "badnames", "formatting"]
TEST_TIMEOUT = 60


def log(msg: str, verbose: bool = True):
    if verbose:
        print(msg, flush=True)


def discover_candidates(repo: str, verbose: bool = False) -> list[tuple[str, MutationSite]]:
    """Phase 1: Discover all mutation candidates from the original variant.

    Returns list of (relative_file_path, MutationSite) sorted by priority.
    """
    config = get_config(repo)
    repo_path = DATA_DIR / "original" / repo
    source_path = config.get_source_path(repo_path)

    injector = Injector()
    all_sites: list[tuple[str, MutationSite]] = []

    files = sorted(source_path.glob("**/*.py"))
    files = [f for f in files if "__pycache__" not in str(f)]

    for file_path in files:
        try:
            code = file_path.read_text()
            sites = injector.list_mutation_sites(code)
            rel_path = str(file_path.relative_to(repo_path))
            for site in sites:
                all_sites.append((rel_path, site))
        except Exception as e:
            log(f"  Warning: could not parse {file_path}: {e}", verbose)

    # Sort by priority (high kill-rate types first)
    priority_map = {mt: i for i, mt in enumerate(MUTATION_PRIORITY)}
    max_priority = len(MUTATION_PRIORITY)
    all_sites.sort(key=lambda x: priority_map.get(x[1].mutation_type, max_priority))

    log(f"  Phase 1: {len(all_sites)} mutation candidates in {len(files)} files", verbose)
    return all_sites


def map_to_variants(
    repo: str,
    candidates: list[tuple[str, MutationSite]],
    verbose: bool = False,
) -> list[tuple[str, MutationSite, dict[str, MappedMutation]]]:
    """Phase 2: Map each candidate to all style variants.

    Returns candidates that successfully map to ALL variants.
    Each entry: (rel_file_path, original_site, {style: MappedMutation})
    """
    mapped: list[tuple[str, MutationSite, dict[str, MappedMutation]]] = []
    skipped = 0

    for rel_path, site in candidates:
        orig_file = DATA_DIR / "original" / repo / rel_path
        if not orig_file.exists():
            skipped += 1
            continue

        orig_source = orig_file.read_text()
        style_mappings: dict[str, MappedMutation] = {}
        all_mapped = True

        for style in VARIANT_STYLES:
            target_file = DATA_DIR / style / repo / rel_path
            if not target_file.exists():
                all_mapped = False
                break

            target_source = target_file.read_text()
            result = map_mutation(orig_source, target_source, site, style)

            if result is None:
                all_mapped = False
                break

            result.file_path = rel_path
            style_mappings[style] = result

        if all_mapped:
            mapped.append((rel_path, site, style_mappings))
        else:
            skipped += 1

    log(
        f"  Phase 2: {len(mapped)} candidates map to all variants "
        f"({skipped} dropped)",
        verbose,
    )
    return mapped


def _apply_mapped_mutation(file_path: Path, mapping: MappedMutation) -> str | None:
    """Apply a mapped mutation to a file. Returns original content for restoration."""
    try:
        original = file_path.read_text()
        before = original[: mapping.start_byte]
        after = original[mapping.end_byte :]
        mutated = before + mapping.mutated_text + after
        file_path.write_text(mutated)
        return original
    except Exception:
        return None


def _apply_original_mutation(file_path: Path, site: MutationSite) -> str | None:
    """Apply a MutationSite to a file. Returns original content."""
    try:
        original = file_path.read_text()
        before = original[: site.start_byte]
        after = original[site.end_byte :]
        mutated = before + site.mutated_text + after
        file_path.write_text(mutated)
        return original
    except Exception:
        return None


def _run_tests_in(
    repo_path: Path, repo: str, timeout: int = TEST_TIMEOUT
) -> tuple[int, str, list[str]]:
    """Run tests in a repo copy. Returns (exit_code, output, failing_tests)."""
    config = get_config(repo)
    test_command = config.get_test_command(repo_path, external=False)

    try:
        result = run_with_cleanup(
            test_command, cwd=repo_path, timeout=timeout,
        )
        output = result.stdout + result.stderr
        failing = []
        for line in output.split("\n"):
            if line.startswith("FAILED "):
                test_name = line.split(" ")[1].split(" -")[0]
                failing.append(test_name)
        return result.returncode, output, failing
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT", []
    except Exception as e:
        return -1, f"ERROR: {e}", []


def validate_candidates(
    repo: str,
    mapped_candidates: list[tuple[str, MutationSite, dict[str, MappedMutation]]],
    count: int,
    max_per_type: int = 0,
    verbose: bool = False,
) -> list[dict]:
    """Phase 3: Validate mutations kill tests in ALL variants.

    Args:
        max_per_type: Max bugs per mutation category (0 = unlimited).
            Categories group similar types (e.g., eq_ne/lt_gt/le_ge = "comparison").

    Returns list of validated bug dicts, each containing:
    - rel_path, site, mappings, and per-style test output
    """
    validated: list[dict] = []
    tested = 0
    category_counts: dict[str, int] = {}

    # Create temp copies of all 5 variants
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        copies: dict[str, Path] = {}

        log("  Phase 3: Creating temporary repo copies...", verbose)

        for style in ["original"] + VARIANT_STYLES:
            src = DATA_DIR / style / repo
            dst = temp_path / style / repo
            shutil.copytree(
                src, dst, symlinks=True,
                ignore=shutil.ignore_patterns(
                    ".venv", "__pycache__", ".pytest_cache",
                    ".ruff_cache", ".mypy_cache",
                ),
            )
            copies[style] = dst

        # Run baseline tests for each variant to detect pre-existing failures
        baselines: dict[str, set[str]] = {}
        for style, copy_path in copies.items():
            log(f"    Baseline test: {style}...", verbose)
            _, _, baseline_failing = _run_tests_in(copy_path, repo, timeout=TEST_TIMEOUT * 2)
            baselines[style] = set(baseline_failing)
            if baseline_failing:
                log(f"      {len(baseline_failing)} pre-existing failures", verbose)

        log(f"  Phase 3: Testing {len(mapped_candidates)} candidates...", verbose)

        for rel_path, site, mappings in mapped_candidates:
            if len(validated) >= count:
                break

            # Skip if this mutation category already hit its cap
            if max_per_type > 0:
                cat = MUTATION_CATEGORY.get(site.mutation_type, site.mutation_type.value)
                if category_counts.get(cat, 0) >= max_per_type:
                    continue

            tested += 1
            all_killed = True
            test_results: dict[str, dict] = {}

            # Test original variant
            file_in_copy = copies["original"] / rel_path
            saved = _apply_original_mutation(file_in_copy, site)
            if saved is None:
                continue

            exit_code, output, failing = _run_tests_in(copies["original"], repo)
            file_in_copy.write_text(saved)

            new_failures = [t for t in failing if t not in baselines["original"]]
            if exit_code == 0 or not new_failures:
                all_killed = False
            else:
                test_results["original"] = {
                    "test_output": output,
                    "failing_tests": new_failures,
                    "exit_code": exit_code,
                }

            if not all_killed:
                if verbose and tested % 20 == 0:
                    log(f"    [{tested}] {len(validated)} validated so far...", True)
                continue

            # Test each variant
            for style in VARIANT_STYLES:
                mapping = mappings[style]
                file_in_copy = copies[style] / rel_path
                saved = _apply_mapped_mutation(file_in_copy, mapping)
                if saved is None:
                    all_killed = False
                    break

                exit_code, output, failing = _run_tests_in(copies[style], repo)
                file_in_copy.write_text(saved)

                new_failures = [t for t in failing if t not in baselines[style]]
                if exit_code == 0 or not new_failures:
                    all_killed = False
                    break

                test_results[style] = {
                    "test_output": output,
                    "failing_tests": new_failures,
                    "exit_code": exit_code,
                }

            if all_killed:
                validated.append({
                    "rel_path": rel_path,
                    "site": site,
                    "mappings": mappings,
                    "test_results": test_results,
                })
                cat = MUTATION_CATEGORY.get(site.mutation_type, site.mutation_type.value)
                category_counts[cat] = category_counts.get(cat, 0) + 1
                log(
                    f"    [{tested}] Validated bug #{len(validated)}: "
                    f"{site.mutation_type.value} in {rel_path} "
                    f"line {site.start_point[0] + 1}",
                    verbose,
                )
            elif verbose and tested % 20 == 0:
                log(f"    [{tested}] {len(validated)} validated so far...", True)

    log(
        f"  Phase 3: {len(validated)} bugs validated across all variants "
        f"(tested {tested})",
        verbose,
    )
    if verbose and category_counts:
        log("  Category breakdown:", True)
        for cat, c in sorted(category_counts.items(), key=lambda x: -x[1]):
            log(f"    {cat}: {c}", True)
    return validated


def build_catalogs(
    repo: str,
    validated: list[dict],
    verbose: bool = False,
) -> dict[str, BugCatalog]:
    """Phase 4: Build one catalog per style with identical bug IDs."""
    config = get_config(repo)
    catalogs: dict[str, BugCatalog] = {}

    for style in ["original"] + VARIANT_STYLES:
        repo_path = DATA_DIR / style / repo
        catalogs[style] = BugCatalog(
            repo=repo,
            style=style,
            generated_at=datetime.now().isoformat(),
            test_command=config.get_test_command(repo_path),
        )

    for bug_num, entry in enumerate(validated, start=1):
        rel_path = entry["rel_path"]
        site: MutationSite = entry["site"]
        mappings: dict[str, MappedMutation] = entry["mappings"]
        test_results: dict[str, dict] = entry["test_results"]

        for style in ["original"] + VARIANT_STYLES:
            bug_id = f"{repo}-{style}-{bug_num:03d}"
            tr = test_results[style]

            bug_entry = BugEntry(
                bug_id=bug_id,
                test_output=tr["test_output"],
                failing_tests=tr["failing_tests"],
                exit_code=tr["exit_code"],
            )

            if style == "original":
                # For return_none, include "return" keyword in text to avoid
                # ambiguity in apply_bug() line-level replacement
                orig_text = site.original_text
                mut_text = site.mutated_text
                if site.mutation_type == MutationType.RETURN_NONE:
                    orig_text = "return " + orig_text
                    mut_text = "return None"

                hidden = HiddenMetadata(
                    bug_id=bug_id,
                    file_path=rel_path,
                    line_number=site.start_point[0] + 1,
                    mutation_type=site.mutation_type.value,
                    original_text=orig_text,
                    mutated_text=mut_text,
                    context=site.context,
                )
            else:
                m = mappings[style]
                hidden = HiddenMetadata(
                    bug_id=bug_id,
                    file_path=m.file_path,
                    line_number=m.line_number,
                    mutation_type=m.mutation_type.value,
                    original_text=m.original_text,
                    mutated_text=m.mutated_text,
                    context=m.context,
                )

            catalogs[style].bugs.append(bug_entry)
            catalogs[style]._hidden.append(hidden)

    log(f"  Phase 4: Built {len(catalogs)} catalogs with {len(validated)} bugs each", verbose)
    return catalogs


def save_catalogs(catalogs: dict[str, BugCatalog], verbose: bool = False):
    """Save catalogs to bugs_canonical/ directory."""
    output_dir = DATA_DIR / "bugs_canonical"
    output_dir.mkdir(parents=True, exist_ok=True)

    for style, catalog in catalogs.items():
        path = output_dir / f"{catalog.repo}-{style}.json"
        catalog.save(path)
        log(f"  Saved {path.name} ({len(catalog.bugs)} bugs)", verbose)


def generate_canonical_bugs(
    repo: str, count: int = 20, max_per_type: int = 0, verbose: bool = False
):
    """Full pipeline for one repo."""
    log(f"\n{'='*60}", verbose)
    log(f"Generating canonical bugs for: {repo}", verbose)
    log(f"Target count: {count}", verbose)
    if max_per_type > 0:
        log(f"Max per mutation type: {max_per_type}", verbose)
    log(f"{'='*60}", verbose)

    # Phase 1: Discover
    candidates = discover_candidates(repo, verbose)

    # Phase 2: Map
    mapped = map_to_variants(repo, candidates, verbose)

    if not mapped:
        log(f"  ERROR: No candidates could be mapped to all variants for {repo}", True)
        return

    # Phase 3: Validate
    validated = validate_candidates(repo, mapped, count, max_per_type, verbose)

    if not validated:
        log(f"  ERROR: No mutations validated across all variants for {repo}", True)
        return

    # Phase 4: Build & save catalogs
    catalogs = build_catalogs(repo, validated, verbose)
    save_catalogs(catalogs, verbose)

    log(f"\nDone: {len(validated)} canonical bugs for {repo}", verbose)


def main():
    parser = argparse.ArgumentParser(
        description="Generate canonical bugs consistent across all style variants"
    )
    parser.add_argument(
        "repo",
        nargs="?",
        choices=ALL_REPOS,
        help="Repository to generate bugs for",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate for all repositories",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Target number of bugs per repo (default: 20)",
    )
    parser.add_argument(
        "--max-per-type",
        type=int,
        default=0,
        help="Max bugs per mutation category (0 = unlimited, try 3-5 for diversity)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed progress",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of parallel workers (currently unused, reserved for future)",
    )
    args = parser.parse_args()

    if not args.repo and not args.all:
        parser.error("Specify a repo name or use --all")

    repos = ALL_REPOS if args.all else [args.repo]

    for repo in repos:
        generate_canonical_bugs(
            repo, count=args.count, max_per_type=args.max_per_type,
            verbose=args.verbose,
        )

    print("\nAll done!")
    print(f"Catalogs saved to: {DATA_DIR / 'bugs_canonical'}/")


if __name__ == "__main__":
    main()
