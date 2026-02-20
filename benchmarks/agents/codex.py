"""OpenAI Codex CLI agent for StyleBench."""

from .base import Agent, BugContext


class CodexAgent(Agent):
    """Agent that uses Codex CLI to fix bugs."""

    name = "codex"
    cli_not_found_msg = "Codex CLI not found. Is it installed?"

    def _build_command(self, prompt: str, context: BugContext) -> list[str]:
        cmd = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            prompt,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd
