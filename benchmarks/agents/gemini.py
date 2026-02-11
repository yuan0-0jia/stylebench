"""Gemini CLI agent for StyleBench.

Wraps the Gemini CLI to fix bugs in repositories.
"""

import subprocess
import time
from pathlib import Path

from .base import Agent, BugContext, FixResult


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
            initial_diff = self._get_git_diff(context.repo_path)

            result = subprocess.run(
                cmd,
                cwd=context.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            elapsed = time.time() - start_time
            agent_output = result.stdout + result.stderr

            final_diff = self._get_git_diff(context.repo_path)
            fix_attempted = initial_diff != final_diff

            changed_files = self._get_changed_files(context.repo_path)
            if not changed_files and fix_attempted:
                for line in initial_diff.split("\n"):
                    if line.startswith("+++ b/"):
                        changed_files.append(line[6:])

            return FixResult(
                success=fix_attempted,
                files_changed=changed_files,
                patch=final_diff if final_diff else "(restored to original)",
                time_seconds=elapsed,
                agent_output=agent_output,
                error=None if result.returncode == 0 else f"Exit code: {result.returncode}",
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

    def _get_git_diff(self, repo_path: Path) -> str:
        """Get git diff of changes in a repository."""
        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout
        except Exception:
            return ""

    def _get_changed_files(self, repo_path: Path) -> list[str]:
        """Get list of files changed in a repository."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            return files
        except Exception:
            return []
