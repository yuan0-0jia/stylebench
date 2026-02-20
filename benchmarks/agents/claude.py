"""Claude Code CLI agent for StyleBench."""

import os
import subprocess
import time

from .base import DEFAULT_TIMEOUT, Agent, BugContext


class ClaudeAgent(Agent):
    """Agent that uses Claude Code CLI to fix bugs."""

    name = "claude"
    cli_not_found_msg = "Claude Code CLI not found. Is it installed?"

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        model: str | None = None,
        max_retries: int = 3,
    ):
        super().__init__(timeout=timeout, model=model)
        self.max_retries = max_retries

    def _build_command(self, prompt: str, context: BugContext) -> list[str]:
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(["--", prompt])
        return cmd

    def _get_env(self) -> dict:
        """Unset CLAUDECODE to allow launching from inside a session."""
        return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    def _run_subprocess(
        self, cmd: list[str], context: BugContext,
    ) -> subprocess.CompletedProcess:
        """Run with retry on transient API errors."""
        result = None
        for attempt in range(self.max_retries):
            result = subprocess.run(
                cmd,
                cwd=context.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._get_env(),
            )
            output = result.stdout + result.stderr
            if "500" in output or "529" in output or "Internal server error" in output:
                if attempt < self.max_retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
            break
        return result
