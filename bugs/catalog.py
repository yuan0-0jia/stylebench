"""
Bug catalog generator for StyleBench.

Generates bug catalogs that separate:
- Agent-visible data: test failure output only (no diff leakage)
- Hidden metadata: mutation details for scoring (never shown to agent)

Features:
- Parallel mutation testing using worker processes
- Smart mutation ordering (high kill-rate types first)
- Process group management for clean subprocess termination
"""

import atexit
import json
import os
import shutil
import signal
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .injector import Injector, MutationSite, MutationType
from .repo_config import RepoConfig, get_config

# Track active subprocesses for cleanup
_active_processes: set[subprocess.Popen] = set()


def _cleanup_all_processes():
    """Kill all tracked subprocess on exit."""
    for proc in list(_active_processes):
        try:
            if proc.poll() is None:  # Still running
                # Kill the entire process group
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


def _signal_handler(signum, frame):
    """Handle interrupt signals by cleaning up subprocesses."""
    _cleanup_all_processes()
    # Re-raise the signal to allow normal termination
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# Install signal handlers for common termination signals
for sig in (signal.SIGTERM, signal.SIGINT):
    try:
        signal.signal(sig, _signal_handler)
    except (ValueError, OSError):
        pass  # Can't set handler in some contexts (e.g., non-main thread)


def run_with_cleanup(cmd, cwd, timeout, capture_output=True, text=True):
    """
    Run a subprocess with proper cleanup on timeout/interrupt.

    Uses Popen with process groups for reliable cleanup:
    - Creates subprocess in new process group (start_new_session=True)
    - Kills entire process group on timeout/error
    - Tracks process for cleanup on parent termination
    """
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

        # Create a result-like object
        class Result:
            pass

        result = Result()
        result.stdout = stdout or ""
        result.stderr = stderr or ""
        result.returncode = proc.returncode
        return result
    except subprocess.TimeoutExpired:
        # Kill the entire process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.kill()
        proc.wait()
        raise
    except Exception:
        # Kill the entire process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        proc.kill()
        proc.wait()
        raise
    finally:
        _active_processes.discard(proc)


# Mutation types ordered by typical kill rate (highest first)
# Mixed ordering to ensure diversity in bug catalogs
MUTATION_PRIORITY = [
    MutationType.COMPARISON_EQ_NE,      # == ↔ != (very high kill rate)
    MutationType.VARIABLE_SWAP,          # var swap (high kill rate, interesting for badnames)
    MutationType.BOOL_TRUE_FALSE,        # True ↔ False (high kill rate)
    MutationType.RETURN_NONE,            # return x → None (high kill rate)
    MutationType.IF_ELSE_SWAP,           # if/else swap (high kill rate)
    MutationType.BOOLEAN_AND_OR,         # and ↔ or (high kill rate)
    MutationType.COMPARISON_LT_GT,       # < ↔ > (medium-high)
    MutationType.COMPARISON_LE_GE,       # <= ↔ >= (medium-high)
    MutationType.MEMBERSHIP_IN,          # in ↔ not in (medium)
    MutationType.IDENTITY_IS,            # is ↔ is not (medium)
    MutationType.ARITHMETIC_ADD_SUB,     # + ↔ - (medium)
    MutationType.ARITHMETIC_MUL_DIV,     # * ↔ / (medium-low)
    MutationType.BOUNDARY_PLUS_ONE,      # n → n+1 (low - many equivalents)
    MutationType.BOUNDARY_MINUS_ONE,     # n → n-1 (low - many equivalents)
]


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


def _is_test_related_to_file(test_name: str, source_file_path: str) -> bool:
    """Check if a test is likely related to a source file.

    Uses heuristics to match test files to source files:
    - test_foo.py tests foo.py
    - test_module.py::TestClass tests module.py
    """
    source_file = Path(source_file_path)
    source_module = source_file.stem.lower()

    test_path = test_name.split("::")[0]
    test_file = Path(test_path).stem.lower()

    if test_file.startswith("test_"):
        test_module = test_file[5:]
    else:
        test_module = test_file

    if source_module == test_module:
        return True
    if source_module in test_module or test_module in source_module:
        return True
    if "::" in test_name:
        test_class = test_name.split("::")[1].lower()
        if source_module in test_class:
            return True
    return False


