"""
Bug catalog generator for StyleBench.

Generates bug catalogs that separate:
- Agent-visible data: test failure output only (no diff leakage)
- Hidden metadata: mutation details for scoring (never shown to agent)
"""

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .injector import Injector, MutationSite
from .repo_config import RepoConfig, get_config


@dataclass
class BugEntry:
    """A single bug entry for the catalog."""

    bug_id: str
    test_output: str  # Only this is shown to agent
    failing_tests: list[str]
    exit_code: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HiddenMetadata:
    """Hidden metadata for scoring - never shown to agent."""

    bug_id: str
    file_path: str
    line_number: int
    mutation_type: str
    original_text: str
    mutated_text: str
    context: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BugCatalog:
    """Complete bug catalog with agent-visible and hidden sections."""

    repo: str
    style: str
    generated_at: str
    test_command: list[str]
    bugs: list[BugEntry] = field(default_factory=list)
    _hidden: list[HiddenMetadata] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Export full catalog (for internal use)."""
        return {
            "repo": self.repo,
            "style": self.style,
            "generated_at": self.generated_at,
            "test_command": self.test_command,
            "bugs": [b.to_dict() for b in self.bugs],
            "_hidden": [h.to_dict() for h in self._hidden],
        }

    def to_agent_dict(self) -> dict:
        """Export agent-visible data only (no hidden metadata)."""
        return {
            "repo": self.repo,
            "style": self.style,
            "test_command": self.test_command,
            "bugs": [b.to_dict() for b in self.bugs],
        }

    def to_json(self, include_hidden: bool = True, indent: int = 2) -> str:
        """Export to JSON."""
        if include_hidden:
            return json.dumps(self.to_dict(), indent=indent)
        return json.dumps(self.to_agent_dict(), indent=indent)

    def save(self, path: Path, include_hidden: bool = True) -> None:
        """Save catalog to file."""
        path.write_text(self.to_json(include_hidden=include_hidden, indent=2))

    @classmethod
    def load(cls, path: Path) -> "BugCatalog":
        """Load catalog from file."""
        data = json.loads(path.read_text())
        catalog = cls(
            repo=data["repo"],
            style=data["style"],
            generated_at=data["generated_at"],
            test_command=data["test_command"],
        )
        for b in data.get("bugs", []):
            catalog.bugs.append(BugEntry(**b))
        for h in data.get("_hidden", []):
            catalog._hidden.append(HiddenMetadata(**h))
        return catalog


class CatalogGenerator:
    """Generates bug catalogs from mutations."""

    def __init__(
        self,
        repo_path: str | Path,
        repo_name: str,
        style: str,
        config: RepoConfig | None = None,
        timeout: int = 60,
        working_dir: str | Path | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.repo_name = repo_name
        self.style = style
        self.config = config or get_config(repo_name)
        self.timeout = timeout
        # Run tests from a neutral directory to avoid uv trying to build the target
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.injector = Injector()

    def run_tests(self) -> tuple[int, str, list[str]]:
        """
        Run tests and capture output.

        Returns:
            Tuple of (exit_code, output, failing_test_names)
        """
        try:
            # Get the test command - run from inside the repo directory
            test_command = self.config.get_test_command(self.repo_path, external=False)

            result = subprocess.run(
                test_command,
                cwd=self.repo_path,  # Run from repo directory
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = result.stdout + result.stderr

            # Extract failing test names from pytest output
            failing_tests = []
            for line in output.split("\n"):
                if line.startswith("FAILED "):
                    # Extract test name: "FAILED tests/test_foo.py::test_bar - ..."
                    test_name = line.split(" ")[1].split(" -")[0]
                    failing_tests.append(test_name)

            return result.returncode, output, failing_tests

        except subprocess.TimeoutExpired:
            return -1, "TIMEOUT: Test execution exceeded time limit", []
        except Exception as e:
            return -1, f"ERROR: {e}", []

    def generate_bug(
        self, file_path: Path, site: MutationSite, bug_number: int
    ) -> tuple[BugEntry, HiddenMetadata] | None:
        """
        Generate a single bug entry by applying mutation and running tests.

        Returns:
            Tuple of (BugEntry, HiddenMetadata) if mutation is killed, None if survived
        """
        original_code = file_path.read_text()
        mutated_code = self.injector.apply_mutation(original_code, site)

        try:
            # Apply mutation
            file_path.write_text(mutated_code)

            # Run tests
            exit_code, output, failing_tests = self.run_tests()

            # Only include killed mutations (tests failed)
            if exit_code != 0 and failing_tests:
                bug_id = f"{self.repo_name}-{self.style}-{bug_number:03d}"

                bug_entry = BugEntry(
                    bug_id=bug_id,
                    test_output=output,
                    failing_tests=failing_tests,
                    exit_code=exit_code,
                )

                hidden = HiddenMetadata(
                    bug_id=bug_id,
                    file_path=str(file_path.relative_to(self.repo_path)),
                    line_number=site.start_point[0] + 1,
                    mutation_type=site.mutation_type.value,
                    original_text=site.original_text,
                    mutated_text=site.mutated_text,
                    context=site.context,
                )

                return bug_entry, hidden

            return None

        finally:
            # Always restore original
            file_path.write_text(original_code)

    def generate_catalog(
        self,
        source_dir: str | Path,
        max_bugs: int = 50,
        file_pattern: str = "**/*.py",
        progress_callback=None,
    ) -> BugCatalog:
        """
        Generate a bug catalog for the repository.

        Args:
            source_dir: Directory containing source files
            max_bugs: Maximum number of bugs to generate
            file_pattern: Glob pattern for source files
            progress_callback: Optional callback(current, total, bug_id)
        """
        from datetime import datetime

        catalog = BugCatalog(
            repo=self.repo_name,
            style=self.style,
            generated_at=datetime.now().isoformat(),
            test_command=self.config.get_test_command(self.repo_path),
        )

        source_path = self.repo_path / source_dir
        files = sorted(source_path.glob(file_pattern))
        files = [f for f in files if "__pycache__" not in str(f)]

        # Collect all mutation sites
        all_sites: list[tuple[Path, MutationSite]] = []
        for file_path in files:
            try:
                code = file_path.read_text()
                sites = self.injector.list_mutation_sites(code)
                for site in sites:
                    all_sites.append((file_path, site))
            except Exception:
                continue

        bug_number = 1
        tested = 0

        for file_path, site in all_sites:
            if len(catalog.bugs) >= max_bugs:
                break

            tested += 1
            result = self.generate_bug(file_path, site, bug_number)

            if result:
                bug_entry, hidden = result
                catalog.bugs.append(bug_entry)
                catalog._hidden.append(hidden)

                if progress_callback:
                    progress_callback(len(catalog.bugs), max_bugs, bug_entry.bug_id)

                bug_number += 1

        return catalog


def generate_catalog(
    repo_path: str | Path,
    repo_name: str,
    style: str,
    max_bugs: int = 50,
    verbose: bool = False,
    working_dir: str | Path | None = None,
) -> BugCatalog:
    """
    Convenience function to generate a bug catalog.

    Args:
        repo_path: Path to the repository
        repo_name: Name of the repository (e.g., "humanize")
        style: Style variant (e.g., "original", "camelcase")
        max_bugs: Maximum bugs to generate
        verbose: Print progress
        working_dir: Directory to run tests from (avoids uv build issues)

    Returns:
        BugCatalog
    """
    config = get_config(repo_name)

    generator = CatalogGenerator(
        repo_path=repo_path,
        repo_name=repo_name,
        style=style,
        config=config,
        working_dir=working_dir,
    )

    def progress(current, total, bug_id):
        if verbose:
            print(f"[{current}/{total}] Generated: {bug_id}")

    return generator.generate_catalog(
        source_dir=config.source_dir,
        max_bugs=max_bugs,
        progress_callback=progress if verbose else None,
    )
