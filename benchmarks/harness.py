"""Benchmark harness for running agent trials.

Orchestrates the benchmark workflow:
1. Load bug catalog (or manifest for controlled runs)
2. Create working copies
3. Apply bugs
4. Run agents (using pre-captured test output for consistency)
5. Evaluate fixes
6. Collect results
"""

import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .agents import Agent, BugContext, FixResult, TrialResult
from .evaluator import (
    apply_bug,
    create_working_copy,
    evaluate_fix,
    get_bug_by_id,
    hide_tests,
    load_bug_catalog,
    lock_tests,
    restore_tests,
    run_tests,
    unlock_tests,
)


class BenchmarkHarness:
    """Harness for running benchmark trials."""

    def __init__(
        self,
        catalog_path: Path,
        repo_path: Path,
        repo_name: str,
        output_dir: Path | None = None,
        manifest: dict | None = None,
    ):
        """Initialize the benchmark harness.

        Args:
            catalog_path: Path to the bug catalog JSON file.
            repo_path: Path to the source repository.
            repo_name: Name of the repository (e.g., 'humanize').
            output_dir: Directory for storing results (default: temp dir).
            manifest: Optional manifest dict with pre-captured test outputs.
                      When provided, uses manifest test_output instead of
                      re-running tests, ensuring all agents see identical input.
        """
        self.catalog_path = catalog_path
        self.repo_path = repo_path
        self.repo_name = repo_name
        self.catalog = load_bug_catalog(catalog_path)
        self.manifest = manifest

        # Index manifest trials by bug_id for fast lookup
        self._manifest_trials = {}
        if manifest:
            for trial in manifest.get("trials", []):
                self._manifest_trials[trial["bug_id"]] = trial

        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="stylebench_results_"))
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.results: list[TrialResult] = []
        self.hit_rate_limit: bool = False

    def _get_manifest_test_output(self, bug_id: str) -> tuple[str, list[str]] | None:
        """Get pre-captured test output from manifest.

        Returns (test_output, failing_tests) or None if not in manifest.
        """
        trial = self._manifest_trials.get(bug_id)
        if trial:
            return trial["test_output"], trial.get("failing_tests", [])
        return None

    def run_trial(
        self,
        agent: Agent,
        bug_id: str,
        mode: str = "with_tests",
        test_timeout: int = 120,
    ) -> TrialResult:
        """Run a single benchmark trial.

        When a manifest is loaded, uses pre-captured test output for the agent
        prompt (ensuring all agents see identical input), but still runs tests
        after the fix to evaluate the result.

        Args:
            agent: The agent to test.
            bug_id: ID of the bug to inject.
            mode: Test access mode ('with_tests' or 'without_tests').
            test_timeout: Timeout for test execution.

        Returns:
            TrialResult with evaluation outcome.
        """
        # Get bug from catalog
        bug_data = get_bug_by_id(self.catalog, bug_id)
        if bug_data is None:
            result = TrialResult(
                bug_id=bug_id,
                agent=agent.get_name(),
                mode=mode,
                fix_result=FixResult(success=False, error=f"Bug {bug_id} not found"),
                evaluation="ERROR",
            )
            self.results.append(result)
            return result

        bug, hidden = bug_data

        # Create working copy
        work_dir = create_working_copy(self.repo_path)

        hidden_path = None
        try:
            # Apply the bug
            if not apply_bug(work_dir, hidden):
                result = TrialResult(
                    bug_id=bug_id,
                    agent=agent.get_name(),
                    mode=mode,
                    fix_result=FixResult(success=False, error="Failed to apply bug"),
                    evaluation="ERROR",
                )
                self.results.append(result)
                return result

            # Get test output for the agent prompt
            manifest_output = self._get_manifest_test_output(bug_id)
            if manifest_output:
                # Use pre-captured test output from manifest (controlled)
                test_output, failing_tests = manifest_output
                # Still run a quick validation to confirm bug is active
                before_result = run_tests(work_dir, self.repo_name, timeout=test_timeout)
                if before_result.exit_code == 0:
                    result = TrialResult(
                        bug_id=bug_id,
                        agent=agent.get_name(),
                        mode=mode,
                        fix_result=FixResult(
                            success=False, error="Bug did not cause test failure"
                        ),
                        evaluation="ERROR",
                    )
                    self.results.append(result)
                    return result
            else:
                # No manifest — run tests to get failure output (legacy behavior)
                before_result = run_tests(work_dir, self.repo_name, timeout=test_timeout)
                if before_result.exit_code == 0:
                    result = TrialResult(
                        bug_id=bug_id,
                        agent=agent.get_name(),
                        mode=mode,
                        fix_result=FixResult(
                            success=False, error="Bug did not cause test failure"
                        ),
                        evaluation="ERROR",
                    )
                    self.results.append(result)
                    return result
                test_output = before_result.output
                failing_tests = before_result.failing_tests

            # Protect tests from agent modification
            hidden_path = None
            if mode == "without_tests":
                hidden_path = hide_tests(work_dir, self.repo_name)
            else:
                # with_tests: make test files read-only so agent can read but not edit
                lock_tests(work_dir, self.repo_name)

            # Build context for agent — uses controlled test output
            context = BugContext(
                repo_path=work_dir,
                test_output=test_output,
                failing_tests=failing_tests,
                mode=mode,
                bug_id=bug_id,
                repo_name=self.repo_name,
                style=self._extract_style(bug_id),
            )

            # Run the agent
            fix_result = agent.fix_bug(context)

            # Restore test access for evaluation
            if hidden_path is not None:
                restore_tests(work_dir, hidden_path, self.repo_name)
            else:
                unlock_tests(work_dir, self.repo_name)

            # Evaluate the fix (always run tests fresh for evaluation)
            if not fix_result.success:
                evaluation = "NO_FIX"
                after_result = before_result  # No changes made
            else:
                after_result = run_tests(work_dir, self.repo_name, timeout=test_timeout)
                evaluation = evaluate_fix(
                    before_result, after_result, before_result.failing_tests
                )

            trial_result = TrialResult(
                bug_id=bug_id,
                agent=agent.get_name(),
                mode=mode,
                fix_result=fix_result,
                evaluation=evaluation,
                tests_passed=after_result.passed,
                tests_failed=after_result.failed,
                tests_total=after_result.total,
            )

            self.results.append(trial_result)
            return trial_result

        finally:
            # Clean up hidden test temp dir if it was never restored
            if hidden_path is not None and hidden_path.exists():
                hidden_parent = hidden_path.parent
                if hidden_parent.name.startswith("stylebench_hidden_tests_"):
                    shutil.rmtree(hidden_parent, ignore_errors=True)
            # Clean up working copy
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def run_all(
        self,
        agent: Agent,
        mode: str = "with_tests",
        bug_ids: list[str] | None = None,
        test_timeout: int = 120,
        progress_callback: Callable | None = None,
        delay_between_trials: int = 0,
    ) -> list[TrialResult]:
        """Run trials for multiple bugs.

        Args:
            agent: The agent to test.
            mode: Test access mode.
            bug_ids: List of bug IDs to test (default: all bugs).
            test_timeout: Timeout for test execution.
            progress_callback: Optional callback(current, total, bug_id).
            delay_between_trials: Seconds to wait between trials (for rate limiting).

        Returns:
            List of TrialResults.
        """
        import time

        if bug_ids is None:
            bug_ids = [bug["bug_id"] for bug in self.catalog.get("bugs", [])]

        results = []
        total = len(bug_ids)

        for i, bug_id in enumerate(bug_ids):
            if progress_callback:
                progress_callback(i + 1, total, bug_id)

            result = self.run_trial(
                agent=agent, bug_id=bug_id, mode=mode, test_timeout=test_timeout
            )

            # If the agent was rate-limited, drop the result and stop the batch.
            # The rate_limited flag is signaled via self.hit_rate_limit and written
            # to the result file metadata so the script layer can detect it.
            if result.fix_result.rate_limited:
                self.results.pop()
                self.hit_rate_limit = True
                break

            results.append(result)

            if delay_between_trials > 0 and i < total - 1:
                time.sleep(delay_between_trials)

        return results

    def save_results(self, filename: str | None = None) -> Path:
        """Save results to JSON file.

        Args:
            filename: Optional filename (default: auto-generated).

        Returns:
            Path to the saved results file.
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Include bug IDs and mode to avoid collisions when running parallel trials
            if self.results:
                bug_ids_str = "_".join(r.bug_id for r in self.results)
                mode = self.results[0].mode
                # Truncate to avoid exceeding filesystem filename limits (255 bytes)
                if len(bug_ids_str) > 150:
                    first = self.results[0].bug_id
                    last = self.results[-1].bug_id
                    bug_ids_str = f"{first}_to_{last}_{len(self.results)}bugs"
                filename = f"results_{timestamp}_{bug_ids_str}_{mode}.json"
            else:
                filename = f"results_{timestamp}.json"

        output_path = self.output_dir / filename

        data = {
            "metadata": {
                "catalog": str(self.catalog_path),
                "repo": str(self.repo_path),
                "repo_name": self.repo_name,
                "timestamp": datetime.now().isoformat(),
                "total_trials": len(self.results),
                "manifest_used": self.manifest is not None,
                "hit_rate_limit": self.hit_rate_limit,
            },
            "results": [r.to_dict() for r in self.results],
            "summary": self._compute_summary(),
        }

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        return output_path

    def _compute_summary(self) -> dict:
        """Compute summary statistics from results."""
        if not self.results:
            return {}

        by_agent = {}
        by_mode = {}
        by_evaluation = {"PASS": 0, "FAIL": 0, "ERROR": 0, "TIMEOUT": 0, "NO_FIX": 0}

        for r in self.results:
            # By agent
            if r.agent not in by_agent:
                by_agent[r.agent] = {"total": 0, "passed": 0}
            by_agent[r.agent]["total"] += 1
            if r.evaluation == "PASS":
                by_agent[r.agent]["passed"] += 1

            # By mode
            if r.mode not in by_mode:
                by_mode[r.mode] = {"total": 0, "passed": 0}
            by_mode[r.mode]["total"] += 1
            if r.evaluation == "PASS":
                by_mode[r.mode]["passed"] += 1

            # By evaluation
            if r.evaluation in by_evaluation:
                by_evaluation[r.evaluation] += 1

        return {
            "by_agent": by_agent,
            "by_mode": by_mode,
            "by_evaluation": by_evaluation,
        }

    # Known style names used in bug IDs
    _KNOWN_STYLES = {"original", "camelcase", "snakecase", "badnames", "formatting", "verbose"}

    def _extract_style(self, bug_id: str) -> str:
        """Extract style from bug ID.

        Handles hyphenated repo names like python-markdown and more-itertools
        by searching for known style names rather than splitting on hyphens.

        Examples:
            'humanize-original-001' -> 'original'
            'python-markdown-camelcase-003' -> 'camelcase'
            'more-itertools-badnames-012' -> 'badnames'
        """
        for style in self._KNOWN_STYLES:
            if f"-{style}-" in bug_id:
                return style
        return "unknown"
