"""Base classes and data structures for StyleBench agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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

    Subclasses must implement the `fix_bug` method to attempt fixing a bug
    given the context (test output, repo path, etc.).
    """

    name: str = "base"
    """Agent name for identification in results."""

    @abstractmethod
    def fix_bug(self, context: BugContext) -> FixResult:
        """Attempt to fix a bug given the context.

        Args:
            context: Bug context with test output, repo path, and mode.

        Returns:
            FixResult with success status, patch, timing, etc.
        """
        pass

    def get_name(self) -> str:
        """Return the agent's name."""
        return self.name