def _test_file_mutations_worker(args: tuple) -> list[dict]:
    """
    Worker function for parallel mutation testing.

    Processes ALL mutations for a single file sequentially to avoid conflicts.
    Runs in a separate process with its own copy of the repo.
    """
    (
        worker_repo_path,
        file_rel_path,
        sites_data,  # List of mutation site data for this file
        test_command,
        timeout,
        repo_name,
        style,
        baseline_failing,  # Tests that fail without mutation
        max_bugs_per_file,  # Stop early if we find enough
    ) = args

    worker_repo = Path(worker_repo_path)
    file_path = worker_repo / file_rel_path
    results = []

    # Read original code once
    try:
        original_code = file_path.read_text()
    except Exception:
        return results

    injector = Injector()

    for site_data in sites_data:
        if len(results) >= max_bugs_per_file:
            break

        # Reconstruct MutationSite from serialized data
        site = MutationSite(
            site_id=site_data["site_id"],
            mutation_type=MutationType(site_data["mutation_type"]),
            start_byte=site_data["start_byte"],
            end_byte=site_data["end_byte"],
            start_point=tuple(site_data["start_point"]),
            end_point=tuple(site_data["end_point"]),
            original_text=site_data["original_text"],
            mutated_text=site_data["mutated_text"],
            context=site_data["context"],
        )

        # Apply mutation
        mutated_code = injector.apply_mutation(original_code, site)

        try:
            # Write mutated code
            file_path.write_text(mutated_code)

            # Run tests with proper cleanup on timeout
            result = run_with_cleanup(
                test_command,
                cwd=worker_repo,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            exit_code = result.returncode

            # Extract failing test names
            failing_tests = []
            for line in output.split("\n"):
                if line.startswith("FAILED "):
                    test_name = line.split(" ")[1].split(" -")[0]
                    failing_tests.append(test_name)

            # Only count as killed if there are NEW failures (not baseline failures)
            new_failures = [t for t in failing_tests if t not in baseline_failing]

            # Filter to prefer failures related to the mutated file
            related_failures = [
                t for t in new_failures
                if _is_test_related_to_file(t, file_rel_path)
            ]

            # Fallback: if no directly related tests, accept all new failures
            if not related_failures and new_failures:
                related_failures = new_failures

            if exit_code != 0 and related_failures:
                results.append({
                    "file_path": file_rel_path,
                    "site_data": site_data,
                    "test_output": output,
                    "failing_tests": related_failures,
                    "exit_code": exit_code,
                })

        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        finally:
            # Restore original for next mutation
            try:
                file_path.write_text(original_code)
            except Exception:
                pass

    return results


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
        num_workers: int | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.repo_name = repo_name
        self.style = style
        self.config = config or get_config(repo_name)
        self.timeout = timeout
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.injector = Injector()
        # Default to 2 workers to avoid memory exhaustion from parallel pytest runs
        self.num_workers = num_workers or 2

    def run_tests(self) -> tuple[int, str, list[str]]:
        """
        Run tests and capture output.

        Returns:
            Tuple of (exit_code, output, failing_test_names)
        """
        try:
            test_command = self.config.get_test_command(self.repo_path, external=False)

            result = run_with_cleanup(
                test_command,
                cwd=self.repo_path,
                timeout=self.timeout,
            )
            output = result.stdout + result.stderr

            failing_tests = []
            for line in output.split("\n"):
                if line.startswith("FAILED "):
                    test_name = line.split(" ")[1].split(" -")[0]
                    failing_tests.append(test_name)

            return result.returncode, output, failing_tests

        except subprocess.TimeoutExpired:
            return -1, "TIMEOUT: Test execution exceeded time limit", []
        except Exception as e:
            return -1, f"ERROR: {e}", []

    def _is_test_related_to_file(self, test_name: str, source_file: Path) -> bool:
        """Check if a test is likely related to a source file.

        Uses heuristics to match test files to source files:
        - test_foo.py tests foo.py
        - test_module.py::TestClass tests module.py
        """
        # Extract module name from source file (e.g., "number" from "src/humanize/number.py")
        source_module = source_file.stem.lower()

        # Extract test file from test name (e.g., "test_number" from "tests/test_number.py::...")
        test_path = test_name.split("::")[0]
        test_file = Path(test_path).stem.lower()

        # Remove "test_" prefix from test file name
        if test_file.startswith("test_"):
            test_module = test_file[5:]
        else:
            test_module = test_file

        # Check for direct match
        if source_module == test_module:
            return True

        # Check for partial match (e.g., "number" in "test_number_utils")
        if source_module in test_module or test_module in source_module:
            return True

        # Check class name in test path for more specific matching
        if "::" in test_name:
            test_class = test_name.split("::")[1].lower()
            if source_module in test_class:
                return True

        return False

    def generate_bug(
        self, file_path: Path, site: MutationSite, bug_number: int,
        baseline_failing: set[str] | None = None,
    ) -> tuple[BugEntry, HiddenMetadata] | None:
        """
        Generate a single bug entry by applying mutation and running tests.
        (Sequential version for compatibility)

        Args:
            file_path: Path to the source file to mutate.
            site: Mutation site to apply.
            bug_number: Sequential bug number for ID generation.
            baseline_failing: Set of test names that fail without mutation.
                             If None, any failure is accepted (legacy behavior).
        """
        original_code = file_path.read_text()
        mutated_code = self.injector.apply_mutation(original_code, site)

        try:
            file_path.write_text(mutated_code)
            exit_code, output, failing_tests = self.run_tests()

            # Only count as killed if there are NEW failures (not baseline failures)
            if baseline_failing is not None:
                new_failures = [t for t in failing_tests if t not in baseline_failing]
            else:
                new_failures = failing_tests

            # Filter to prefer failures that are related to the mutated file
            # This filters out flaky/unrelated test failures when possible
            related_failures = [
                t for t in new_failures
                if self._is_test_related_to_file(t, file_path)
            ]

            # Fallback: if no directly related tests found, accept all new failures
            # This handles projects where test names don't match source names
            if not related_failures and new_failures:
                related_failures = new_failures

            if exit_code != 0 and related_failures:
                bug_id = f"{self.repo_name}-{self.style}-{bug_number:03d}"

                bug_entry = BugEntry(
                    bug_id=bug_id,
                    test_output=output,
                    failing_tests=related_failures,  # Only related failures caused by mutation
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
            file_path.write_text(original_code)

    def _sort_mutations_by_priority(
        self, sites: list[tuple[Path, MutationSite]]
    ) -> list[tuple[Path, MutationSite]]:
        """Sort mutations by expected kill rate (highest first)."""
        priority_map = {mt: i for i, mt in enumerate(MUTATION_PRIORITY)}
        # Unknown types get lowest priority
        max_priority = len(MUTATION_PRIORITY)

        return sorted(
            sites,
            key=lambda x: priority_map.get(x[1].mutation_type, max_priority)
        )

    def _serialize_site(self, site: MutationSite) -> dict:
        """Serialize a MutationSite for passing to worker process."""
        return {
            "site_id": site.site_id,
            "mutation_type": site.mutation_type.value,
            "start_byte": site.start_byte,
            "end_byte": site.end_byte,
            "start_point": list(site.start_point),
            "end_point": list(site.end_point),
            "original_text": site.original_text,
            "mutated_text": site.mutated_text,
            "context": site.context,
        }

    def generate_catalog_parallel(
        self,
        source_dir: str | Path,
        max_bugs: int = 50,
        file_pattern: str = "**/*.py",
        progress_callback=None,
    ) -> BugCatalog:
        """
        Generate a bug catalog using parallel mutation testing.

        Creates temporary repo copies for each worker to test mutations
        in parallel without file conflicts.
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

        # Sort by priority (high kill-rate types first)
        all_sites = self._sort_mutations_by_priority(all_sites)

        if progress_callback:
            progress_callback(0, max_bugs, f"Found {len(all_sites)} mutation sites")

        # Create temporary directory for worker repos
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create worker repo copies
            worker_repos = []
            for i in range(self.num_workers):
                worker_repo = temp_path / f"worker_{i}"
                shutil.copytree(self.repo_path, worker_repo, symlinks=True)
                worker_repos.append(worker_repo)

            # Get test command for workers
            test_command = self.config.get_test_command(worker_repos[0], external=False)

            # Run baseline test to find tests that already fail
            if progress_callback:
                progress_callback(0, max_bugs, "Running baseline tests...")

            baseline_result = run_with_cleanup(
                test_command,
                cwd=worker_repos[0],
                timeout=self.timeout * 2,  # Extra time for first run (uv sync)
            )
            baseline_failing = set()
            for line in (baseline_result.stdout + baseline_result.stderr).split("\n"):
                if line.startswith("FAILED "):
                    test_name = line.split(" ")[1].split(" -")[0]
                    baseline_failing.add(test_name)

            if progress_callback:
                if baseline_failing:
                    msg = f"Baseline: {len(baseline_failing)} tests already failing"
                    progress_callback(0, max_bugs, msg)
                else:
                    progress_callback(0, max_bugs, "Baseline: all tests pass")

            # Group mutations by file to avoid conflicts
            from collections import defaultdict
            mutations_by_file: dict[str, list[MutationSite]] = defaultdict(list)
            for file_path, site in all_sites:
                file_rel_path = str(file_path.relative_to(self.repo_path))
                mutations_by_file[file_rel_path].append(site)

            # Assign files to workers round-robin
            file_list = list(mutations_by_file.keys())

            # Generate test command for each worker (with correct paths for --ignore flags)
            worker_test_commands = {
                i: self.config.get_test_command(worker_repos[i], external=False)
                for i in range(self.num_workers)
            }

            # Prepare work items - one per FILE (not per mutation)
            # Each worker processes all mutations in its assigned files sequentially
            work_items = []
            for idx, file_rel_path in enumerate(file_list):
                worker_idx = idx % self.num_workers
                worker_repo = worker_repos[worker_idx]
                sites = mutations_by_file[file_rel_path]

                work_items.append((
                    str(worker_repo),
                    file_rel_path,
                    [self._serialize_site(site) for site in sites],
                    worker_test_commands[worker_idx],  # Use correct command for this worker
                    self.timeout,
                    self.repo_name,
                    self.style,
                    baseline_failing,
                    max_bugs,  # Limit per file to avoid over-testing
                ))

            if progress_callback:
                msg = f"Processing {len(file_list)} files with {self.num_workers} workers"
                progress_callback(0, max_bugs, msg)

            # Process files in parallel
            killed_results = []

            with ProcessPoolExecutor(max_workers=self.num_workers) as executor:
                # Submit all file processing jobs
                future_to_file = {
                    executor.submit(_test_file_mutations_worker, item): item[1]
                    for item in work_items
                }

                # Collect results as files complete
                for future in as_completed(future_to_file):
                    if len(killed_results) >= max_bugs:
                        # Cancel remaining futures
                        for f in future_to_file:
                            f.cancel()
                        break

                    try:
                        file_results = future.result()
                        if file_results:
                            killed_results.extend(file_results)

                            if progress_callback:
                                progress_callback(
                                    min(len(killed_results), max_bugs),
                                    max_bugs,
                                    f"Found {len(killed_results)} killed mutations"
                                )
                    except Exception:
                        pass

            # Convert results to catalog entries
            bug_number = 1
            for result in killed_results[:max_bugs]:
                bug_id = f"{self.repo_name}-{self.style}-{bug_number:03d}"
                site_data = result["site_data"]

                bug_entry = BugEntry(
                    bug_id=bug_id,
                    test_output=result["test_output"],
                    failing_tests=result["failing_tests"],
                    exit_code=result["exit_code"],
                )

                hidden = HiddenMetadata(
                    bug_id=bug_id,
                    file_path=result["file_path"],
                    line_number=site_data["start_point"][0] + 1,
                    mutation_type=site_data["mutation_type"],
                    original_text=site_data["original_text"],
                    mutated_text=site_data["mutated_text"],
                    context=site_data["context"],
                )

                catalog.bugs.append(bug_entry)
                catalog._hidden.append(hidden)
                bug_number += 1

        return catalog

    def generate_catalog(
        self,
        source_dir: str | Path,
        max_bugs: int = 50,
        file_pattern: str = "**/*.py",
        progress_callback=None,
        parallel: bool = True,
    ) -> BugCatalog:
        """
        Generate a bug catalog for the repository.

        Args:
            source_dir: Directory containing source files
            max_bugs: Maximum number of bugs to generate
            file_pattern: Glob pattern for source files
            progress_callback: Optional callback(current, total, message)
            parallel: Use parallel testing (default True for repos with 5+ source files)
        """
        # Count source files to decide if parallel is worth it
        source_path = self.repo_path / source_dir
        files = list(source_path.glob(file_pattern))
        files = [f for f in files if "__pycache__" not in str(f)]

        # Only use parallel if we have enough files to benefit
        # Parallel has overhead (repo copy), so only use when >= 5 files
        use_parallel = parallel and self.num_workers > 1 and len(files) >= 5

        if use_parallel:
            return self.generate_catalog_parallel(
                source_dir=source_dir,
                max_bugs=max_bugs,
                file_pattern=file_pattern,
                progress_callback=progress_callback,
            )

        # Sequential fallback
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

        # Run baseline tests to find pre-existing failures
        if progress_callback:
            progress_callback(0, max_bugs, "Running baseline tests...")

        _, _, baseline_failing_list = self.run_tests()
        baseline_failing = set(baseline_failing_list)

        if progress_callback:
            if baseline_failing:
                msg = f"Baseline: {len(baseline_failing)} tests already failing"
                progress_callback(0, max_bugs, msg)
            else:
                progress_callback(0, max_bugs, "Baseline: all tests pass")

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

        # Sort by priority
        all_sites = self._sort_mutations_by_priority(all_sites)

        bug_number = 1

        for file_path, site in all_sites:
            if len(catalog.bugs) >= max_bugs:
                break

            result = self.generate_bug(file_path, site, bug_number, baseline_failing)

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
    parallel: bool = True,
    num_workers: int | None = None,
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
        parallel: Use parallel testing (default True)
        num_workers: Number of parallel workers (default: CPU count, max 8)

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
        num_workers=num_workers,
    )

    def progress(current, total, message):
        if verbose:
            print(f"[{current}/{total}] {message}")

    return generator.generate_catalog(
        source_dir=config.source_dir,
        max_bugs=max_bugs,
        progress_callback=progress if verbose else None,
        parallel=parallel,
    )
