"""Gemini CLI agent for StyleBench."""

from .base import DEFAULT_TIMEOUT, Agent, BugContext


class GeminiAgent(Agent):
    """Agent that uses Gemini CLI to fix bugs."""

    name = "gemini"
    cli_not_found_msg = "Gemini CLI not found. Is it installed?"

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        model: str | None = None,
        sandbox: bool = False,
    ):
        super().__init__(timeout=timeout, model=model)
        self.sandbox = sandbox

    def _build_command(self, prompt: str, context: BugContext) -> list[str]:
        cmd = [
            "gemini",
            "-y",
            "-p",
            prompt,
        ]
        if self.model:
            cmd.extend(["-m", self.model])
        if self.sandbox:
            cmd.extend(["-s"])
        return cmd
