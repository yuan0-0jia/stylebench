"""Agent implementations for StyleBench."""

from .base import Agent, BugContext, FixResult, TrialResult
from .claude import ClaudeAgent

__all__ = ["Agent", "BugContext", "FixResult", "TrialResult", "ClaudeAgent"]
