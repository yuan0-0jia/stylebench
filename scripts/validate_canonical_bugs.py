#!/usr/bin/env python3
"""Validate canonical bug catalogs.

Phase 1 (fast): Verify apply_bug() succeeds and revert_bug() restores for all entries.
Phase 2 (slow): Spot-check that bugs actually cause test failures in temp copies.

Usage:
    python scripts/validate_canonical_bugs.py              # Phase 1 only (fast)
    python scripts/validate_canonical_bugs.py --test-all   # Phase 1 + 2 for all bugs
    python scripts/validate_canonical_bugs.py --test-sample 3  # Phase 1 + test 3 bugs/catalog
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.evaluator import apply_bug, revert_bug
from bugs.catalog import BugCatalog, run_with_cleanup
from bugs.repo_config import get_config

DATA_DIR = Path("/Users/yuan/stylebench-data")
CATALOG_DIR = DATA_DIR / "bugs_canonical"

ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
ALL_STYLES = ["original", "camelcase", "badnames", "formatting"]


def phase1_apply_revert(verbose=False):
    """Phase 1: Verify apply_bug/revert_bug works for every catalog entry using temp copies."""
    print("=" * 60)
    print("Phase 1: Verify apply_bug() / revert_bug() for all entries")
    print("=" * 60)

    total = 0
    apply_ok = 0
    revert_ok = 0
    apply_fail = []
    revert_fail = []
    content_mismatch = []

    for repo in ALL_REPOS:
        for style in ALL_STYLES:
            catalog_path = CATALOG_DIR / f"{repo}-{style}.json"
            if not catalog_path.exists():
                print(f"  SKIP: {catalog_path.name} not found")
                continue

            catalog = BugCatalog.load(catalog_path)
            repo_path = DATA_DIR / style / repo

            # Create a temp copy to avoid dirtying the data repo
            with tempfile.TemporaryDirectory() as tmp:
                tmp_repo = Path(tmp) / repo
                shutil.copytree(
                    repo_path,
                    tmp_repo,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(
                        ".venv",
                        "__pycache__",
                        ".pytest_cache",
                        ".ruff_cache",
                        ".mypy_cache",
                    ),
                )

                for bug, hidden in zip(catalog.bugs, catalog._hidden):
                    total += 1
                    bug_id = bug.bug_id
                    h = hidden.to_dict()

                    # Save original content for comparison
                    file_path = tmp_repo / h["file_path"]
                    if not file_path.exists():
                        apply_fail.append((bug_id, "file not found"))
                        continue
                    original_content = file_path.read_text()

                    # Apply
                    ok = apply_bug(tmp_repo, h)
                    if ok:
                        apply_ok += 1
                        modified_content = file_path.read_text()
                        if modified_content == original_content:
                            apply_fail.append((bug_id, "apply returned True but content unchanged"))
                            apply_ok -= 1
                        else:
                            # Revert
                            rok = revert_bug(tmp_repo, h)
                            if rok:
                                reverted_content = file_path.read_text()
                                if reverted_content == original_content:
                                    revert_ok += 1
                                else:
                                    content_mismatch.append(bug_id)
                            else:
                                revert_fail.append((bug_id, "revert returned False"))
                    else:
                        apply_fail.append((bug_id, "apply returned False"))

                    # ALWAYS restore original content for next bug
                    file_path.write_text(original_content)

            if verbose:
                print(f"  {repo}-{style}: {len(catalog.bugs)} bugs checked")

    print("\nPhase 1 Results:")
    print(f"  Total entries:     {total}")
    print(f"  Apply succeeded:   {apply_ok}/{total}")
    print(f"  Revert succeeded:  {revert_ok}/{total}")

    if apply_fail:
        print(f"\n  Apply failures ({len(apply_fail)}):")
        for bug_id, reason in apply_fail:
            print(f"    {bug_id}: {reason}")

    if revert_fail:
        print(f"\n  Revert failures ({len(revert_fail)}):")
        for bug_id, reason in revert_fail:
            print(f"    {bug_id}: {reason}")

    if content_mismatch:
        print(f"\n  Content mismatch after revert ({len(content_mismatch)}):")
        for bug_id in content_mismatch:
            print(f"    {bug_id}")

    success = not apply_fail and not revert_fail and not content_mismatch
    print(f"\n  Phase 1: {'PASS' if success else 'FAIL'}")
    return success


def phase2_test_failures(sample_size=0, verbose=False):
    """Phase 2: Verify bugs cause test failures in temp copies.

    Args:
        sample_size: Number of bugs per catalog to test. 0 = all.
    """
    print("\n" + "=" * 60)
    print(f"Phase 2: Verify bugs cause test failures (sample={sample_size or 'all'})")
    print("=" * 60)

    total_tested = 0
    total_killed = 0
    failures = []

    for repo in ALL_REPOS:
        config = get_config(repo)

        for style in ALL_STYLES:
            catalog_path = CATALOG_DIR / f"{repo}-{style}.json"
            if not catalog_path.exists():
                continue

            catalog = BugCatalog.load(catalog_path)
            repo_path = DATA_DIR / style / repo

            # Select bugs to test
            indices = list(range(len(catalog.bugs)))
            if sample_size > 0:
                # Spread across the catalog
                step = max(1, len(indices) // sample_size)
                indices = indices[::step][:sample_size]

            if not indices:
                continue

            print(f"\n  Testing {repo}-{style} ({len(indices)} bugs)...")

            # Create temp copy
            with tempfile.TemporaryDirectory() as tmp:
                tmp_repo = Path(tmp) / repo
                shutil.copytree(
                    repo_path,
                    tmp_repo,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(
                        ".venv",
                        "__pycache__",
                        ".pytest_cache",
                        ".ruff_cache",
                        ".mypy_cache",
                    ),
                )

                # Run baseline
                test_cmd = config.get_test_command(tmp_repo, external=False)
                try:
                    result = run_with_cleanup(test_cmd, cwd=tmp_repo, timeout=120)
                    baseline_output = result.stdout + result.stderr
                    baseline_failing = set()
                    for line in baseline_output.split("\n"):
                        if line.startswith("FAILED "):
                            baseline_failing.add(line.split(" ")[1].split(" -")[0])
                except Exception as e:
                    print(f"    Baseline failed: {e}")
                    continue

                for idx in indices:
                    bug = catalog.bugs[idx]
                    hidden = catalog._hidden[idx]
                    h = hidden.to_dict()
                    bug_id = bug.bug_id

                    file_path = tmp_repo / h["file_path"]
                    original_content = file_path.read_text()

                    ok = apply_bug(tmp_repo, h)
                    if not ok:
                        failures.append((bug_id, "apply failed"))
                        total_tested += 1
                        continue

                    try:
                        result = run_with_cleanup(test_cmd, cwd=tmp_repo, timeout=60)
                        output = result.stdout + result.stderr
                        exit_code = result.returncode

                        failing = []
                        for line in output.split("\n"):
                            if line.startswith("FAILED "):
                                failing.append(line.split(" ")[1].split(" -")[0])

                        new_failures = [t for t in failing if t not in baseline_failing]

                        if exit_code != 0 and new_failures:
                            total_killed += 1
                            if verbose:
                                print(f"    {bug_id}: KILLED ({len(new_failures)} failures)")
                        else:
                            failures.append(
                                (bug_id, f"exit={exit_code}, new_failures={len(new_failures)}")
                            )
                            if verbose:
                                print(
                                    f"    {bug_id}: SURVIVED "
                                    f"(exit={exit_code}, new={len(new_failures)})"
                                )
                    except subprocess.TimeoutExpired:
                        failures.append((bug_id, "timeout"))
                    except Exception as e:
                        failures.append((bug_id, f"error: {e}"))
                    finally:
                        # Restore
                        file_path.write_text(original_content)
                        total_tested += 1

    print("\nPhase 2 Results:")
    print(f"  Tested:  {total_tested}")
    print(f"  Killed:  {total_killed}/{total_tested}")

    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for bug_id, reason in failures:
            print(f"    {bug_id}: {reason}")

    success = not failures
    print(f"\n  Phase 2: {'PASS' if success else 'FAIL'}")
    return success


def main():
    parser = argparse.ArgumentParser(description="Validate canonical bug catalogs")
    parser.add_argument(
        "--test-all",
        action="store_true",
        help="Test ALL bugs for test failures (slow)",
    )
    parser.add_argument(
        "--test-sample",
        type=int,
        default=0,
        help="Test N bugs per catalog for failures",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    p1 = phase1_apply_revert(verbose=args.verbose)

    if args.test_all:
        p2 = phase2_test_failures(sample_size=0, verbose=args.verbose)
    elif args.test_sample > 0:
        p2 = phase2_test_failures(sample_size=args.test_sample, verbose=args.verbose)
    else:
        p2 = True

    if p1 and p2:
        print("\n*** ALL VALIDATIONS PASSED ***")
    else:
        print("\n*** SOME VALIDATIONS FAILED ***")
        sys.exit(1)


if __name__ == "__main__":
    main()
