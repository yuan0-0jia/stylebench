#!/usr/bin/env python3
"""Run benchmark with rate limit handling and resumable progress.

Features:
- Tracks per-bug progress so partial batches are preserved
- Detects rate limit errors and exits gracefully
- Resumes from where it left off, only re-running incomplete bugs
- Configurable delays between trials

Usage:
    # Start or resume benchmark
    python scripts/run_benchmark.py

    # Reset progress and start fresh
    python scripts/run_benchmark.py --reset

    # Run specific repos only
    python scripts/run_benchmark.py --repos python-markdown more-itertools

    # Run specific mode only
    python scripts/run_benchmark.py --mode without_tests
"""

import argparse
import gc
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

# Configuration
DATA_DIR = Path("/Users/yuan/stylebench-data")
RESULTS_DIR = DATA_DIR / "results" / "benchmark"
STATE_FILE = RESULTS_DIR / "benchmark_state.json"
BUGS_PER_STYLE = 10
DELAY_BETWEEN_TRIALS = 2  # seconds
DELAY_BETWEEN_BATCHES = 5  # seconds

ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
ALL_STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]
ALL_MODES = ["with_tests", "without_tests"]


def load_state() -> dict:
    """Load progress state from file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return _empty_state()


def _empty_state() -> dict:
    return {
        "completed_bugs": {},  # {mode: {bug_id: evaluation}} - per-bug results
        "completed_batches": [],  # [repo, style, mode] fully done
        "current": None,
        "rate_limited_at": None,
        "started_at": None,
        "last_updated": None,
    }


def save_state(state: dict):
    """Save progress state to file."""
    state["last_updated"] = datetime.now().isoformat()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_bug_ids_for_batch(repo: str, style: str, limit: int) -> list[str]:
    """Load bug IDs from a catalog, limited to first `limit`."""
    catalog = DATA_DIR / "bugs" / f"{repo}-{style}.json"
    if not catalog.exists():
        return []
    with open(catalog) as f:
        data = json.load(f)
    bug_ids = [b["bug_id"] for b in data.get("bugs", [])]
    return bug_ids[:limit]


def get_pending_bugs(state: dict, repo: str, style: str, mode: str, limit: int) -> list[str]:
    """Return bug IDs for this batch that haven't completed yet."""
    all_bugs = get_bug_ids_for_batch(repo, style, limit)
    done = state.get("completed_bugs", {}).get(mode, {})
    return [b for b in all_bugs if b not in done]


def is_batch_complete(state: dict, repo: str, style: str, mode: str, limit: int) -> bool:
    """Check if all bugs in a batch are completed."""
    return len(get_pending_bugs(state, repo, style, mode, limit)) == 0


def parse_result_file(result_file: Path) -> list[dict]:
    """Parse a result file and classify each bug.

    Returns list of dicts with keys: bug_id, evaluation, rate_limited.
    """
    if not result_file.exists():
        return []
    try:
        with open(result_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return []

    parsed = []
    for trial in data.get("results", []):
        bug_id = trial.get("bug_id", "")
        evaluation = trial.get("evaluation", "ERROR")
        output = trial.get("fix_result", {}).get("agent_output", "") or ""
        rate_limited = "hit your limit" in output.lower()
        parsed.append(
            {
                "bug_id": bug_id,
                "evaluation": evaluation,
                "rate_limited": rate_limited,
            }
        )
    return parsed


def find_result_file_after(timestamp: float) -> Path | None:
    """Find the newest result file created after `timestamp`."""
    candidates = []
    for f in RESULTS_DIR.glob("results_*.json"):
        if f.stat().st_mtime >= timestamp:
            candidates.append(f)
    if candidates:
        return max(candidates, key=lambda x: x.stat().st_mtime)
    return None


def run_batch(repo: str, style: str, mode: str, bug_ids: list[str]) -> tuple[list[dict], bool]:
    """Run a batch for specific bug IDs.

    Returns (parsed_results, had_rate_limit).
    """
    catalog = DATA_DIR / "bugs" / f"{repo}-{style}.json"
    repo_path = DATA_DIR / style / repo

    if not catalog.exists():
        print(f"    Catalog not found: {catalog}")
        return [], False

    if not repo_path.exists():
        print(f"    Repo not found: {repo_path}")
        return [], False

    cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "benchmarks.runner",
        "--catalog",
        str(catalog),
        "--repo",
        str(repo_path),
        "--repo-name",
        repo,
        "--agent",
        "claude",
        "--mode",
        mode,
        "--output-dir",
        str(RESULTS_DIR),
        "--quiet",
        "--bugs",
        *bug_ids,
    ]

    before = time.time()
    try:
        subprocess.run(
            cmd,
            cwd="/Users/yuan/stylebench",
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max per batch
        )

        result_file = find_result_file_after(before)
        if result_file is None:
            return [], False

        parsed = parse_result_file(result_file)
        had_rate_limit = any(r["rate_limited"] for r in parsed)
        return parsed, had_rate_limit

    except subprocess.TimeoutExpired:
        print("    Timeout!")
        return [], False
    except Exception as e:
        print(f"    Error: {e}")
        return [], False


