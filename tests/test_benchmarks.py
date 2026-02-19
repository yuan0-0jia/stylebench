"""Tests for the benchmarks module."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarks import (
    Agent,
    BenchmarkHarness,
    BugContext,
    ClaudeAgent,
    FixResult,
    TestRunResult,
    TrialResult,
    apply_bug,
    create_working_copy,
    detect_changes,
    evaluate_fix,
    get_bug_by_id,
    hash_source_files,
    hide_tests,
    load_bug_catalog,
    lock_tests,
    restore_tests,
    revert_bug,
    run_tests,
    unlock_tests,
)


class TestBugContext:
    """Tests for BugContext dataclass."""

    def test_valid_with_tests_mode(self):
        ctx = BugContext(
            repo_path=Path("/tmp/test"),
            test_output="FAILED test_foo",
            failing_tests=["test_foo"],
            mode="with_tests",
        )
        assert ctx.mode == "with_tests"

    def test_valid_without_tests_mode(self):
        ctx = BugContext(
            repo_path=Path("/tmp/test"),
            test_output="FAILED test_foo",
            failing_tests=["test_foo"],
            mode="without_tests",
        )
        assert ctx.mode == "without_tests"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            BugContext(
                repo_path=Path("/tmp/test"),
                test_output="FAILED",
                failing_tests=[],
                mode="invalid_mode",
            )

    def test_optional_fields(self):
        ctx = BugContext(
            repo_path=Path("/tmp/test"),
            test_output="FAILED",
            failing_tests=["test_foo"],
            mode="with_tests",
            bug_id="test-001",
            repo_name="humanize",
            style="original",
        )
        assert ctx.bug_id == "test-001"
        assert ctx.repo_name == "humanize"
        assert ctx.style == "original"


class TestFixResult:
    """Tests for FixResult dataclass."""

    def test_successful_fix(self):
        fix = FixResult(
            success=True,
            files_changed=["foo.py", "bar.py"],
            patch="diff --git...",
            time_seconds=10.5,
        )
        assert fix.success is True
        assert len(fix.files_changed) == 2
        assert fix.error is None

    def test_failed_fix(self):
        fix = FixResult(
            success=False,
            error="Agent timed out",
            time_seconds=300.0,
        )
        assert fix.success is False
        assert fix.error == "Agent timed out"
        assert fix.files_changed == []

    def test_default_values(self):
        fix = FixResult(success=True)
        assert fix.files_changed == []
        assert fix.patch == ""
        assert fix.tokens_used == 0
        assert fix.time_seconds == 0.0
        assert fix.error is None


class TestTrialResult:
    """Tests for TrialResult dataclass."""

    def test_to_dict(self):
        fix = FixResult(success=True, files_changed=["foo.py"])
        trial = TrialResult(
            bug_id="test-001",
            agent="claude",
            mode="with_tests",
            fix_result=fix,
            evaluation="PASS",
            tests_passed=10,
            tests_failed=0,
            tests_total=10,
        )

        d = trial.to_dict()
        assert d["bug_id"] == "test-001"
        assert d["agent"] == "claude"
        assert d["evaluation"] == "PASS"
        assert d["fix_result"]["success"] is True
        assert "timestamp" in d

    def test_evaluation_values(self):
        fix = FixResult(success=False)
        for eval_type in ["PASS", "FAIL", "ERROR", "TIMEOUT", "NO_FIX"]:
            trial = TrialResult(
                bug_id="test-001",
                agent="claude",
                mode="with_tests",
                fix_result=fix,
                evaluation=eval_type,
            )
            assert trial.evaluation == eval_type


class TestAgent:
    """Tests for Agent ABC."""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Agent()

    def test_subclass_must_implement_fix_bug(self):
        class IncompleteAgent(Agent):
            pass

        with pytest.raises(TypeError):
            IncompleteAgent()

    def test_valid_subclass(self):
        class DummyAgent(Agent):
            name = "dummy"

            def fix_bug(self, context: BugContext) -> FixResult:
                return FixResult(success=False, error="Not implemented")

        agent = DummyAgent()
        assert agent.get_name() == "dummy"

        ctx = BugContext(
            repo_path=Path("/tmp"),
            test_output="FAILED",
            failing_tests=[],
            mode="with_tests",
        )
        result = agent.fix_bug(ctx)
        assert result.success is False


class TestEvaluateFix:
    """Tests for evaluate_fix function."""

    def test_pass_when_all_tests_pass(self):
        before = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )
        after = TestRunResult(
            exit_code=0, passed=10, failed=0, total=10, output="", failing_tests=[]
        )

        result = evaluate_fix(before, after, ["test_foo"])
        assert result == "PASS"

    def test_fail_when_tests_still_fail(self):
        before = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )
        after = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )

        result = evaluate_fix(before, after, ["test_foo"])
        assert result == "FAIL"

    def test_error_when_more_failures(self):
        before = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )
        after = TestRunResult(
            exit_code=1,
            passed=7,
            failed=3,
            total=10,
            output="",
            failing_tests=["test_foo", "test_bar", "test_baz"],
        )

        result = evaluate_fix(before, after, ["test_foo"])
        assert result == "ERROR"

    def test_timeout(self):
        before = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )
        after = TestRunResult(
            exit_code=-1,
            passed=0,
            failed=0,
            total=0,
            output="TIMEOUT: Test execution exceeded time limit",
            failing_tests=[],
        )

        result = evaluate_fix(before, after, ["test_foo"])
        assert result == "TIMEOUT"

    def test_error_on_test_failure(self):
        before = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10, output="", failing_tests=["test_foo"]
        )
        after = TestRunResult(
            exit_code=-1,
            passed=0,
            failed=0,
            total=0,
            output="ERROR: Something went wrong",
            failing_tests=[],
        )

        result = evaluate_fix(before, after, ["test_foo"])
        assert result == "ERROR"


# Integration tests that use real data
@pytest.fixture
def humanize_catalog():
    """Load the humanize-original bug catalog."""
    catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
    if not catalog_path.exists():
        pytest.skip("Bug catalog not found")
    return load_bug_catalog(catalog_path)


@pytest.fixture
def humanize_repo():
    """Path to humanize repository."""
    repo_path = Path("/Users/yuan/stylebench-data/original/humanize")
    if not repo_path.exists():
        pytest.skip("Repository not found")
    return repo_path


class TestLoadBugCatalog:
    """Tests for load_bug_catalog function."""

    def test_load_catalog(self, humanize_catalog):
        assert "bugs" in humanize_catalog
        assert "_hidden" in humanize_catalog
        assert len(humanize_catalog["bugs"]) == 50
        assert len(humanize_catalog["_hidden"]) == 50

    def test_get_bug_by_id(self, humanize_catalog):
        bug, hidden = get_bug_by_id(humanize_catalog, "humanize-original-001")
        assert bug is not None
        assert hidden is not None
        assert bug["bug_id"] == "humanize-original-001"
        assert "file_path" in hidden
        assert "mutation_type" in hidden

    def test_get_bug_by_id_not_found(self, humanize_catalog):
        result = get_bug_by_id(humanize_catalog, "nonexistent-bug")
        assert result is None


class TestWorkingCopy:
    """Tests for create_working_copy function."""

    def test_create_working_copy(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")
            assert work_dir.exists()
            assert (work_dir / "src" / "humanize").exists()
            assert (work_dir / "tests").exists()


class TestApplyBug:
    """Tests for apply_bug and revert_bug functions."""

    def test_apply_and_revert_bug(self, humanize_catalog, humanize_repo):
        bug, hidden = get_bug_by_id(humanize_catalog, "humanize-original-001")

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            # Read original content
            file_path = work_dir / hidden["file_path"]
            original_content = file_path.read_text()
            assert hidden["original_text"] in original_content

            # Apply bug
            success = apply_bug(work_dir, hidden)
            assert success is True

            # Verify mutation applied - content should be different
            mutated_content = file_path.read_text()
            assert mutated_content != original_content
            assert hidden["mutated_text"] in mutated_content

            # Revert bug
            success = revert_bug(work_dir, hidden)
            assert success is True

            # Verify reverted - should match original
            reverted_content = file_path.read_text()
            assert reverted_content == original_content


class TestHideRestoreTests:
    """Tests for hide_tests and restore_tests functions."""

    def test_hide_and_restore_tests(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")
            test_dir = work_dir / "tests"

            # Tests should exist initially
            assert test_dir.exists()

            # Hide tests
            hidden_path = hide_tests(work_dir, "humanize")
            assert hidden_path is not None
            assert not test_dir.exists()
            assert hidden_path.exists()

            # Restore tests
            success = restore_tests(work_dir, hidden_path, "humanize")
            assert success is True
            assert test_dir.exists()
            assert not hidden_path.exists()


class TestLockUnlockTests:
    """Tests for lock_tests and unlock_tests functions."""

    def test_lock_makes_tests_readonly(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")
            test_dir = work_dir / "tests"
            test_file = next(test_dir.rglob("*.py"))

            # Lock tests
            lock_tests(work_dir, "humanize")

            # Test files should be read-only
            import os

            assert not os.access(test_file, os.W_OK)

            # Writing should raise PermissionError
            with pytest.raises(PermissionError):
                test_file.write_text("should fail")

    def test_unlock_restores_writable(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")
            test_dir = work_dir / "tests"
            test_file = next(test_dir.rglob("*.py"))

            lock_tests(work_dir, "humanize")
            unlock_tests(work_dir, "humanize")

            # Test files should be writable again
            import os

            assert os.access(test_file, os.W_OK)


class TestRunTests:
    """Tests for run_tests function."""

    def test_run_tests_passing(self, humanize_repo):
        """Test that clean repo passes all tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            result = run_tests(work_dir, "humanize", timeout=60)
            assert result.exit_code == 0
            assert result.failed == 0
            assert result.passed > 0

    def test_run_tests_with_bug(self, humanize_catalog, humanize_repo):
        """Test that buggy repo fails tests."""
        bug, hidden = get_bug_by_id(humanize_catalog, "humanize-original-001")

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            # Apply bug
            apply_bug(work_dir, hidden)

            # Run tests - should fail
            result = run_tests(work_dir, "humanize", timeout=60)
            assert result.exit_code != 0
            assert result.failed > 0
            assert len(result.failing_tests) > 0


