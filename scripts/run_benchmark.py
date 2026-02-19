#!/usr/bin/env python3
"""Run benchmark with rate limit handling and resumable progress.

Features:
- Tracks per-bug progress so partial batches are preserved
- Detects rate limit errors and exits gracefully
- Resumes from where it left off, only re-running incomplete bugs
- Supports multiple agents (claude, gemini) with separate state/results
- Supports model selection (e.g., --model haiku, --model sonnet)
- Configurable delays between trials

Usage:
    # Start or resume benchmark (default: claude, default model)
    python scripts/run_benchmark.py

    # Run with specific model
    python scripts/run_benchmark.py --agent claude --model haiku
    python scripts/run_benchmark.py --agent claude --model sonnet

    # Run with gemini agent
    python scripts/run_benchmark.py --agent gemini

    # Reset progress and start fresh
    python scripts/run_benchmark.py --agent claude --model haiku --reset

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
BUGS_PER_STYLE = 20
DELAY_BETWEEN_TRIALS = 2  # seconds between batches
DELAY_BETWEEN_BATCHES = 5  # seconds every 5 batches

# Per-trial delay within a batch (seconds) - keyed by agent name
# Gemini CLI makes many API calls per invocation, needs throttling
AGENT_TRIAL_DELAY = {
    "claude": 0,
    "gemini": 30,
}

ALL_REPOS = ["humanize", "validators", "python-markdown", "more-itertools"]
ALL_STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]
ALL_MODES = ["with_tests", "without_tests"]

def _get_run_id(agent: str, model: str | None) -> str:
    """Return a unique identifier for an agent+model combination."""
    if model:
        return f"{agent}_{model}"
    return agent


def _get_dirs(agent: str, model: str | None) -> tuple[Path, Path]:
    """Return (results_dir, state_file) for an agent+model combination."""
    run_id = _get_run_id(agent, model)
    results_dir = DATA_DIR / "results" / f"benchmark_{run_id}"
    state_file = results_dir / "benchmark_state.json"
    return results_dir, state_file


def load_state(state_file: Path) -> dict:
    """Load progress state from file."""
    if state_file.exists():
        with open(state_file) as f:
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


def save_state(state: dict, state_file: Path, results_dir: Path):
    """Save progress state to file."""
    state["last_updated"] = datetime.now().isoformat()
    results_dir.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def get_bug_ids_for_batch(
    repo: str, style: str, limit: int, catalog_dir: str = "bugs",
) -> list[str]:
    """Load bug IDs from a catalog, limited to first `limit`."""
    catalog = DATA_DIR / catalog_dir / f"{repo}-{style}.json"
    if not catalog.exists():
        return []
    with open(catalog) as f:
        data = json.load(f)
    bug_ids = [b["bug_id"] for b in data.get("bugs", [])]
    return bug_ids[:limit]


def get_pending_bugs(
    state: dict, repo: str, style: str, mode: str, limit: int,
    catalog_dir: str = "bugs",
) -> list[str]:
    """Return bug IDs for this batch that haven't completed yet."""
    all_bugs = get_bug_ids_for_batch(repo, style, limit, catalog_dir)
    done = state.get("completed_bugs", {}).get(mode, {})
    return [b for b in all_bugs if b not in done]


def is_batch_complete(
    state: dict, repo: str, style: str, mode: str, limit: int,
    catalog_dir: str = "bugs",
) -> bool:
    """Check if all bugs in a batch are completed."""
    return len(get_pending_bugs(state, repo, style, mode, limit, catalog_dir)) == 0


