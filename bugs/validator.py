"""
Mutation validator for batch-testing mutations against test suites.

Applies mutations one at a time, runs tests, and records which mutations
are "killed" (cause test failures) vs "survived" (tests still pass).
"""

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .injector import Injector, MutationSite, MutationType


@dataclass
class MutationResult:
    """Result of testing a single mutation."""

    site_id: int
    mutation_type: str
    file_path: str
    line_number: int
    original_text: str
    mutated_text: str
    killed: bool
    test_output: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    """Summary report for a validation run."""

    repo_path: str
    total_mutations: int
    killed: int
    survived: int
    errors: int
    mutation_score: float  # killed / (killed + survived)
    duration_seconds: float
    results: list[MutationResult] = field(default_factory=list)
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["results"] = [r.to_dict() for r in self.results]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            "Mutation Validation Report",
            "==========================",
            f"Repository: {self.repo_path}",
            f"Total mutations tested: {self.total_mutations}",
            f"  Killed: {self.killed}",
            f"  Survived: {self.survived}",
            f"  Errors: {self.errors}",
            f"Mutation score: {self.mutation_score:.1%}",
            f"Duration: {self.duration_seconds:.1f}s",
            "",
            "By mutation type:",
        ]
        for mut_type, counts in sorted(self.by_type.items()):
            k = counts.get("killed", 0)
            s = counts.get("survived", 0)
            total = k + s
            score = k / total if total > 0 else 0
            lines.append(f"  {mut_type}: {k}/{total} killed ({score:.0%})")
        return "\n".join(lines)


