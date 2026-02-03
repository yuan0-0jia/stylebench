"""
Repository configuration for bug injection.

Defines test commands, source directories, and dependencies for each target repo.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoConfig:
    """Configuration for a target repository."""

    name: str
    source_dir: str  # Relative to repo root
    test_dir: str  # Relative to repo root
    test_deps: list[str]  # Additional test dependencies
    pythonpath_dir: str | None = None  # If different from source_dir
    ignore_patterns: list[str] | None = None  # Test files/dirs to ignore

    def get_pythonpath(self, repo_path: Path) -> str:
        """Get the PYTHONPATH for running tests."""
        if self.pythonpath_dir:
            return str(repo_path / self.pythonpath_dir)
        return str(repo_path / self.source_dir)

    def get_test_command(self, repo_path: Path, external: bool = False) -> list[str]:
        """
        Get the test command for this repo.

        Args:
            repo_path: Path to the repository
            external: If True, use PYTHONPATH approach for running from outside repo
        """
        # Build ignore flags
        ignore_flags = []
        if self.ignore_patterns:
            for pattern in self.ignore_patterns:
                ignore_flags.extend(["--ignore", str(repo_path / pattern)])

        if external:
            # Running from outside the repo - use --with to install deps
            deps = ["--with", "pytest"]
            for dep in self.test_deps:
                deps.extend(["--with", dep])
            test_path = str(repo_path / self.test_dir)
            return [
                "uv", "run", *deps, "pytest", test_path,
                "-x", "-q", "--tb=short", "--color=no", *ignore_flags
            ]
        else:
            # Running from inside the repo - use uv run directly
            # Specify test directory explicitly for proper ignore handling
            test_path = str(repo_path / self.test_dir)
            cmd = ["uv", "run", "pytest", test_path, "-x", "-q", "--tb=short", "--color=no"]
            return cmd + ignore_flags

    def get_source_path(self, repo_path: Path) -> Path:
        """Get the source directory path."""
        return repo_path / self.source_dir


# Repository configurations
REPO_CONFIGS = {
    "humanize": RepoConfig(
        name="humanize",
        source_dir="src/humanize",
        test_dir="tests",
        test_deps=["freezegun"],
        pythonpath_dir="src",
    ),
    "validators": RepoConfig(
        name="validators",
        source_dir="src/validators",
        test_dir="tests",
        test_deps=["validators[crypto-eth-addresses]"],
        pythonpath_dir="src",
    ),
    "python-markdown": RepoConfig(
        name="python-markdown",
        source_dir="markdown",
        test_dir="tests",
        test_deps=["pyyaml"],
        pythonpath_dir=".",
        ignore_patterns=["tests/test_syntax/extensions/test_md_in_html.py"],
    ),
    "more-itertools": RepoConfig(
        name="more-itertools",
        source_dir="more_itertools",
        test_dir="tests",
        test_deps=[],
        pythonpath_dir=".",
    ),
}


def get_config(repo_name: str) -> RepoConfig:
    """Get configuration for a repository."""
    if repo_name not in REPO_CONFIGS:
        raise ValueError(
            f"Unknown repo: {repo_name}. "
            f"Available: {list(REPO_CONFIGS.keys())}"
        )
    return REPO_CONFIGS[repo_name]