def parse_result_file(result_file: Path) -> tuple[list[dict], bool]:
    """Parse a result file and extract per-bug results and rate-limit status.

    Rate-limited trials are NOT stored as result entries (they are dropped by the
    harness). Instead, rate limiting is signaled via metadata.hit_rate_limit.

    Returns (parsed_results, hit_rate_limit).
    """
    if not result_file.exists():
        return [], False
    try:
        with open(result_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return [], False

    # Rate limiting is signaled via metadata (harness drops rate-limited trials)
    metadata = data.get("metadata", {})
    hit_rate_limit = metadata.get("hit_rate_limit", False)

    parsed = []
    for trial in data.get("results", []):
        bug_id = trial.get("bug_id", "")
        evaluation = trial.get("evaluation", "ERROR")
        parsed.append(
            {
                "bug_id": bug_id,
                "evaluation": evaluation,
            }
        )
    return parsed, hit_rate_limit


def find_result_file_after(results_dir: Path, timestamp: float) -> Path | None:
    """Find the newest result file created after `timestamp`."""
    candidates = []
    for f in results_dir.glob("results_*.json"):
        if f.stat().st_mtime >= timestamp:
            candidates.append(f)
    if candidates:
        return max(candidates, key=lambda x: x.stat().st_mtime)
    return None


def run_batch(
    agent: str,
    model: str | None,
    repo: str,
    style: str,
    mode: str,
    bug_ids: list[str],
    results_dir: Path,
    manifest_path: Path | None = None,
    catalog_dir: str = "bugs",
) -> tuple[list[dict], bool]:
    """Run a batch for specific bug IDs.

    Returns (parsed_results, had_rate_limit).
    """
    catalog = DATA_DIR / catalog_dir / f"{repo}-{style}.json"
    repo_path = DATA_DIR / style / repo

    if not catalog.exists():
        print(f"    Catalog not found: {catalog}")
        return [], False

    if not repo_path.exists():
        print(f"    Repo not found: {repo_path}")
        return [], False

    trial_delay = AGENT_TRIAL_DELAY.get(agent, 0)
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
        agent,
        "--mode",
        mode,
        "--output-dir",
        str(results_dir),
        "--trial-delay",
        str(trial_delay),
        "--quiet",
    ]

    if model:
        cmd.extend(["--model", model])

    if manifest_path:
        cmd.extend(["--manifest", str(manifest_path)])

    cmd.extend(["--bugs", *bug_ids])

    before = time.time()
    proc = None
    try:
        proc = subprocess.run(
            cmd,
            cwd="/Users/yuan/stylebench",
            capture_output=True,
            text=True,
            timeout=3600,  # 60 min max per batch
        )
    except subprocess.TimeoutExpired:
        print("    Timeout!", end=" ")
    except Exception as e:
        print(f"    Error: {e}", end=" ")

    # Always try to parse results, even after timeout (runner may have written partial results)
    result_file = find_result_file_after(results_dir, before)
    if result_file is None:
        if proc is not None and proc.returncode != 0:
            stderr_snippet = (proc.stderr or "").strip()[-500:]
            print(f"\n    Runner failed (exit {proc.returncode}): {stderr_snippet}", end=" ")
        return [], False

    parsed, hit_rate_limit = parse_result_file(result_file)
    return parsed, hit_rate_limit


def record_results(state: dict, mode: str, parsed: list[dict]):
    """Record per-bug results into state.

    Rate-limited trials are never present in parsed results (dropped by harness),
    so all entries here are genuine results.
    """
    if mode not in state["completed_bugs"]:
        state["completed_bugs"][mode] = {}
    for r in parsed:
        state["completed_bugs"][mode][r["bug_id"]] = r["evaluation"]


