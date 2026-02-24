#!/usr/bin/env python3
"""
Set up data repos for bug generation.

Initializes git repos and installs test dependencies for each repo/style variant.
"""

import subprocess
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "stylebench-data"

REPOS = {
    "humanize": {"test_deps": ["freezegun"], "ignore": []},
    "validators": {"test_deps": ["validators[crypto-eth-addresses]"], "ignore": []},
    "python-markdown": {
        "test_deps": ["pyyaml"],
        "ignore": ["tests/test_syntax/extensions/test_md_in_html.py"],
    },
    "more-itertools": {"test_deps": [], "ignore": []},
}

STYLES = ["original", "camelcase", "badnames", "formatting"]


def setup_repo(repo_path: Path, test_deps: list[str], ignore: list[str]) -> bool:
    """Set up a single repo: git init, uv sync, install pytest."""
    if not repo_path.exists():
        print("    NOT FOUND")
        return False

    try:
        # Initialize git if needed
        if not (repo_path / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
            subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "Initial"],
                cwd=repo_path,
                check=True,
            )

        # Sync dependencies
        subprocess.run(
            ["uv", "sync", "-q"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )

        # Install pytest and test deps
        deps = ["pytest"] + test_deps
        subprocess.run(
            ["uv", "pip", "install", "-q"] + deps,
            cwd=repo_path,
            check=True,
            capture_output=True,
        )

        # Build ignore flags
        ignore_flags = []
        for pattern in ignore:
            ignore_flags.extend(["--ignore", pattern])

        # Quick test run
        result = subprocess.run(
            [".venv/bin/pytest", "tests/", "-q", "--color=no", "-x"] + ignore_flags,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Check result
        if result.returncode == 0:
            # Extract pass count from output
            last_line = result.stdout.strip().split("\n")[-1]
            print(f"    OK: {last_line}")
            return True
        else:
            # Show failure summary
            for line in result.stdout.split("\n"):
                if "failed" in line.lower() or "error" in line.lower():
                    print(f"    FAIL: {line}")
                    break
            return False

    except subprocess.TimeoutExpired:
        print("    TIMEOUT")
        return False
    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def main():
    print(f"Setting up data repos in {DATA_DIR}")
    print()

    results = {}

    for repo_name, config in REPOS.items():
        print(f"[{repo_name}]")
        results[repo_name] = {}

        for style in STYLES:
            print(f"  {style}:", end=" ", flush=True)
            repo_path = DATA_DIR / style / repo_name
            success = setup_repo(repo_path, config["test_deps"], config.get("ignore", []))
            results[repo_name][style] = success

        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total = 0
    passing = 0

    for repo_name in REPOS:
        for style in STYLES:
            total += 1
            if results[repo_name].get(style):
                passing += 1

    print(f"Passing: {passing}/{total}")

    # Show failing variants
    print("\nFailing variants:")
    for repo_name in REPOS:
        for style in STYLES:
            if not results[repo_name].get(style):
                print(f"  {repo_name}/{style}")


if __name__ == "__main__":
    main()