def record_results(state: dict, mode: str, parsed: list[dict]):
    """Record per-bug results into state, skipping rate-limited ones."""
    if mode not in state["completed_bugs"]:
        state["completed_bugs"][mode] = {}
    for r in parsed:
        if not r["rate_limited"]:
            state["completed_bugs"][mode][r["bug_id"]] = r["evaluation"]


def main():
    parser = argparse.ArgumentParser(description="Run benchmark with rate limit handling")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start fresh")
    parser.add_argument("--repos", nargs="+", default=ALL_REPOS, help="Repos to test")
    parser.add_argument("--styles", nargs="+", default=ALL_STYLES, help="Styles to test")
    parser.add_argument("--modes", nargs="+", default=ALL_MODES, help="Modes to test")
    parser.add_argument("--limit", type=int, default=BUGS_PER_STYLE, help="Bugs per batch")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load or reset state
    if args.reset:
        state = _empty_state()
        state["started_at"] = datetime.now().isoformat()
        save_state(state)
        print("Progress reset.")
    else:
        state = load_state()
        # Migrate old state format if needed
        if "completed_bugs" not in state:
            state["completed_bugs"] = {}
        if "completed_batches" not in state:
            state["completed_batches"] = state.pop("completed", [])

    if state.get("started_at") is None:
        state["started_at"] = datetime.now().isoformat()
        save_state(state)

    # Build list of batches
    all_batches = [
        (repo, style, mode) for mode in args.modes for repo in args.repos for style in args.styles
    ]

    # Filter out fully completed batches
    pending = [b for b in all_batches if not is_batch_complete(state, *b, args.limit)]

    # Count total completed bugs across all modes
    total_done_bugs = sum(len(bugs) for bugs in state.get("completed_bugs", {}).values())

    print("Benchmark Progress")
    print("=" * 60)
    print(f"Total batches: {len(all_batches)}")
    print(f"Fully completed batches: {len(all_batches) - len(pending)}")
    print(f"Batches with remaining work: {len(pending)}")
    print(f"Individual bugs completed: {total_done_bugs}")
    print(f"Results dir: {RESULTS_DIR}")
    print()

    if not pending:
        print("All batches completed!")
        return

    # Check if we recently hit rate limit
    if state.get("rate_limited_at"):
        rate_limit_time = datetime.fromisoformat(state["rate_limited_at"])
        elapsed = (datetime.now() - rate_limit_time).total_seconds()
        if elapsed < 3600:  # Less than 1 hour ago
            print(f"Rate limit was hit {elapsed / 60:.0f} minutes ago.")
            print("Consider waiting before resuming.")
            response = input("Continue anyway? [y/N]: ")
            if response.lower() != "y":
                print("Exiting. Run again later.")
                return

    print(f"Starting benchmark with {len(pending)} pending batches...")
    print()

    current_mode = None
    current_repo = None
    batch_num = 0

    for repo, style, mode in pending:
        # Print headers for new mode/repo
        if mode != current_mode:
            current_mode = mode
            print(f"\n=== Mode: {mode} ===")

        if repo != current_repo:
            current_repo = repo
            print(f"\n  Repository: {repo}")

        # Determine which bugs still need running
        remaining = get_pending_bugs(state, repo, style, mode, args.limit)
        if not remaining:
            continue

        total_for_batch = len(get_bug_ids_for_batch(repo, style, args.limit))
        already_done = total_for_batch - len(remaining)

        # Update state
        state["current"] = [repo, style, mode]
        save_state(state)

        # Show progress
        batch_num += 1
        if already_done > 0:
            print(
                f"    [{batch_num}/{len(pending)}] {style} "
                f"({len(remaining)} remaining, {already_done} from previous run)...",
                end=" ",
                flush=True,
            )
        else:
            print(
                f"    [{batch_num}/{len(pending)}] {style} ({len(remaining)} bugs)...",
                end=" ",
                flush=True,
            )

        parsed, had_rate_limit = run_batch(repo, style, mode, remaining)

        # Record the successful results (even if batch was partially rate-limited)
        if parsed:
            record_results(state, mode, parsed)
            save_state(state)

        succeeded = sum(1 for r in parsed if not r["rate_limited"])
        rate_limited_count = sum(1 for r in parsed if r["rate_limited"])

        if had_rate_limit:
            print(f"RATE LIMITED ({succeeded} ok, {rate_limited_count} limited)")
            state["rate_limited_at"] = datetime.now().isoformat()
            state["current"] = None
            save_state(state)
            print()
            print("Rate limit detected. Partial progress saved.")
            print(f"Individual bugs completed: {_count_done(state)}")
            print("Run this script again after rate limit resets.")
            return

        if parsed:
            print(f"done ({succeeded}/{len(remaining)})")
        else:
            print("FAILED (will retry next run)")

        # Delay between batches
        time.sleep(DELAY_BETWEEN_TRIALS)
        gc.collect()

        if batch_num % 5 == 0:
            print(f"    Pausing {DELAY_BETWEEN_BATCHES}s...")
            time.sleep(DELAY_BETWEEN_BATCHES)

    state["current"] = None
    save_state(state)

    print()
    print("Benchmark complete!")
    print(f"Individual bugs completed: {_count_done(state)}")
    print(f"Results saved to: {RESULTS_DIR}")


def _count_done(state: dict) -> int:
    return sum(len(bugs) for bugs in state.get("completed_bugs", {}).values())


if __name__ == "__main__":
    main()
