"""Evaluator for scoring agent bug fixes.

Provides functions to:
- Apply a bug (mutation) to a repository
- Run tests and capture results
- Evaluate whether a fix was successful
"""

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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

    # Directories to exclude (contain cached/compiled code, and .git to prevent
    # agents from using git diff to discover bug locations)
    exclude_dirs = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}

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

        occurrence_count = content.count(original)
        if occurrence_count > 1:
            logger.warning(
                "apply_bug: last-resort fallback used with %d occurrences of original text "
                "in %s (line_number and context matching both failed)",
                occurrence_count,
                hidden_metadata["file_path"],
            )
        else:
            logger.warning(
                "apply_bug: last-resort fallback used for %s (line_number and context matching both failed)",
                hidden_metadata["file_path"],
            )

        new_content = content.replace(original, mutated, 1)

        if new_content == content:
            return False

        file_path.write_text(new_content)
        return True

    except Exception:
        return False


def revert_bug(repo_path: Path, hidden_metadata: dict) -> bool:
    """Revert a bug (mutation) in a repository.

    Uses line-number-based replacement (same approach as apply_bug) with
    fallback to simple replacement for robustness.

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
        lines = content.split("\n")

        # Reverse the mutation
        original = hidden_metadata["original_text"]
        mutated = hidden_metadata["mutated_text"]
        line_number = hidden_metadata.get("line_number", 0)

        # Try line-number-based revert first (mirrors apply_bug logic)
        if line_number > 0 and line_number <= len(lines):
            target_line_idx = line_number - 1
            target_line = lines[target_line_idx]

            if mutated in target_line:
                lines[target_line_idx] = target_line.replace(mutated, original, 1)
                new_content = "\n".join(lines)

                if new_content != content:
                    file_path.write_text(new_content)
                    return True

        # Fallback: simple replacement
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

    Anchors to the pytest summary line (starts with '=' and contains
    result keywords like 'passed', 'failed', 'error', 'skipped').
    Only uses the last matching summary line since pytest prints it at the end.

    Args:
        output: Pytest output string.

    Returns:
        Tuple of (passed, failed, total).
    """
    import re

    passed = 0
    failed = 0
    errors = 0
    skipped = 0

    # Find the last pytest summary line (e.g., "===== 3 passed, 1 failed, 1 error =====")
    last_summary = None
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith("=") and ("passed" in stripped.lower() or "failed" in stripped.lower()
                                         or "error" in stripped.lower()):
            last_summary = stripped

    if last_summary:
        summary_lower = last_summary.lower()
        passed_match = re.search(r"(\d+)\s+passed", summary_lower)
        failed_match = re.search(r"(\d+)\s+failed", summary_lower)
        error_match = re.search(r"(\d+)\s+error", summary_lower)
        skipped_match = re.search(r"(\d+)\s+skipped", summary_lower)

        if passed_match:
            passed = int(passed_match.group(1))
        if failed_match:
            failed = int(failed_match.group(1))
        if error_match:
            errors = int(error_match.group(1))
        if skipped_match:
            skipped = int(skipped_match.group(1))

    total = passed + failed + errors + skipped
    return passed, failed, total


def _parse_failing_tests(output: str) -> list[str]:
    """Parse failing test names from pytest output.

    Handles pytest's "FAILED tests/test_foo.py::test_bar - reason" format.
    Uses " - " (space-dash-space) as the delimiter to avoid breaking on
    test names that contain hyphens or parametrized values with " -".

    Args:
        output: Pytest output string.

    Returns:
        List of failing test names.
    """
    failing = []
    for line in output.split("\n"):
        if line.startswith("FAILED "):
            # Remove "FAILED " prefix, then split on " - " to separate test name from reason
            rest = line.removeprefix("FAILED ")
            test_name = rest.split(" - ")[0].strip()
            if test_name:
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


def hash_source_files(repo_path: Path) -> dict[str, str]:
    """Hash all Python source files in a repository.

    Args:
        repo_path: Path to the repository.

    Returns:
        Dictionary mapping relative file paths to their SHA-256 hashes.
    """
    import hashlib

    hashes = {}
    # Include Python source files and config files that could affect behavior
    patterns = ["*.py", "*.pyi", "*.toml", "*.cfg"]
    for pattern in patterns:
        for src_file in sorted(repo_path.rglob(pattern)):
            # Skip hidden dirs, caches, and venvs
            parts = src_file.relative_to(repo_path).parts
            if any(p.startswith(".") or p in {"__pycache__", ".venv"} for p in parts):
                continue
            try:
                content = src_file.read_bytes()
                rel_path = str(src_file.relative_to(repo_path))
                hashes[rel_path] = hashlib.sha256(content).hexdigest()
            except Exception:
                continue
    return hashes


def detect_changes(before_hashes: dict[str, str], after_hashes: dict[str, str]) -> list[str]:
    """Detect which files changed by comparing hash snapshots.

    Args:
        before_hashes: File hashes before agent ran.
        after_hashes: File hashes after agent ran.

    Returns:
        List of relative file paths that were added, modified, or deleted.
    """
    changed = []
    all_files = set(before_hashes) | set(after_hashes)
    for f in sorted(all_files):
        if before_hashes.get(f) != after_hashes.get(f):
            changed.append(f)
    return changed


def lock_tests(repo_path: Path, repo_name: str) -> None:
    """Make test files read-only so agents cannot modify them.

    Used in 'with_tests' mode where tests are visible but should not be edited.

    Args:
        repo_path: Path to the repository.
        repo_name: Name of the repository.
    """
    config = get_config(repo_name)
    test_dir = repo_path / config.test_dir

    if not test_dir.exists():
        return

    for f in test_dir.rglob("*"):
        if f.is_file():
            f.chmod(0o444)


def unlock_tests(repo_path: Path, repo_name: str) -> None:
    """Restore test files to writable for evaluation.

    Args:
        repo_path: Path to the repository.
        repo_name: Name of the repository.
    """
    config = get_config(repo_name)
    test_dir = repo_path / config.test_dir

    if not test_dir.exists():
        return

    for f in test_dir.rglob("*"):
        if f.is_file():
            f.chmod(0o644)


def hide_tests(repo_path: Path, repo_name: str) -> Path | None:
    """Hide test directory for 'without_tests' mode.

    Moves the test directory to a temp dir outside the repo tree so agents
    cannot discover tests via ls -a, find, or any other file listing.

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

    # Move to a temp dir OUTSIDE the repo tree
    hidden_path = Path(tempfile.mkdtemp(prefix="stylebench_hidden_tests_")) / config.test_dir
    shutil.move(test_dir, hidden_path)

    return hidden_path


def restore_tests(repo_path: Path, hidden_path: Path, repo_name: str) -> bool:
    """Restore hidden test directory.

    Args:
        repo_path: Path to the repository.
        hidden_path: Path where tests were hidden (outside the repo tree).
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
        # Clean up the temp parent directory
        hidden_parent = hidden_path.parent
        if hidden_parent.name.startswith("stylebench_hidden_tests_"):
            shutil.rmtree(hidden_parent, ignore_errors=True)
        return True
    except Exception:
        return False
