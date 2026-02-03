#!/usr/bin/env python3
"""
Generate bug catalogs for all repo/style combinations.

Usage:
    # Single repo/style
    python scripts/generate_bugs.py humanize original --count 50

    # All variants for one repo
    python scripts/generate_bugs.py humanize --all-styles --count 50

    # All repos and styles
    python scripts/generate_bugs.py --all --count 50

    # Save to data repo
    python scripts/generate_bugs.py --all --count 50 --output ../stylebench-data/bugs/

    # Control parallelism (default: 2 workers)
    python scripts/generate_bugs.py --all --count 50 --workers 1
"""

import argparse
import atexit
import signal
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from bugs.catalog import generate_catalog  # noqa: E402
from bugs.repo_config import REPO_CONFIGS  # noqa: E402

_cleanup_done = False


def cleanup_children():
    """Kill any orphaned pytest child processes on exit."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    # Kill pytest processes spawned by this script
    import subprocess
    try:
        # Find and kill any pytest processes in stylebench-data
        subprocess.run(
            ["pkill", "-9", "-f", "pytest.*stylebench-data"],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    print("\nInterrupted. Cleaning up...")
    cleanup_children()
    sys.exit(1)


# Register cleanup handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
atexit.register(cleanup_children)

STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]


def generate_for_variant(
    repo_name: str,
    style: str,
    data_dir: Path,
    output_dir: Path | None,
    max_bugs: int,
    verbose: bool,
    num_workers: int,
    no_parallel: bool,
) -> dict:
    """Generate bugs for a single repo/style variant."""
    repo_path = data_dir / style / repo_name

    if not repo_path.exists():
        print(f"  SKIP: {repo_path} does not exist")
        return {"status": "skipped", "reason": "path not found"}

    print(f"  Generating {max_bugs} bugs for {repo_name}/{style}...")

    try:
        catalog = generate_catalog(
            repo_path=repo_path,
            repo_name=repo_name,
            style=style,
            max_bugs=max_bugs,
            verbose=verbose,
            num_workers=num_workers,
            parallel=not no_parallel,
        )

        result = {
            "status": "success",
            "bugs_generated": len(catalog.bugs),
            "repo": repo_name,
            "style": style,
        }

        # Save if output directory specified
        if output_dir:
            out_path = output_dir / f"{repo_name}-{style}.json"
            catalog.save(out_path, include_hidden=True)
            result["output_file"] = str(out_path)

            # Also save agent-only version
            agent_path = output_dir / f"{repo_name}-{style}-agent.json"
            catalog.save(agent_path, include_hidden=False)
            result["agent_file"] = str(agent_path)

        print(f"  SUCCESS: {len(catalog.bugs)} bugs generated")
        return result

    except Exception as e:
        print(f"  ERROR: {e}")
        return {"status": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Generate bug catalogs for StyleBench",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "repo",
        nargs="?",
        choices=list(REPO_CONFIGS.keys()),
        help="Repository to process",
    )
    parser.add_argument(
        "style",
        nargs="?",
        choices=STYLES,
        help="Style variant to process",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all repos and styles",
    )
    parser.add_argument(
        "--all-styles",
        action="store_true",
        help="Process all styles for the specified repo",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of bugs to generate per variant (default: 50)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory for bug catalogs",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent.parent / "data",
        help="Directory containing style variants (default: ./data)",
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
        help="Number of parallel workers (default: 2, use 1 for sequential)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Force sequential mode (no repo copies, lower memory)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.all:
        repos = list(REPO_CONFIGS.keys())
        styles = STYLES
    elif args.repo and args.all_styles:
        repos = [args.repo]
        styles = STYLES
    elif args.repo and args.style:
        repos = [args.repo]
        styles = [args.style]
    else:
        parser.error("Specify --all, or repo with --all-styles, or both repo and style")

    # Resolve data directory
    data_dir = args.data_dir.resolve()
    if not data_dir.exists():
        print(f"Error: Data directory does not exist: {data_dir}")
        sys.exit(1)

    # Create output directory if specified
    output_dir = None
    if args.output:
        output_dir = args.output.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

    # Generate bugs
    results = []
    total = len(repos) * len(styles)
    current = 0

    print(f"Generating bugs for {len(repos)} repos × {len(styles)} styles = {total} variants")
    print(f"Target: {args.count} bugs per variant")
    print()

    for repo in repos:
        print(f"[{repo}]")
        for style in styles:
            current += 1
            result = generate_for_variant(
                repo_name=repo,
                style=style,
                data_dir=data_dir,
                output_dir=output_dir,
                max_bugs=args.count,
                verbose=args.verbose,
                num_workers=args.workers,
                no_parallel=args.no_parallel,
            )
            result["variant"] = f"{repo}/{style}"
            results.append(result)
        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    success = [r for r in results if r["status"] == "success"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"Success: {len(success)}/{total}")
    print(f"Skipped: {len(skipped)}/{total}")
    print(f"Errors:  {len(errors)}/{total}")

    if success:
        total_bugs = sum(r["bugs_generated"] for r in success)
        print(f"\nTotal bugs generated: {total_bugs}")

    if errors:
        print("\nErrors:")
        for r in errors:
            print(f"  {r['variant']}: {r['error']}")

    if output_dir:
        print(f"\nOutput saved to: {output_dir}")


if __name__ == "__main__":
    main()
