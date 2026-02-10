#!/usr/bin/env python3
"""Run pilot study with rate limiting and memory safety.

Runs 10 bugs per style × 5 styles × 2 modes = 100 total trials.
Sequential execution with delays to avoid rate limits.
"""

import gc
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

# Configuration
DATA_DIR = Path("/Users/yuan/stylebench-data")
RESULTS_DIR = DATA_DIR / "results" / "pilot_validated"
BUGS_PER_STYLE = 10
DELAY_BETWEEN_TRIALS = 2  # seconds
DELAY_BETWEEN_REPOS = 10  # seconds

# Repos to test (excluding humanize which achieved 100%)
REPOS = ["validators", "python-markdown", "more-itertools"]
STYLES = ["original", "camelcase", "snakecase", "badnames", "formatting"]
MODES = ["with_tests", "without_tests"]


def run_trial(repo: str, style: str, mode: str, limit: int) -> dict:
    """Run a single trial batch and return results."""
    catalog = DATA_DIR / "bugs" / f"{repo}-{style}.json"
    repo_path = DATA_DIR / style / repo

    if not catalog.exists():
        print(f"  Catalog not found: {catalog}")
        return {"error": "catalog_not_found"}

    if not repo_path.exists():
        print(f"  Repo not found: {repo_path}")
        return {"error": "repo_not_found"}

    cmd = [
        "uv", "run", "python", "-m", "benchmarks.runner",
        "--catalog", str(catalog),
        "--repo", str(repo_path),
        "--repo-name", repo,
        "--agent", "claude",
        "--mode", mode,
        "--limit", str(limit),
        "--output-dir", str(RESULTS_DIR),
        "--quiet",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd="/Users/yuan/stylebench",
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max per batch
        )

        # Parse output for summary
        output = result.stdout + result.stderr
        return {"returncode": result.returncode, "output": output[-500:]}

    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}


def main():
    """Run the pilot study."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Summary tracking
    summary = {
        "start_time": datetime.now().isoformat(),
        "config": {
            "bugs_per_style": BUGS_PER_STYLE,
            "repos": REPOS,
            "styles": STYLES,
            "modes": MODES,
        },
        "results": {},
    }

    total_batches = len(REPOS) * len(STYLES) * len(MODES)
    current = 0

    print(f"Starting pilot study: {total_batches} batches")
    n_styles, n_modes, n_repos = len(STYLES), len(MODES), len(REPOS)
    print(f"  {BUGS_PER_STYLE} bugs × {n_styles} styles × {n_modes} modes × {n_repos} repos")
    print(f"  Total trials: {BUGS_PER_STYLE * len(STYLES) * len(MODES) * len(REPOS)}")
    print()

    for mode in MODES:
        print(f"=== Mode: {mode} ===")
        summary["results"][mode] = {}

        for repo in REPOS:
            print(f"\n  Repository: {repo}")
            summary["results"][mode][repo] = {}

            for style in STYLES:
                current += 1
                print(f"    [{current}/{total_batches}] {style}...", end=" ", flush=True)

                result = run_trial(repo, style, mode, BUGS_PER_STYLE)
                summary["results"][mode][repo][style] = result

                if "error" in result:
                    print(f"ERROR: {result['error']}")
                else:
                    print(f"done (exit={result['returncode']})")

                # Delay between trials
                time.sleep(DELAY_BETWEEN_TRIALS)

                # Force garbage collection
                gc.collect()

            # Longer delay between repos
            print(f"    Waiting {DELAY_BETWEEN_REPOS}s before next repo...")
            time.sleep(DELAY_BETWEEN_REPOS)

        print()

    # Save summary
    summary["end_time"] = datetime.now().isoformat()
    summary_path = RESULTS_DIR / "pilot_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\nPilot study complete!")
    print(f"Results saved to: {RESULTS_DIR}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
