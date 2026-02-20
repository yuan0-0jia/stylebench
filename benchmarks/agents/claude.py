"""Claude Code CLI agent for StyleBench.

Wraps the Claude Code CLI to fix bugs in repositories.
"""

import subprocess
import time
from pathlib import Path

from ..evaluator import detect_changes, hash_source_files
from .base import DEFAULT_TIMEOUT, RATE_LIMIT_PATTERNS, Agent, BugContext, FixResult


class ClaudeAgent(Agent):
    """Agent that uses Claude Code CLI to fix bugs.

    Runs Claude Code in non-interactive mode with --print flag
    to attempt fixing bugs based on test failure output.
    """

    name = "claude"

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        model: str | None = None,
        max_retries: int = 3,
    ):
        """Initialize the Claude agent.

        Args:
            timeout: Maximum time in seconds for fix attempt (default: 60s).
            model: Model to use (default: None, uses Claude Code default).
            max_retries: Maximum retries on API errors (default: 3).
        """
        self.timeout = timeout
        self.model = model
        self.max_retries = max_retries

    def _build_prompt(self, context: BugContext) -> str:
        """Build the prompt for Claude Code.

        Args:
            context: Bug context with test output and mode.

        Returns:
            Prompt string for Claude Code.
        """
        # Base prompt with test failure info
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

        # Add mode-specific instructions
        if context.mode == "without_tests":
            prompt += """
- You do not have access to the test files
- Focus on understanding the code logic to find the bug"""
        else:
            prompt += """
- You may read test files to understand expected behavior
- But do NOT modify test files"""

        return prompt

    def _build_command(self, prompt: str, repo_path: Path) -> list[str]:
        """Build the Claude Code CLI command.

        Args:
            prompt: The prompt to send to Claude.
            repo_path: Path to the repository.

        Returns:
            Command list for subprocess.
        """
        cmd = [
            "claude",
            "--print",  # Non-interactive mode
            "--dangerously-skip-permissions",  # Skip permission prompts
        ]

        if self.model:
            cmd.extend(["--model", self.model])

        # Prompt must be last
        cmd.extend(["--", prompt])

        return cmd

    def fix_bug(self, context: BugContext) -> FixResult:
        """Attempt to fix a bug using Claude Code CLI.

        Args:
            context: Bug context with test output, repo path, and mode.

        Returns:
            FixResult with success status, patch, timing, etc.
        """
        start_time = time.time()
        prompt = self._build_prompt(context)
        cmd = self._build_command(prompt, context.repo_path)

        try:
            # Hash all source files before the agent runs
            before_hashes = hash_source_files(context.repo_path)

            # Run Claude Code with retry on API errors
            result = None
            last_error = None
            for attempt in range(self.max_retries):
                result = subprocess.run(
                    cmd,
                    cwd=context.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )

                output = result.stdout + result.stderr
                # Check for transient API errors (500, 529, etc.)
                if "500" in output or "529" in output or "Internal server error" in output:
                    last_error = f"API error on attempt {attempt + 1}"
                    if attempt < self.max_retries - 1:
                        time.sleep(5 * (attempt + 1))  # Exponential backoff
                        continue
                break

            elapsed = time.time() - start_time
            agent_output = result.stdout + result.stderr
            if last_error:
                agent_output = f"[Retried due to: {last_error}]\n" + agent_output

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
                error="Claude Code CLI not found. Is it installed?",
            )

        except Exception as e:
            elapsed = time.time() - start_time
            return FixResult(
                success=False,
                time_seconds=elapsed,
                error=str(e),
            )

