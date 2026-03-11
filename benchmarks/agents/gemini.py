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
        disallow_bash: bool = False,
    ):
        super().__init__(timeout=timeout, model=model, disallow_bash=disallow_bash)
        self.sandbox = sandbox

    def _build_command(self, prompt: str, context: BugContext) -> list[str]:
        cmd = ["gemini"]
        if self.disallow_bash:
            # auto_edit: auto-approve file edits only; shell commands need
            # approval, which blocks them in headless (-p) mode.
            cmd.extend(["--approval-mode", "auto_edit"])
        else:
            cmd.append("-y")
        cmd.extend(["-p", prompt])
        if self.model:
            cmd.extend(["-m", self.model])
        if self.sandbox:
            cmd.extend(["-s"])
        return cmd
