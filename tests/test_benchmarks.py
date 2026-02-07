"""Tests for the benchmarks module."""

import tempfile
from pathlib import Path

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
    evaluate_fix,
    get_bug_by_id,
    get_changed_files,
    get_git_diff,
    hide_tests,
    load_bug_catalog,
    restore_tests,
    revert_bug,
    run_tests,
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


class TestGitDiff:
    """Tests for get_git_diff and get_changed_files functions."""

    def test_no_changes(self, humanize_repo):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            diff = get_git_diff(work_dir)
            changed = get_changed_files(work_dir)

            # No changes yet
            assert diff == ""
            assert changed == []

    def test_with_changes(self, humanize_catalog, humanize_repo):
        bug, hidden = get_bug_by_id(humanize_catalog, "humanize-original-001")

        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = create_working_copy(humanize_repo, Path(tmpdir) / "work")

            # Apply bug (makes a change)
            apply_bug(work_dir, hidden)

            diff = get_git_diff(work_dir)
            changed = get_changed_files(work_dir)

            assert diff != ""
            assert len(changed) > 0
            assert hidden["file_path"] in changed


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