def main():
    parser = argparse.ArgumentParser(description="Run benchmark with rate limit handling")
    parser.add_argument("--agent", default="claude", help="Agent to use (claude, gemini)")
    parser.add_argument("--model", default=None, help="Model to use (e.g., haiku, sonnet, opus)")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start fresh")
    parser.add_argument("--repos", nargs="+", default=ALL_REPOS, help="Repos to test")
    parser.add_argument("--styles", nargs="+", default=ALL_STYLES, help="Styles to test")
    parser.add_argument("--modes", nargs="+", default=ALL_MODES, help="Modes to test")
    parser.add_argument("--limit", type=int, default=BUGS_PER_STYLE, help="Bugs per batch")
    parser.add_argument(
        "--catalog-dir",
        default="bugs",
        help="Bug catalog subdirectory under data dir "
        "(default: bugs, use bugs_canonical for canonical)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Path to trial manifest JSON (for controlled runs with pre-captured test output)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompts (for non-interactive use)",
    )
    args = parser.parse_args()

    run_id = _get_run_id(args.agent, args.model)
    results_dir, state_file = _get_dirs(args.agent, args.model)
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load or reset state
    if args.reset:
        state = _empty_state()
        state["started_at"] = datetime.now().isoformat()
        state["config"] = {"agent": args.agent, "model": args.model}
        save_state(state, state_file, results_dir)
        print("Progress reset.")
    else:
        state = load_state(state_file)
        # Migrate old state format if needed
        if "completed_bugs" not in state:
            state["completed_bugs"] = {}
        if "completed_batches" not in state:
            state["completed_batches"] = state.pop("completed", [])

    if state.get("started_at") is None:
        state["started_at"] = datetime.now().isoformat()
        state["config"] = {"agent": args.agent, "model": args.model}
        save_state(state, state_file, results_dir)

    # Build list of batches
    all_batches = [
        (repo, style, mode) for mode in args.modes for repo in args.repos for style in args.styles
    ]

    # Filter out fully completed batches
    pending = [
        b for b in all_batches
        if not is_batch_complete(state, *b, args.limit, args.catalog_dir)
    ]

    # Count total completed bugs across all modes
    total_done_bugs = sum(len(bugs) for bugs in state.get("completed_bugs", {}).values())

    print(f"Benchmark Progress ({run_id})")
    print("=" * 60)
    print(f"Agent: {args.agent}")
    print(f"Model: {args.model or '(default)'}")
    print(f"Total batches: {len(all_batches)}")
    print(f"Fully completed batches: {len(all_batches) - len(pending)}")
    print(f"Batches with remaining work: {len(pending)}")
    print(f"Individual bugs completed: {total_done_bugs}")
    print(f"Results dir: {results_dir}")
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
            if args.yes:
                print("Continuing (--yes flag).")
            else:
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
        remaining = get_pending_bugs(state, repo, style, mode, args.limit, args.catalog_dir)
        if not remaining:
            continue

        total_for_batch = len(get_bug_ids_for_batch(repo, style, args.limit, args.catalog_dir))
        already_done = total_for_batch - len(remaining)

        # Update state
        state["current"] = [repo, style, mode]
        save_state(state, state_file, results_dir)

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

        parsed, had_rate_limit = run_batch(
            args.agent, args.model, repo, style, mode, remaining, results_dir,
            manifest_path=Path(args.manifest) if args.manifest else None,
            catalog_dir=args.catalog_dir,
        )

        # Record the successful results (even if batch was partially rate-limited)
        if parsed:
            record_results(state, mode, parsed)
            save_state(state, state_file, results_dir)

        if had_rate_limit:
            print(f"RATE LIMITED ({len(parsed)} ok, batch stopped)")
            state["rate_limited_at"] = datetime.now().isoformat()
            state["current"] = None
            save_state(state, state_file, results_dir)
            print()
            print("Rate limit detected. Partial progress saved.")
            print(f"Individual bugs completed: {_count_done(state)}")
            print("Run this script again after rate limit resets.")
            return
        elif parsed:
            print(f"done ({len(parsed)}/{len(remaining)})")
        else:
            print("FAILED (will retry next run)")

        # Delay between batches
        time.sleep(DELAY_BETWEEN_TRIALS)
        gc.collect()

        if batch_num % 5 == 0:
            print(f"    Pausing {DELAY_BETWEEN_BATCHES}s...")
            time.sleep(DELAY_BETWEEN_BATCHES)

    state["current"] = None
    save_state(state, state_file, results_dir)

    print()
    print("Benchmark complete!")
    print(f"Individual bugs completed: {_count_done(state)}")
    print(f"Results saved to: {results_dir}")


def _count_done(state: dict) -> int:
    return sum(len(bugs) for bugs in state.get("completed_bugs", {}).values())


if __name__ == "__main__":
    main()
