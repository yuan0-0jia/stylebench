#!/usr/bin/env python3
"""CLI runner for StyleBench benchmarks.

Usage:
    python -m benchmarks.runner --catalog path/to/catalog.json --repo path/to/repo \\
        --repo-name humanize --agent claude --mode with_tests

    # Run specific bugs
    python -m benchmarks.runner ... --bugs humanize-original-001 humanize-original-002

    # Run first N bugs
    python -m benchmarks.runner ... --limit 10
"""

import argparse
import sys
from pathlib import Path

from .agents import ClaudeAgent
from .harness import BenchmarkHarness


def get_agent(name: str, timeout: int = 300, max_turns: int = 10, model: str | None = None):
    """Get an agent by name.

    Args:
        name: Agent name ('claude').
        timeout: Timeout in seconds.
        max_turns: Maximum agentic turns.
        model: Model to use.

    Returns:
        Agent instance.
    """
    if name == "claude":
        return ClaudeAgent(timeout=timeout, max_turns=max_turns, model=model)
    else:
        raise ValueError(f"Unknown agent: {name}. Available: claude")


def main():
    """Run benchmark trials from command line."""
    parser = argparse.ArgumentParser(
        description="Run StyleBench benchmark trials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Required arguments
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Path to bug catalog JSON file",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        required=True,
        help="Path to the source repository",
    )
    parser.add_argument(
        "--repo-name",
        type=str,
        required=True,
        help="Repository name (humanize, validators, etc.)",
    )

    # Optional arguments
    parser.add_argument(
        "--agent",
        type=str,
        default="claude",
        help="Agent to use (default: claude)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["with_tests", "without_tests"],
        default="with_tests",
        help="Test access mode (default: with_tests)",
    )
    parser.add_argument(
        "--bugs",
        type=str,
        nargs="+",
        help="Specific bug IDs to test",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit to first N bugs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for results (default: temp dir)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Agent timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=120,
        help="Test timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Max agentic turns (default: 10)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model to use (agent-specific)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    # Validate paths
    if not args.catalog.exists():
        print(f"Error: Catalog not found: {args.catalog}", file=sys.stderr)
        sys.exit(1)

    if not args.repo.exists():
        print(f"Error: Repository not found: {args.repo}", file=sys.stderr)
        sys.exit(1)

    # Create harness
    harness = BenchmarkHarness(
        catalog_path=args.catalog,
        repo_path=args.repo,
        repo_name=args.repo_name,
        output_dir=args.output_dir,
    )

    # Get agent
    agent = get_agent(
        args.agent,
        timeout=args.timeout,
        max_turns=args.max_turns,
        model=args.model,
    )

    # Determine bug IDs to test
    bug_ids = args.bugs
    if bug_ids is None:
        bug_ids = [bug["bug_id"] for bug in harness.catalog.get("bugs", [])]

    if args.limit:
        bug_ids = bug_ids[: args.limit]

    # Progress callback
    def progress(current, total, bug_id):
        if not args.quiet:
            print(f"[{current}/{total}] Running {bug_id}...")

    # Run trials
    if not args.quiet:
        print(f"Running {len(bug_ids)} trials with {args.agent} agent ({args.mode} mode)")
        print(f"Repository: {args.repo_name}")
        print(f"Output: {harness.output_dir}")
        print()

    results = harness.run_all(
        agent=agent,
        mode=args.mode,
        bug_ids=bug_ids,
        test_timeout=args.test_timeout,
        progress_callback=progress,
    )

    # Save results
    output_path = harness.save_results()

    # Compute summary stats
    passed = sum(1 for r in results if r.evaluation == "PASS")
    failed = sum(1 for r in results if r.evaluation == "FAIL")
    errors = sum(1 for r in results if r.evaluation == "ERROR")
    timeouts = sum(1 for r in results if r.evaluation == "TIMEOUT")
    no_fix = sum(1 for r in results if r.evaluation == "NO_FIX")

    # Print summary
    if not args.quiet:
        print()
        print("=" * 60)
        print("RESULTS SUMMARY")
        print("=" * 60)

        print(f"Total trials: {len(results)}")
        print(f"  PASS:    {passed:3d} ({100*passed/len(results):.1f}%)")
        print(f"  FAIL:    {failed:3d} ({100*failed/len(results):.1f}%)")
        print(f"  NO_FIX:  {no_fix:3d} ({100*no_fix/len(results):.1f}%)")
        print(f"  ERROR:   {errors:3d} ({100*errors/len(results):.1f}%)")
        print(f"  TIMEOUT: {timeouts:3d} ({100*timeouts/len(results):.1f}%)")
        print()
        print(f"Results saved to: {output_path}")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
