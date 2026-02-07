"""Claude Code CLI agent for StyleBench.

Wraps the Claude Code CLI to fix bugs in repositories.
"""

import subprocess
import time
from pathlib import Path

from .base import Agent, BugContext, FixResult


class ClaudeAgent(Agent):
    """Agent that uses Claude Code CLI to fix bugs.

    Runs Claude Code in non-interactive mode with --print flag
    to attempt fixing bugs based on test failure output.
    """

    name = "claude"

    def __init__(
        self,
        timeout: int = 300,
        max_turns: int = 10,
        model: str | None = None,
        max_retries: int = 3,
    ):
        """Initialize the Claude agent.

        Args:
            timeout: Maximum time in seconds for fix attempt (default: 5 min).
            max_turns: Maximum number of agentic turns (default: 10).
            model: Model to use (default: None, uses Claude Code default).
            max_retries: Maximum retries on API errors (default: 3).
        """
        self.timeout = timeout
        self.max_turns = max_turns
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
        prompt = f"""The tests in this repository are failing. Use your tools to find and fix the bug.

Test failure output:
{context.test_output}

Instructions:
- Use the Read and Grep tools to find the bug location
- Use the Edit tool to fix the bug in the source code
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
            f"--max-turns={self.max_turns}",
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
            # Get initial git state (repo has bug applied, so should have diff)
            initial_diff = self._get_git_diff(context.repo_path)

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

            # Get changes made by Claude
            final_diff = self._get_git_diff(context.repo_path)

            # Determine if any fix was attempted
            # Success if: initial had diff (bug applied) and final diff changed
            # This includes the case where Claude fixed the bug back to original
            # (final_diff would be empty, but it changed from initial)
            fix_attempted = initial_diff != final_diff

            # Get changed files - compare to HEAD to see what Claude touched
            # even if it restored to original
            changed_files = self._get_changed_files(context.repo_path)
            if not changed_files and fix_attempted:
                # Claude fixed back to original - the bug file was touched
                # Extract file from initial diff
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
                error="Claude Code CLI not found. Is it installed?",
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
