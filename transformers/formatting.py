"""
Formatting transformers using ruff.

Applies different formatting configurations to transform code style.
"""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .base import Transformer, TransformResult


@dataclass
class RuffConfig:
    """Configuration for ruff formatter."""

    line_length: int = 88
    indent_width: int = 4
    quote_style: str = "double"  # "double" or "single"
    skip_magic_trailing_comma: bool = False

    def to_toml(self) -> str:
        """Generate ruff configuration as TOML string."""
        return f"""
[format]
line-length = {self.line_length}
indent-width = {self.indent_width}
quote-style = "{self.quote_style}"
skip-magic-trailing-comma = {str(self.skip_magic_trailing_comma).lower()}
"""


# Predefined formatting profiles
PROFILES = {
    "default": RuffConfig(
        line_length=88,
        indent_width=4,
        quote_style="double",
    ),
    "pep8_strict": RuffConfig(
        line_length=79,
        indent_width=4,
        quote_style="double",
    ),
    "wide": RuffConfig(
        line_length=120,
        indent_width=4,
        quote_style="double",
    ),
    "compact": RuffConfig(
        line_length=79,
        indent_width=2,
        quote_style="single",
        skip_magic_trailing_comma=True,
    ),
}


class RuffFormatter(Transformer):
    """
    Format code using ruff with configurable settings.

    Uses 'uv run ruff format' to apply formatting.
    """

    def __init__(self, config: RuffConfig | None = None, profile: str | None = None):
        """
        Initialize the ruff formatter.

        Args:
            config: Custom RuffConfig to use
            profile: Name of predefined profile ("default", "pep8_strict", "wide", "compact")
        """
        super().__init__()

        if profile and profile in PROFILES:
            self.config = PROFILES[profile]
        elif config:
            self.config = config
        else:
            self.config = PROFILES["default"]

        self.profile_name = profile or "custom"

    def transform(self, source_code: str) -> TransformResult:
        """Format source code using ruff."""
        # Write source to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as src_file:
            src_file.write(source_code)
            src_path = Path(src_file.name)

        # Write config to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as cfg_file:
            cfg_file.write(self.config.to_toml())
            cfg_path = Path(cfg_file.name)

        try:
            # Run ruff format
            result = subprocess.run(
                [
                    "uv", "run", "ruff", "format",
                    "--config", str(cfg_path),
                    str(src_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                # Formatting failed
                return TransformResult(
                    original_code=source_code,
                    transformed_code=source_code,
                    changes_made=0,
                    details=[f"Ruff formatting failed: {result.stderr}"],
                )

            # Read formatted code
            formatted_code = src_path.read_text()

            # Count changes (rough estimate based on diff)
            original_lines = source_code.splitlines()
            formatted_lines = formatted_code.splitlines()
            changes = sum(
                1 for a, b in zip(original_lines, formatted_lines) if a != b
            )
            changes += abs(len(original_lines) - len(formatted_lines))

            details = [
                f"Applied ruff formatting with profile '{self.profile_name}':",
                f"  Line length: {self.config.line_length}",
                f"  Indent width: {self.config.indent_width}",
                f"  Quote style: {self.config.quote_style}",
            ]

            if source_code == formatted_code:
                details.append("  No changes needed (already formatted)")
                changes = 0

            return TransformResult(
                original_code=source_code,
                transformed_code=formatted_code,
                changes_made=changes,
                details=details,
            )

        except subprocess.TimeoutExpired:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["Ruff formatting timed out"],
            )

        except FileNotFoundError:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["Ruff not found. Install with: uv add ruff"],
            )

        finally:
            # Clean up temp files
            src_path.unlink(missing_ok=True)
            cfg_path.unlink(missing_ok=True)


class FormattingTransformer(Transformer):
    """
    High-level formatting transformer that can apply multiple formatting styles.

    This is a convenience wrapper around RuffFormatter for common use cases.
    """

    def __init__(self, style: str = "default"):
        """
        Initialize with a formatting style.

        Args:
            style: One of "default", "pep8_strict", "wide", "compact"
        """
        super().__init__()
        self.style = style
        self.formatter = RuffFormatter(profile=style)

    def transform(self, source_code: str) -> TransformResult:
        """Apply formatting transformation."""
        return self.formatter.transform(source_code)
