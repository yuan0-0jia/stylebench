#!/usr/bin/env python3
"""StyleBench CLI entry point."""

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path


def _resolve_data_dir(cli_flag: str | None = None) -> Path:
    """Resolve the data directory path.

    Resolution order:
    1. --data-dir CLI flag
    2. STYLEBENCH_DATA env var
    3. Sibling ../stylebench-data relative to repo root
    """
    if cli_flag:
        return Path(cli_flag).resolve()

    env = os.environ.get("STYLEBENCH_DATA")
    if env:
        return Path(env).resolve()

    repo_root = Path(__file__).resolve().parent.parent
    sibling = repo_root.parent / "stylebench-data"
    if sibling.exists():
        return sibling

    return sibling  # return default even if not yet cloned


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cmd_run(args: argparse.Namespace) -> int:
    """Run the benchmark."""
    data_dir = _resolve_data_dir(args.data_dir)
    if not data_dir.exists():
        print(f"Error: Data directory not found: {data_dir}", file=sys.stderr)
        print("Run: stylebench setup-data", file=sys.stderr)
        return 1

    env = {**os.environ, "STYLEBENCH_DATA": str(data_dir)}
    cmd = [sys.executable, str(_repo_root() / "scripts" / "run_benchmark.py")]

    # Forward arguments
    if args.agent:
        cmd.extend(["--agent", args.agent])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.repos:
        cmd.extend(["--repos", *args.repos])
    if args.limit is not None:
        cmd.extend(["--limit", str(args.limit)])
    if args.reset:
        cmd.append("--reset")
    if args.yes:
        cmd.append("--yes")

    result = subprocess.run(cmd, cwd=_repo_root(), env=env)
    return result.returncode


def cmd_setup_data(args: argparse.Namespace) -> int:
    """Clone or update the data repository."""
    data_url = "https://github.com/yuan0-0jia/stylebench-data.git"
    target = Path(args.dir).resolve() if args.dir else _resolve_data_dir()

    if target.exists():
        print(f"Updating data repo in {target}...")
        result = subprocess.run(["git", "-C", str(target), "pull", "--ff-only"])
        return result.returncode

    print(f"Cloning data repo to {target}...")
    result = subprocess.run(["git", "clone", data_url, str(target)])
    return result.returncode


def cmd_status(args: argparse.Namespace) -> int:
    """Show benchmark progress."""
    data_dir = _resolve_data_dir(args.data_dir)
    results_dir = data_dir / "results"

    if not results_dir.exists():
        print("No benchmark results found.")
        print(f"Expected results in: {results_dir}")
        return 0

    # Find all benchmark state files
    found = False
    for state_file in sorted(results_dir.glob("benchmark_*/benchmark_state.json")):
        found = True
        run_name = state_file.parent.name
        with open(state_file) as f:
            state = json.load(f)

        total_done = sum(len(bugs) for bugs in state.get("completed_bugs", {}).values())
        config = state.get("config", {})
        agent = config.get("agent", "?")
        model = config.get("model", "(default)")

        # Count by evaluation
        evals: dict[str, int] = {}
        for mode_bugs in state.get("completed_bugs", {}).values():
            for ev in mode_bugs.values():
                evals[ev] = evals.get(ev, 0) + 1

        print(f"{run_name}: agent={agent} model={model}")
        print(f"  Trials completed: {total_done}")
        if evals:
            print(f"  Results: {', '.join(f'{e}: {c}' for e, c in sorted(evals.items()))}")
        if state.get("started_at"):
            print(f"  Started: {state['started_at']}")
        if state.get("last_updated"):
            print(f"  Last update: {state['last_updated']}")
        print()

    if not found:
        print("No benchmark runs found.")
        print(f"Looked in: {results_dir}")

    return 0


def main():
    try:
        version = importlib.metadata.version("stylebench")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"

    parser = argparse.ArgumentParser(
        prog="stylebench",
        description="StyleBench: benchmarking coding agents across code style variants",
    )
    parser.add_argument("--version", action="version", version=f"stylebench {version}")
    parser.add_argument("--data-dir", default=None, help="Path to stylebench-data directory")
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run the benchmark")
    p_run.add_argument("--agent", default="claude", help="Agent to use (claude, gemini)")
    p_run.add_argument("--model", default=None, help="Model (e.g., haiku, sonnet)")
    p_run.add_argument("--repos", nargs="+", default=None, help="Repos to test")
    p_run.add_argument("--limit", type=int, default=None, help="Bugs per style")
    p_run.add_argument("--reset", action="store_true", help="Reset progress")
    p_run.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")

    # setup-data
    p_data = sub.add_parser("setup-data", help="Clone or update the data repository")
    p_data.add_argument("--dir", default=None, help="Target directory for data repo")

    # status
    sub.add_parser("status", help="Show benchmark progress")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "run": cmd_run,
        "setup-data": cmd_setup_data,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
