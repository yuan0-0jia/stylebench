"""Base classes and data structures for StyleBench agents."""

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..evaluator import detect_changes, hash_source_files

# Patterns that indicate the agent was rate-limited rather than genuinely failing.
# Shared across all agent implementations.
DEFAULT_TIMEOUT = 60
"""Default agent timeout in seconds. All agents use this unless overridden."""

RATE_LIMIT_PATTERNS = [
    "out of extra usage",
    "hit your limit",
    "rate limit",
    "too many requests",
    "429",
    "request limit reached",
    "tokens per min",
    "requests per min",
]


@dataclass
class BugContext:
    """Context provided to an agent for fixing a bug.

    The agent receives:
    - Path to the buggy repository (working copy)
    - Test failure output (what went wrong)
    - List of failing test names
    - Mode indicating test file access

    The agent does NOT receive:
    - The actual bug location (file/line)
    - The diff showing what was mutated
    - The original code before mutation
    """

    repo_path: Path
    """Path to the buggy repository working copy."""

    test_output: str
    """Test failure output from running pytest."""

    failing_tests: list[str]
    """List of failing test names (e.g., 'tests/test_foo.py::test_bar')."""

    mode: str
    """Access mode: 'with_tests' or 'without_tests'."""

    bug_id: str = ""
    """Bug identifier from the catalog."""

    repo_name: str = ""
    """Name of the repository (e.g., 'humanize')."""

    style: str = ""
    """Style variant (e.g., 'original', 'camelcase')."""

    def __post_init__(self):
        if self.mode not in ("with_tests", "without_tests"):
            raise ValueError(f"Invalid mode: {self.mode}. Must be 'with_tests' or 'without_tests'")


@dataclass
class FixResult:
    """Result of an agent's fix attempt.

    Captures what the agent did (or failed to do) when attempting to fix a bug.
    """

    success: bool
    """Whether the agent produced any fix (made changes to files)."""

    files_changed: list[str] = field(default_factory=list)
    """List of files modified by the agent."""

    patch: str = ""
    """Git diff of the changes made."""

    tokens_used: int = 0
    """Approximate token count used (if available from agent)."""

    time_seconds: float = 0.0
    """Wall clock time taken for the fix attempt."""

    error: str | None = None
    """Error message if the fix attempt failed."""

    agent_output: str = ""
    """Raw output from the agent (for debugging)."""

    rate_limited: bool = False
    """Whether the agent was rate-limited by the API."""


@dataclass
class TrialResult:
    """Complete result of a benchmark trial.

    Combines the bug context, agent's fix attempt, and evaluation results.
    """

    bug_id: str
    """Bug identifier from the catalog."""

    agent: str
    """Agent name (e.g., 'claude', 'gpt4')."""

    mode: str
    """Access mode: 'with_tests' or 'without_tests'."""

    fix_result: FixResult
    """Result of the agent's fix attempt."""

    evaluation: str
    """Evaluation result: 'PASS', 'FAIL', 'ERROR', 'TIMEOUT', 'NO_FIX'."""

    tests_passed: int = 0
    """Number of tests that passed after the fix."""

    tests_failed: int = 0
    """Number of tests that failed after the fix."""

    tests_total: int = 0
    """Total number of tests run."""

    repo_name: str = ""
    """Repository name."""

    style: str = ""
    """Style variant."""

    mutation_type: str = ""
    """Type of mutation that created the bug."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    """When the trial was run."""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "bug_id": self.bug_id,
            "agent": self.agent,
            "mode": self.mode,
            "evaluation": self.evaluation,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_total": self.tests_total,
            "repo_name": self.repo_name,
            "style": self.style,
            "mutation_type": self.mutation_type,
            "timestamp": self.timestamp,
            "fix_result": {
                "success": self.fix_result.success,
                "files_changed": self.fix_result.files_changed,
                "patch": self.fix_result.patch,
                "tokens_used": self.fix_result.tokens_used,
                "time_seconds": self.fix_result.time_seconds,
                "error": self.fix_result.error,
                "agent_output": self.fix_result.agent_output,
                "rate_limited": self.fix_result.rate_limited,
            },
        }


class Agent(ABC):
    """Abstract base class for coding agents.

    Subclasses implement `_build_command()` to define CLI invocation.
    The base class handles everything else: prompting, hashing,
    subprocess execution, rate-limit detection, and result construction.
    """

    name: str = "base"
    """Agent name for identification in results."""

    cli_not_found_msg: str = "CLI not found. Is it installed?"
    """Error message when the CLI binary is not found."""

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        model: str | None = None,
    ):
        self.timeout = timeout
        self.model = model

    def build_prompt(self, context: BugContext) -> str:
        """Build the standard prompt for fixing a bug.

        All agents receive the same prompt to ensure fair comparison.
        """
        prompt = f"""The tests in this repository are failing.
Find and fix the bug.

Test failure output:
{context.test_output}

Instructions:
- Read the source code files to find the bug location
- Edit the source code to fix the bug
- Do NOT modify any test files
- Make minimal changes to fix the issue
- The bug is likely a simple logic error, off-by-one error, or similar"""

        if context.mode == "without_tests":
            prompt += """
- You do not have access to the test files
- Focus on understanding the code logic to find the bug
- Do not attempt to run tests; make your best fix and stop"""
        else:
            prompt += """
- You may read test files to understand expected behavior
- But do NOT modify test files"""

        return prompt

    @abstractmethod
    def _build_command(self, prompt: str, context: BugContext) -> list[str]:
        """Build the CLI command to run. Subclasses must implement."""
        pass

    def _get_env(self) -> dict | None:
        """Return custom environment for subprocess, or None for default."""
        return None

    def _run_subprocess(
        self, cmd: list[str], context: BugContext,
    ) -> subprocess.CompletedProcess:
        """Run the agent subprocess. Override for retry logic etc."""
        return subprocess.run(
            cmd,
            cwd=context.repo_path,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=self._get_env(),
        )

    def fix_bug(self, context: BugContext) -> FixResult:
        """Attempt to fix a bug by running the agent CLI."""
        start_time = time.time()
        prompt = self.build_prompt(context)
        cmd = self._build_command(prompt, context)

        try:
            before_hashes = hash_source_files(context.repo_path)

            result = self._run_subprocess(cmd, context)

            elapsed = time.time() - start_time
            agent_output = result.stdout + result.stderr

            after_hashes = hash_source_files(context.repo_path)

            output_lower = agent_output.lower()
            was_rate_limited = any(
                p in output_lower for p in RATE_LIMIT_PATTERNS
            )

            fix_attempted = before_hashes != after_hashes
            changed_files = detect_changes(before_hashes, after_hashes)

            return FixResult(
                success=fix_attempted,
                files_changed=changed_files,
                patch="(hash-based change detection)",
                time_seconds=elapsed,
                agent_output=agent_output,
                error=(
                    None if result.returncode == 0
                    else f"Exit code: {result.returncode}"
                ),
                rate_limited=was_rate_limited,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=f"Timeout after {self.timeout} seconds",
            )

        except FileNotFoundError:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=self.cli_not_found_msg,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=str(e),
            )

    def get_name(self) -> str:
        """Return the agent's name."""
        return self.name
