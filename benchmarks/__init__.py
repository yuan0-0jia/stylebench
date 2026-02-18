"""Benchmarking harness for StyleBench."""

from .agents import Agent, BugContext, ClaudeAgent, FixResult, TrialResult
from .evaluator import (
    TestRunResult,
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
    "detect_changes",
    "evaluate_fix",
    "get_bug_by_id",
    "hash_source_files",
    "hide_tests",
    "load_bug_catalog",
    "lock_tests",
    "restore_tests",
    "unlock_tests",
    "revert_bug",
    "run_tests",
]
