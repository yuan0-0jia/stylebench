"""Benchmarking harness for StyleBench."""

from .agents import Agent, BugContext, ClaudeAgent, FixResult, TrialResult
from .evaluator import (
    TestRunResult,
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
from .harness import BenchmarkHarness

__all__ = [
    # Agent classes
    "Agent",
    "BugContext",
    "ClaudeAgent",
    "FixResult",
    "TrialResult",
    # Harness
    "BenchmarkHarness",
    # Evaluator functions
    "TestRunResult",
    "apply_bug",
    "create_working_copy",
    "evaluate_fix",
    "get_bug_by_id",
    "get_changed_files",
    "get_git_diff",
    "hide_tests",
    "load_bug_catalog",
    "restore_tests",
    "revert_bug",
    "run_tests",
]
