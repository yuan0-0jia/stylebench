"""
Code style transformers for StyleBench.

Available transformers:
- CamelCaseTransformer: Convert snake_case to camelCase
- SnakeCaseTransformer: Convert camelCase to snake_case
- BadNamingTransformer: Convert descriptive names to single-letter names
- RuffFormatter: Format code using ruff with configurable settings
- FormattingTransformer: High-level formatting with predefined styles
"""

from .base import Transformer, TransformResult
from .formatting import FormattingTransformer, RuffConfig, RuffFormatter
from .naming import (
    BadNamingTransformer,
    CamelCaseTransformer,
    SnakeCaseTransformer,
    camel_to_snake,
    snake_to_camel,
)

__all__ = [
    # Base classes
    "Transformer",
    "TransformResult",
    # Naming transformers
    "CamelCaseTransformer",
    "SnakeCaseTransformer",
    "BadNamingTransformer",
    # Naming utilities
    "snake_to_camel",
    "camel_to_snake",
    # Formatting transformers
    "RuffFormatter",
    "RuffConfig",
    "FormattingTransformer",
]
