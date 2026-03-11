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
        ]
        if self.disallow_bash:
            # Allow trusted file operations (cat, sed, ls) but block
            # untrusted commands (pytest, python) in non-interactive exec mode.
            cmd.extend(["--sandbox", "workspace-write", "-a", "untrusted"])
        else:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        cmd.extend(
            [
                "--skip-git-repo-check",
                prompt,
            ]
        )
        if self.model:
            cmd.extend(["--model", self.model])
        return cmd
