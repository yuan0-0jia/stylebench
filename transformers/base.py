"""
Base class for code style transformers.

All transformers inherit from this class and implement the transform() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser


@dataclass
class TransformResult:
    """Result of a transformation operation."""

    original_code: str
    transformed_code: str
    changes_made: int  # Number of changes applied
    details: list[str]  # Human-readable descriptions of changes


class Transformer(ABC):
    """Abstract base class for code style transformers."""

    def __init__(self):
        """Initialize the parser with Python language."""
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse(self, source_code: str) -> Node:
        """Parse source code and return the AST root."""
        tree = self.parser.parse(source_code.encode())
        return tree.root_node

    @abstractmethod
    def transform(self, source_code: str) -> TransformResult:
        """
        Transform the source code according to the style rules.

        Args:
            source_code: Original Python source code

        Returns:
            TransformResult with the transformed code and metadata
        """
        pass

    def transform_file(self, file_path: Path | str) -> TransformResult:
        """
        Transform a file in place.

        Args:
            file_path: Path to the Python file

        Returns:
            TransformResult with transformation details
        """
        file_path = Path(file_path)
        source_code = file_path.read_text()
        result = self.transform(source_code)
        return result

    def transform_file_inplace(self, file_path: Path | str) -> TransformResult:
        """
        Transform a file and write back to the same location.

        Args:
            file_path: Path to the Python file

        Returns:
            TransformResult with transformation details
        """
        file_path = Path(file_path)
        result = self.transform_file(file_path)
        if result.changes_made > 0:
            file_path.write_text(result.transformed_code)
        return result

    def transform_directory(
        self,
        directory: Path | str,
        pattern: str = "**/*.py",
        exclude_patterns: list[str] | None = None,
    ) -> dict[str, TransformResult]:
        """
        Transform all Python files in a directory.

        Args:
            directory: Root directory to transform
            pattern: Glob pattern for files to include
            exclude_patterns: List of patterns to exclude (e.g., ["**/test_*"])

        Returns:
            Dictionary mapping file paths to their TransformResults
        """
        directory = Path(directory)
        exclude_patterns = exclude_patterns or []
        results = {}

        for file_path in directory.glob(pattern):
            # Skip excluded patterns
            skip = False
            for exclude in exclude_patterns:
                if file_path.match(exclude):
                    skip = True
                    break
            if skip:
                continue

            # Skip non-files
            if not file_path.is_file():
                continue

            try:
                result = self.transform_file(file_path)
                results[str(file_path)] = result
            except Exception as e:
                # Log error but continue with other files
                results[str(file_path)] = TransformResult(
                    original_code="",
                    transformed_code="",
                    changes_made=0,
                    details=[f"Error: {e}"],
                )

        return results
