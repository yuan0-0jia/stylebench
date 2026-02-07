"""Evaluator for scoring agent bug fixes.

Provides functions to:
- Apply a bug (mutation) to a repository
- Run tests and capture results
- Evaluate whether a fix was successful
"""

import atexit
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bugs.repo_config import get_config

# Track active subprocesses for cleanup
_active_processes: set[subprocess.Popen] = set()


def _cleanup_all_processes():
    """Kill all tracked subprocesses on exit."""
    for proc in list(_active_processes):
        try:
            if proc.poll() is None:  # Still running
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                proc.kill()
                proc.wait()
        except Exception:
            pass
    _active_processes.clear()


# Register cleanup on exit
atexit.register(_cleanup_all_processes)


def _run_with_cleanup(cmd, cwd, timeout, capture_output=True, text=True):
    """Run a subprocess with process group cleanup on timeout/interrupt."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        start_new_session=True,  # Create new process group
    )

    _active_processes.add(proc)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)

        class Result:
            pass

        result = Result()
        result.stdout = stdout or ""
        result.stderr = stderr or ""
        result.returncode = proc.returncode
        return result
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.kill()
        proc.wait()
        raise
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.kill()
        proc.wait()
        raise
    finally:
        _active_processes.discard(proc)


@dataclass
class TestRunResult:
    """Result of running tests on a repository.

    Named TestRunResult (not TestResult) to avoid pytest collection.
    """

    exit_code: int
    """Exit code from pytest (0 = all passed)."""

    passed: int
    """Number of tests that passed."""

    failed: int
    """Number of tests that failed."""

    total: int
    """Total number of tests run."""

    output: str
    """Full test output."""

    failing_tests: list[str]
    """List of failing test names."""


def load_bug_catalog(catalog_path: Path) -> dict:
    """Load a bug catalog from JSON file.

    Args:
        catalog_path: Path to the catalog JSON file.

    Returns:
        Dictionary with 'bugs' and '_hidden' keys.
    """
    with open(catalog_path) as f:
        return json.load(f)


def get_bug_by_id(catalog: dict, bug_id: str) -> tuple[dict, dict] | None:
    """Get a bug entry and its hidden metadata by ID.

    Args:
        catalog: Loaded bug catalog.
        bug_id: Bug identifier (e.g., 'humanize-original-001').

    Returns:
        Tuple of (bug_entry, hidden_metadata) or None if not found.
    """
    for i, bug in enumerate(catalog.get("bugs", [])):
        if bug.get("bug_id") == bug_id:
            hidden = catalog.get("_hidden", [])[i]
            return bug, hidden
    return None


def create_working_copy(source_repo: Path, dest_dir: Path | None = None) -> Path:
    """Create a working copy of a repository.

    Excludes .venv, __pycache__, and .pytest_cache to ensure fresh builds.

    Args:
        source_repo: Path to the source repository.
        dest_dir: Optional destination directory. If None, creates a temp dir.

    Returns:
        Path to the working copy.
    """
    if dest_dir is None:
        dest_dir = Path(tempfile.mkdtemp(prefix="stylebench_"))

    # Directories to exclude (contain cached/compiled code)
    exclude_dirs = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

    def ignore_patterns(directory: str, files: list[str]) -> list[str]:
        """Return files to ignore during copy."""
        return [f for f in files if f in exclude_dirs]

    # Copy the repository, excluding cache directories
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(source_repo, dest_dir, symlinks=True, ignore=ignore_patterns)

    return dest_dir


def apply_bug(repo_path: Path, hidden_metadata: dict) -> bool:
    """Apply a bug (mutation) to a repository.

    Args:
        repo_path: Path to the repository working copy.
        hidden_metadata: Hidden metadata from the bug catalog containing
                        file_path, original_text, mutated_text, line_number.

    Returns:
        True if the bug was applied successfully.
    """
    file_path = repo_path / hidden_metadata["file_path"]

    if not file_path.exists():
        return False

    try:
        content = file_path.read_text()
        lines = content.split("\n")

        original = hidden_metadata["original_text"]
        mutated = hidden_metadata["mutated_text"]
        line_number = hidden_metadata.get("line_number", 0)

        # Use line number if available to find the correct mutation location
        if line_number > 0 and line_number <= len(lines):
            # 1-indexed line number
            target_line_idx = line_number - 1
            target_line = lines[target_line_idx]

            if original in target_line:
                # Replace only on the target line
                lines[target_line_idx] = target_line.replace(original, mutated, 1)
                new_content = "\n".join(lines)

                if new_content != content:
                    file_path.write_text(new_content)
                    return True

        # Fallback: try context-based matching if available
        context = hidden_metadata.get("context", "")
        if context and len(context) > 10:
            # Extract a unique substring from context that contains the original
            # Context format: "...surrounding code with mutation..."
            context_clean = context.strip(".")
            if context_clean in content:
                # Replace within the context match
                mutated_context = context_clean.replace(original, mutated, 1)
                new_content = content.replace(context_clean, mutated_context, 1)

                if new_content != content:
                    file_path.write_text(new_content)
                    return True

        # Last resort: simple replacement (may be wrong if multiple occurrences)
        if original not in content:
            return False

        new_content = content.replace(original, mutated, 1)

        if new_content == content:
            return False

        file_path.write_text(new_content)
        return True

    except Exception:
        return False


def revert_bug(repo_path: Path, hidden_metadata: dict) -> bool:
    """Revert a bug (mutation) in a repository.

    Args:
        repo_path: Path to the repository working copy.
        hidden_metadata: Hidden metadata from the bug catalog.

    Returns:
        True if the bug was reverted successfully.
    """
    file_path = repo_path / hidden_metadata["file_path"]

    if not file_path.exists():
        return False

    try:
        content = file_path.read_text()

        # Reverse the mutation
        original = hidden_metadata["original_text"]
        mutated = hidden_metadata["mutated_text"]

        if mutated not in content:
            return False

        new_content = content.replace(mutated, original, 1)
        file_path.write_text(new_content)
        return True

    except Exception:
        return False


def run_tests(repo_path: Path, repo_name: str, timeout: int = 120) -> TestRunResult:
    """Run tests on a repository and capture results.

    Args:
        repo_path: Path to the repository.
        repo_name: Name of the repository (for config lookup).
        timeout: Timeout in seconds for test execution.

    Returns:
        TestRunResult with pass/fail counts and output.
    """
    config = get_config(repo_name)
    test_command = config.get_test_command(repo_path, external=False)

    try:
        # First ensure dependencies are synced (installs package + test deps)
        _run_with_cleanup(
            ["uv", "sync", "--all-extras"],
            cwd=repo_path,
            timeout=60,
        )

        result = _run_with_cleanup(
            test_command,
            cwd=repo_path,
            timeout=timeout,
        )

        output = result.stdout + result.stderr
        exit_code = result.returncode

        # Parse test results from output
        passed, failed, total = _parse_test_counts(output)
        failing_tests = _parse_failing_tests(output)

        return TestRunResult(
            exit_code=exit_code,
            passed=passed,
            failed=failed,
            total=total,
            output=output,
            failing_tests=failing_tests,
        )

    except subprocess.TimeoutExpired:
        return TestRunResult(
            exit_code=-1,
            passed=0,
            failed=0,
            total=0,
            output="TIMEOUT: Test execution exceeded time limit",
            failing_tests=[],
        )
    except Exception as e:
        return TestRunResult(
            exit_code=-1,
            passed=0,
            failed=0,
            total=0,
            output=f"ERROR: {e}",
            failing_tests=[],
        )


def _parse_test_counts(output: str) -> tuple[int, int, int]:
    """Parse test pass/fail counts from pytest output.

    Args:
        output: Pytest output string.

    Returns:
        Tuple of (passed, failed, total).
    """
    passed = 0
    failed = 0

    # Look for summary line like "5 passed, 2 failed"
    for line in output.split("\n"):
        line_lower = line.lower()
        if "passed" in line_lower or "failed" in line_lower:
            # Parse numbers
            import re

            passed_match = re.search(r"(\d+)\s+passed", line_lower)
            failed_match = re.search(r"(\d+)\s+failed", line_lower)

            if passed_match:
                passed = int(passed_match.group(1))
            if failed_match:
                failed = int(failed_match.group(1))

    total = passed + failed
    return passed, failed, total


def _parse_failing_tests(output: str) -> list[str]:
    """Parse failing test names from pytest output.

    Args:
        output: Pytest output string.

    Returns:
        List of failing test names.
    """
    failing = []
    for line in output.split("\n"):
        if line.startswith("FAILED "):
            # Extract test name
            test_name = line.split(" ")[1].split(" -")[0]
            failing.append(test_name)
    return failing


def evaluate_fix(
    before_result: TestRunResult,
    after_result: TestRunResult,
    original_failing: list[str],
) -> str:
    """Evaluate whether a fix was successful.

    Args:
        before_result: Test results before agent's fix (should have failures).
        after_result: Test results after agent's fix.
        original_failing: Original list of failing tests from the bug.

    Returns:
        Evaluation string: 'PASS', 'FAIL', 'ERROR', or 'TIMEOUT'.
    """
    # Timeout
    if after_result.exit_code == -1 and "TIMEOUT" in after_result.output:
        return "TIMEOUT"

    # Error running tests
    if after_result.exit_code == -1:
        return "ERROR"

    # All tests pass = successful fix
    if after_result.exit_code == 0 and after_result.failed == 0:
        return "PASS"

    # Check if the fix made things worse (more failures than before)
    if after_result.failed > before_result.failed:
        return "ERROR"

    # Still has failures
    return "FAIL"


def get_git_diff(repo_path: Path) -> str:
    """Get git diff of changes in a repository.

    Args:
        repo_path: Path to the repository.

    Returns:
        Git diff string, or empty string if no changes or not a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def get_changed_files(repo_path: Path) -> list[str]:
    """Get list of files changed in a repository.

    Args:
        repo_path: Path to the repository.

    Returns:
        List of changed file paths relative to repo root.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return files
    except Exception:
        return []


def hide_tests(repo_path: Path, repo_name: str) -> Path | None:
    """Hide test directory for 'without_tests' mode.

    Moves the test directory to a temporary location.

    Args:
        repo_path: Path to the repository.
        repo_name: Name of the repository.

    Returns:
        Path where tests were moved, or None if no tests found.
    """
    config = get_config(repo_name)
    test_dir = repo_path / config.test_dir

    if not test_dir.exists():
        return None

    # Move to temp location
    hidden_path = repo_path / f".{config.test_dir}_hidden"
    shutil.move(test_dir, hidden_path)

    return hidden_path


def restore_tests(repo_path: Path, hidden_path: Path, repo_name: str) -> bool:
    """Restore hidden test directory.

    Args:
        repo_path: Path to the repository.
        hidden_path: Path where tests were hidden.
        repo_name: Name of the repository.

    Returns:
        True if tests were restored successfully.
    """
    config = get_config(repo_name)
    test_dir = repo_path / config.test_dir

    if not hidden_path.exists():
        return False

    try:
        if test_dir.exists():
            shutil.rmtree(test_dir)
        shutil.move(hidden_path, test_dir)
        return True
    except Exception:
        return False
