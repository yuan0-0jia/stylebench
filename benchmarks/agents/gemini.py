"""Gemini CLI agent for StyleBench.

Wraps the Gemini CLI to fix bugs in repositories.
"""

import subprocess
import time

from ..evaluator import detect_changes, hash_source_files
from .base import RATE_LIMIT_PATTERNS, Agent, BugContext, FixResult


class GeminiAgent(Agent):
    """Agent that uses Gemini CLI to fix bugs.

    Runs Gemini CLI in non-interactive mode with -p flag
    to attempt fixing bugs based on test failure output.
    """

    name = "gemini"

    def __init__(
        self,
        timeout: int = 300,
        model: str | None = None,
        sandbox: bool = False,
    ):
        """Initialize the Gemini agent.

        Args:
            timeout: Maximum time in seconds for fix attempt (default: 5 min).
            model: Model to use (default: None, uses Gemini CLI default).
            sandbox: Whether to run in sandbox mode (default: False).
        """
        self.timeout = timeout
        self.model = model
        self.sandbox = sandbox

    def _build_prompt(self, context: BugContext) -> str:
        """Build the prompt for Gemini CLI."""
        prompt = f"""The tests in this repository are failing.
Find and fix the bug.

Test failure output:
{context.test_output}

Instructions:
- Read the source code files to find the bug location
- Edit the source code to fix the bug
- Do NOT modify any test files
- Make minimal changes to fix the issue
- The bug is likely a simple logic error, off-by-one error, or similar"""

        if context.mode == "without_tests":
            prompt += """
- You do not have access to the test files
- Focus on understanding the code logic to find the bug"""
        else:
            prompt += """
- You may read test files to understand expected behavior
- But do NOT modify test files"""

        return prompt

    def _build_command(self, prompt: str) -> list[str]:
        """Build the Gemini CLI command."""
        cmd = [
            "gemini",
            "-y",  # Auto-approve all actions (yolo mode)
            "-p",
            prompt,  # Non-interactive mode
        ]

        if self.model:
            cmd.extend(["-m", self.model])

        if self.sandbox:
            cmd.extend(["-s"])

        return cmd

    def fix_bug(self, context: BugContext) -> FixResult:
        """Attempt to fix a bug using Gemini CLI."""
        start_time = time.time()
        prompt = self._build_prompt(context)
        cmd = self._build_command(prompt)

        try:
            # Hash all source files before the agent runs
            before_hashes = hash_source_files(context.repo_path)

            result = subprocess.run(
                cmd,
                cwd=context.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            elapsed = time.time() - start_time
            agent_output = result.stdout + result.stderr

            # Hash all source files after the agent runs
            after_hashes = hash_source_files(context.repo_path)

            # Check if the agent was rate-limited
            output_lower = agent_output.lower()
            was_rate_limited = any(p in output_lower for p in RATE_LIMIT_PATTERNS)

            # Determine if any fix was attempted by comparing hashes
            fix_attempted = before_hashes != after_hashes

            # Get list of changed files from hash comparison
            changed_files = detect_changes(before_hashes, after_hashes)

            return FixResult(
                success=fix_attempted,
                files_changed=changed_files,
                patch="(hash-based change detection)",
                time_seconds=elapsed,
                agent_output=agent_output,
                error=None if result.returncode == 0 else f"Exit code: {result.returncode}",
                rate_limited=was_rate_limited,
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=f"Timeout after {self.timeout} seconds",
            )

        except FileNotFoundError:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error="Gemini CLI not found. Is it installed?",
            )

        except Exception as e:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=str(e),
            )