class TestHashChangeDetection:
    """Tests for hash_source_files and detect_changes functions."""

    def test_no_changes(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            before = hash_source_files(work_dir)
            after = hash_source_files(work_dir)

            changed = detect_changes(before, after)
            assert changed == []

    def test_with_changes(self, humanize_catalog, humanize_repo):
        bug, hidden = get_bug_by_id(humanize_catalog, "humanize-original-001")

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            before = hash_source_files(work_dir)

            # Apply bug (makes a change)
            apply_bug(work_dir, hidden)

            after = hash_source_files(work_dir)
            changed = detect_changes(before, after)

            assert len(changed) > 0
            assert hidden["file_path"] in changed

    def test_no_git_dir_in_working_copy(self, humanize_repo):
        """Verify .git is excluded from working copies to prevent diff leakage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")
            assert not (work_dir / ".git").exists()


class TestClaudeAgent:
    """Tests for ClaudeAgent class."""

    def test_initialization_defaults(self):
        agent = ClaudeAgent()
        assert agent.timeout == 300
        assert agent.max_turns == 10
        assert agent.model is None
        assert agent.get_name() == "claude"

    def test_initialization_custom(self):
        agent = ClaudeAgent(timeout=600, max_turns=20, model="opus")
        assert agent.timeout == 600
        assert agent.max_turns == 20
        assert agent.model == "opus"

    def test_build_prompt_with_tests(self):
        agent = ClaudeAgent()
        ctx = BugContext(
            repo_path=Path("/tmp/test"),
            test_output="FAILED test_foo - AssertionError",
            failing_tests=["test_foo"],
            mode="with_tests",
        )

        prompt = agent._build_prompt(ctx)
        assert "FAILED test_foo" in prompt
        assert "You may read test files" in prompt
        assert "do not modify test files" in prompt.lower()

    def test_build_prompt_without_tests(self):
        agent = ClaudeAgent()
        ctx = BugContext(
            repo_path=Path("/tmp/test"),
            test_output="FAILED test_foo - AssertionError",
            failing_tests=["test_foo"],
            mode="without_tests",
        )

        prompt = agent._build_prompt(ctx)
        assert "FAILED test_foo" in prompt
        assert "do not have access to the test files" in prompt.lower()

    def test_build_command_basic(self):
        agent = ClaudeAgent()
        cmd = agent._build_command("Fix the bug", Path("/tmp/repo"))

        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--max-turns=10" in cmd
        assert "Fix the bug" in cmd

    def test_build_command_with_model(self):
        agent = ClaudeAgent(model="opus", max_turns=5)
        cmd = agent._build_command("Fix the bug", Path("/tmp/repo"))

        assert "--model" in cmd
        assert "opus" in cmd
        assert "--max-turns=5" in cmd

    def test_fix_bug_cli_not_found(self):
        """Test handling when Claude CLI is not installed."""
        agent = ClaudeAgent(timeout=5)
        ctx = BugContext(
            repo_path=Path("/nonexistent/path"),
            test_output="FAILED test_foo",
            failing_tests=["test_foo"],
            mode="with_tests",
        )

        # This should handle FileNotFoundError gracefully
        result = agent.fix_bug(ctx)
        assert result.success is False
        assert result.error is not None


class TestBenchmarkHarness:
    """Tests for BenchmarkHarness class."""

    def test_initialization(self, humanize_catalog, humanize_repo):
        """Test harness can be initialized."""
        catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=humanize_repo,
            repo_name="humanize",
        )

        assert harness.catalog is not None
        assert len(harness.catalog.get("bugs", [])) == 50
        assert harness.output_dir.exists()

    def test_extract_style(self, humanize_catalog, humanize_repo):
        """Test style extraction from bug IDs."""
        catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=humanize_repo,
            repo_name="humanize",
        )

        assert harness._extract_style("humanize-original-001") == "original"
        assert harness._extract_style("humanize-verbose-042") == "verbose"
        assert harness._extract_style("python-markdown-original-001") == "original"
        assert harness._extract_style("python-markdown-camelcase-003") == "camelcase"
        assert harness._extract_style("more-itertools-badnames-012") == "badnames"
        assert harness._extract_style("unknown") == "unknown"

    def test_compute_summary_empty(self, humanize_catalog, humanize_repo):
        """Test summary computation with no results."""
        catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=humanize_repo,
            repo_name="humanize",
        )

        summary = harness._compute_summary()
        assert summary == {}

    def test_compute_summary_with_results(self, humanize_catalog, humanize_repo):
        """Test summary computation with mock results."""
        catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=humanize_repo,
            repo_name="humanize",
        )

        # Add mock results
        harness.results = [
            TrialResult(
                bug_id="test-001",
                agent="claude",
                mode="with_tests",
                fix_result=FixResult(success=True),
                evaluation="PASS",
            ),
            TrialResult(
                bug_id="test-002",
                agent="claude",
                mode="with_tests",
                fix_result=FixResult(success=False),
                evaluation="FAIL",
            ),
            TrialResult(
                bug_id="test-003",
                agent="claude",
                mode="without_tests",
                fix_result=FixResult(success=True),
                evaluation="PASS",
            ),
        ]

        summary = harness._compute_summary()

        assert summary["by_agent"]["claude"]["total"] == 3
        assert summary["by_agent"]["claude"]["passed"] == 2
        assert summary["by_mode"]["with_tests"]["total"] == 2
        assert summary["by_mode"]["with_tests"]["passed"] == 1
        assert summary["by_mode"]["without_tests"]["total"] == 1
        assert summary["by_evaluation"]["PASS"] == 2
        assert summary["by_evaluation"]["FAIL"] == 1

    def test_run_trial_bug_not_found(self, humanize_catalog, humanize_repo):
        """Test handling of non-existent bug ID."""
        catalog_path = Path("/Users/yuan/stylebench-data/bugs/humanize-original.json")
        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=humanize_repo,
            repo_name="humanize",
        )

        # Create a dummy agent
        class DummyAgent(Agent):
            name = "dummy"

            def fix_bug(self, context: BugContext) -> FixResult:
                return FixResult(success=True)

        agent = DummyAgent()
        result = harness.run_trial(agent, "nonexistent-bug-999")

        assert result.evaluation == "ERROR"
        assert "not found" in result.fix_result.error


class TestRateLimitWorkflow:
    """End-to-end test for the rate-limit recovery workflow.

    Verifies that:
    1. Rate-limited trials are dropped from saved results
    2. The hit_rate_limit flag is set in result file metadata
    3. The script layer detects it and skips recording the bug
    4. The bug shows up as pending on the next run
    """

    def _make_catalog(self, tmp_path: Path, bug_ids: list[str]) -> Path:
        """Create a minimal bug catalog with the given bug IDs."""
        catalog = {
            "bugs": [
                {
                    "bug_id": bid,
                    "exit_code": 1,
                    "failing_tests": ["tests/test_foo.py::test_bar"],
                    "test_output": "FAILED tests/test_foo.py::test_bar",
                }
                for bid in bug_ids
            ],
            "_hidden": [
                {
                    "bug_id": bid,
                    "file_path": "src/foo.py",
                    "original_text": "x == y",
                    "mutated_text": "x != y",
                    "line_number": 1,
                    "context": "",
                    "mutation_type": "eq_ne",
                }
                for bid in bug_ids
            ],
        }
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog))
        return catalog_path

    def _make_repo(self, tmp_path: Path) -> Path:
        """Create a minimal repo with a source file and test dir."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "foo.py").write_text("x == y\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_foo.py").write_text("def test_bar(): pass\n")
        return repo

    def test_harness_drops_rate_limited_trial(self, tmp_path):
        """Rate-limited trial should not appear in saved results."""
        bug_ids = ["test-original-001", "test-original-002", "test-original-003"]
        catalog_path = self._make_catalog(tmp_path, bug_ids)
        repo = self._make_repo(tmp_path)
        output_dir = tmp_path / "results"

        # Mock run_tests to simulate: before=failing, after=passing
        failing_result = TestRunResult(
            exit_code=1, passed=9, failed=1, total=10,
            output="FAILED tests/test_foo.py::test_bar",
            failing_tests=["tests/test_foo.py::test_bar"],
        )
        passing_result = TestRunResult(
            exit_code=0, passed=10, failed=0, total=10,
            output="10 passed",
            failing_tests=[],
        )

        # Agent that succeeds on first bug, then gets rate-limited on second
        call_count = 0
        run_tests_call_count = 0

        class RateLimitAgent(Agent):
            name = "mock"

            def fix_bug(self, context: BugContext) -> FixResult:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return FixResult(success=True, files_changed=["src/foo.py"])
                else:
                    return FixResult(
                        success=False,
                        rate_limited=True,
                        agent_output="Error: you hit your limit",
                    )

        def mock_run_tests(repo_path, repo_name, timeout=120):
            nonlocal run_tests_call_count
            run_tests_call_count += 1
            # Odd calls = before (failing), even calls = after (passing)
            if run_tests_call_count % 2 == 1:
                return failing_result
            return passing_result

        harness = BenchmarkHarness(
            catalog_path=catalog_path,
            repo_path=repo,
            repo_name="humanize",
            output_dir=output_dir,
            manifest={
                "trials": [
                    {
                        "bug_id": bid,
                        "test_output": "FAILED tests/test_foo.py::test_bar",
                        "failing_tests": ["tests/test_foo.py::test_bar"],
                    }
                    for bid in bug_ids
                ],
            },
        )

        with patch("benchmarks.harness.run_tests", side_effect=mock_run_tests):
            results = harness.run_all(agent=RateLimitAgent(), bug_ids=bug_ids, mode="with_tests")

        # run_all should return only the first (clean) result
        assert len(results) == 1
        assert results[0].bug_id == "test-original-001"

        # harness.results (used for saving) should also have only the clean result
        assert len(harness.results) == 1
        assert harness.results[0].bug_id == "test-original-001"

        # hit_rate_limit flag should be set
        assert harness.hit_rate_limit is True

        # Agent should have been called twice (first OK, second rate-limited, third never called)
        assert call_count == 2

        # Save results and verify the file
        result_path = harness.save_results()
        with open(result_path) as f:
            data = json.load(f)

        # Metadata should signal rate limiting
        assert data["metadata"]["hit_rate_limit"] is True

        # Only the clean trial should be in results
        assert len(data["results"]) == 1
        assert data["results"][0]["bug_id"] == "test-original-001"
        assert data["results"][0]["fix_result"]["rate_limited"] is False

    def test_script_parse_result_file(self, tmp_path):
        """Script layer should detect rate limiting from metadata."""
        # Add scripts dir to path so we can import run_benchmark
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        try:
            import run_benchmark
        finally:
            sys.path.pop(0)

        # Create a result file with hit_rate_limit in metadata
        result_file = tmp_path / "results_test.json"
        result_file.write_text(json.dumps({
            "metadata": {
                "hit_rate_limit": True,
                "total_trials": 1,
            },
            "results": [
                {
                    "bug_id": "test-original-001",
                    "evaluation": "PASS",
                    "fix_result": {"rate_limited": False},
                },
            ],
        }))

        parsed, hit_rate_limit = run_benchmark.parse_result_file(result_file)

        assert hit_rate_limit is True
        assert len(parsed) == 1
        assert parsed[0]["bug_id"] == "test-original-001"

    def test_script_pending_bugs_after_rate_limit(self, tmp_path):
        """Rate-limited bug should show up as pending on resume."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        try:
            import run_benchmark
        finally:
            sys.path.pop(0)

        # Simulate state after a rate-limited run:
        # bug 001 completed, bug 002 was rate-limited (not recorded), bug 003 never attempted
        state = run_benchmark._empty_state()
        run_benchmark.record_results(state, "with_tests", [
            {"bug_id": "test-original-001", "evaluation": "PASS"},
        ])

        assert "test-original-001" in state["completed_bugs"]["with_tests"]
        # 002 and 003 should not be in completed_bugs
        assert "test-original-002" not in state["completed_bugs"].get("with_tests", {})
        assert "test-original-003" not in state["completed_bugs"].get("with_tests", {})