class Validator:
    """Batch validator for testing mutations."""

    def __init__(
        self,
        repo_path: str | Path,
        test_command: list[str] | None = None,
        timeout: int = 60,
    ):
        """
        Initialize the validator.

        Args:
            repo_path: Path to the repository to validate
            test_command: Command to run tests (default: uv run pytest -x -q)
            timeout: Timeout in seconds for each test run
        """
        self.repo_path = Path(repo_path).resolve()
        self.test_command = test_command or ["uv", "run", "pytest", "-x", "-q"]
        self.timeout = timeout
        self.injector = Injector()

    def run_tests(self) -> tuple[bool, str]:
        """
        Run the test suite.

        Returns:
            Tuple of (passed, output)
        """
        try:
            result = subprocess.run(
                self.test_command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = result.stdout + result.stderr
            # Truncate long output
            if len(output) > 2000:
                output = output[:1000] + "\n...[truncated]...\n" + output[-500:]
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"
        except Exception as e:
            return False, f"ERROR: {e}"

    def validate_mutation(
        self, file_path: Path, site: MutationSite
    ) -> MutationResult:
        """
        Test a single mutation.

        Args:
            file_path: Path to the file to mutate
            site: The mutation site to test

        Returns:
            MutationResult with the outcome
        """
        original_code = file_path.read_text()
        mutated_code = self.injector.apply_mutation(original_code, site)

        start_time = time.time()

        try:
            # Apply mutation
            file_path.write_text(mutated_code)

            # Run tests
            passed, output = self.run_tests()
            killed = not passed

            duration = time.time() - start_time

            return MutationResult(
                site_id=site.site_id,
                mutation_type=site.mutation_type.value,
                file_path=str(file_path.relative_to(self.repo_path)),
                line_number=site.start_point[0] + 1,
                original_text=site.original_text,
                mutated_text=site.mutated_text,
                killed=killed,
                # Only save output for survivors (to debug why they survived)
                test_output=output if not killed else "",
                duration_seconds=duration,
            )
        finally:
            # Always restore original
            file_path.write_text(original_code)

    def validate_file(
        self,
        file_path: str | Path,
        mutation_types: list[MutationType] | None = None,
        max_mutations: int | None = None,
        progress_callback: Callable | None = None,
    ) -> list[MutationResult]:
        """
        Validate all mutations in a single file.

        Args:
            file_path: Path to the file to validate (absolute or relative to repo_path)
            mutation_types: Types of mutations to test (default: all)
            max_mutations: Maximum number of mutations to test
            progress_callback: Optional callback(current, total, result) for progress

        Returns:
            List of MutationResult objects
        """
        file_path = Path(file_path).resolve()

        code = file_path.read_text()
        sites = self.injector.list_mutation_sites(code)

        # Filter by type if specified
        if mutation_types:
            sites = [s for s in sites if s.mutation_type in mutation_types]

        # Limit if specified
        if max_mutations:
            sites = sites[:max_mutations]

        results = []
        for i, site in enumerate(sites):
            result = self.validate_mutation(file_path, site)
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, len(sites), result)

        return results

    def validate_repo(
        self,
        source_dir: str | Path,
        file_pattern: str = "*.py",
        mutation_types: list[MutationType] | None = None,
        max_mutations_per_file: int | None = None,
        max_total_mutations: int | None = None,
        progress_callback: Callable | None = None,
    ) -> ValidationReport:
        """
        Validate mutations across an entire repository.

        Args:
            source_dir: Directory containing source files (relative to repo_path)
            file_pattern: Glob pattern for files to process
            mutation_types: Types of mutations to test (default: all)
            max_mutations_per_file: Max mutations per file
            max_total_mutations: Max total mutations to test
            progress_callback: Optional callback for progress updates

        Returns:
            ValidationReport with all results
        """
        start_time = time.time()
        source_path = self.repo_path / source_dir

        # Find all Python files
        files = sorted(source_path.glob(file_pattern))
        files = [f for f in files if f.name != "__init__.py"]

        all_results = []
        total_tested = 0

        for file_path in files:
            if max_total_mutations and total_tested >= max_total_mutations:
                break

            remaining = None
            if max_total_mutations:
                remaining = max_total_mutations - total_tested

            max_for_file = max_mutations_per_file
            if remaining and (not max_for_file or remaining < max_for_file):
                max_for_file = remaining

            results = self.validate_file(
                file_path,
                mutation_types=mutation_types,
                max_mutations=max_for_file,
                progress_callback=progress_callback,
            )
            all_results.extend(results)
            total_tested += len(results)

        # Calculate statistics
        killed = sum(1 for r in all_results if r.killed)
        survived = sum(1 for r in all_results if not r.killed)
        errors = 0  # Could track timeouts/errors separately

        mutation_score = killed / (killed + survived) if (killed + survived) > 0 else 0

        # Group by type
        by_type: dict[str, dict[str, int]] = {}
        for r in all_results:
            if r.mutation_type not in by_type:
                by_type[r.mutation_type] = {"killed": 0, "survived": 0}
            if r.killed:
                by_type[r.mutation_type]["killed"] += 1
            else:
                by_type[r.mutation_type]["survived"] += 1

        duration = time.time() - start_time

        return ValidationReport(
            repo_path=str(self.repo_path),
            total_mutations=len(all_results),
            killed=killed,
            survived=survived,
            errors=errors,
            mutation_score=mutation_score,
            duration_seconds=duration,
            results=all_results,
            by_type=by_type,
        )


def validate_mutations(
    repo_path: str | Path,
    source_dir: str | Path,
    test_command: list[str] | None = None,
    max_mutations: int | None = None,
    verbose: bool = False,
) -> ValidationReport:
    """
    Convenience function to validate mutations in a repository.

    Args:
        repo_path: Path to the repository
        source_dir: Source directory relative to repo_path
        test_command: Command to run tests
        max_mutations: Maximum mutations to test
        verbose: Print progress

    Returns:
        ValidationReport
    """
    validator = Validator(repo_path, test_command=test_command)

    def progress(current, total, result):
        if verbose:
            status = "KILLED" if result.killed else "SURVIVED"
            loc = f"{result.file_path}:{result.line_number}"
            mut = f"'{result.original_text}' → '{result.mutated_text}'"
            print(f"[{current}/{total}] {status}: {loc} {mut}")

    return validator.validate_repo(
        source_dir,
        max_total_mutations=max_mutations,
        progress_callback=progress if verbose else None,
    )
