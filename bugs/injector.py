"""
Bug injector using tree-sitter for Python code mutation.

Implements semantic mutations:
- Comparison operators: < ↔ >, <= ↔ >=, == ↔ !=
- Boolean operators: and ↔ or
- Boundary mutations: +1 ↔ -1 for integer literals
"""

from dataclasses import dataclass
from enum import Enum

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser


class MutationType(Enum):
    """Types of mutations supported."""

    COMPARISON_LT_GT = "lt_gt"  # < ↔ >
    COMPARISON_LE_GE = "le_ge"  # <= ↔ >=
    COMPARISON_EQ_NE = "eq_ne"  # == ↔ !=
    BOOLEAN_AND_OR = "and_or"  # and ↔ or
    BOUNDARY_PLUS_ONE = "plus_one"  # n → n+1
    BOUNDARY_MINUS_ONE = "minus_one"  # n → n-1


@dataclass
class MutationSite:
    """Represents a location in source code that can be mutated."""

    site_id: int
    mutation_type: MutationType
    start_byte: int
    end_byte: int
    start_point: tuple[int, int]  # (row, col)
    end_point: tuple[int, int]
    original_text: str
    mutated_text: str
    context: str  # surrounding code for context

    def __repr__(self) -> str:
        line = self.start_point[0] + 1
        return (
            f"MutationSite({self.site_id}, {self.mutation_type.value}, "
            f"line {line}: '{self.original_text}' → '{self.mutated_text}')"
        )


class Injector:
    """Tree-sitter based mutation injector for Python code."""

    # Mutation mappings
    COMPARISON_SWAPS = {
        "<": ">",
        ">": "<",
        "<=": ">=",
        ">=": "<=",
        "==": "!=",
        "!=": "==",
    }

    BOOLEAN_SWAPS = {
        "and": "or",
        "or": "and",
    }

    def __init__(self):
        """Initialize the parser with Python language."""
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse(self, source_code: str) -> Node:
        """Parse source code and return the AST root."""
        tree = self.parser.parse(source_code.encode())
        return tree.root_node

    def list_mutation_sites(self, source_code: str) -> list[MutationSite]:
        """
        Find all mutable locations in the source code.

        Returns a list of MutationSite objects, each representing
        a potential mutation that can be applied.
        """
        root = self.parse(source_code)
        sites = []
        site_id = 0

        def get_context(start_byte: int, end_byte: int, context_chars: int = 40) -> str:
            """Get surrounding context for a mutation site."""
            ctx_start = max(0, start_byte - context_chars)
            ctx_end = min(len(source_code), end_byte + context_chars)
            context = source_code[ctx_start:ctx_end]
            # Clean up for display
            context = context.replace("\n", " ").strip()
            if ctx_start > 0:
                context = "..." + context
            if ctx_end < len(source_code):
                context = context + "..."
            return context

        def visit(node):
            nonlocal site_id

            # Comparison operators
            if node.type == "comparison_operator":
                for child in node.children:
                    if child.type in self.COMPARISON_SWAPS:
                        original = child.text.decode()
                        mutated = self.COMPARISON_SWAPS[original]

                        # Determine mutation type
                        if original in ("<", ">"):
                            mut_type = MutationType.COMPARISON_LT_GT
                        elif original in ("<=", ">="):
                            mut_type = MutationType.COMPARISON_LE_GE
                        else:
                            mut_type = MutationType.COMPARISON_EQ_NE

                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=mut_type,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text=original,
                                mutated_text=mutated,
                                context=get_context(child.start_byte, child.end_byte),
                            )
                        )
                        site_id += 1

            # Boolean operators (and/or)
            elif node.type == "boolean_operator":
                for child in node.children:
                    if child.type in self.BOOLEAN_SWAPS:
                        original = child.text.decode()
                        mutated = self.BOOLEAN_SWAPS[original]
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.BOOLEAN_AND_OR,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text=original,
                                mutated_text=mutated,
                                context=get_context(child.start_byte, child.end_byte),
                            )
                        )
                        site_id += 1

            # Integer literals for boundary mutations
            elif node.type == "integer":
                try:
                    original = node.text.decode()
                    value = int(original)

                    # Skip 0 and 1 for -1 mutations (would become -1 or 0)
                    # Skip very large numbers
                    if abs(value) < 1000000:
                        # +1 mutation
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.BOUNDARY_PLUS_ONE,
                                start_byte=node.start_byte,
                                end_byte=node.end_byte,
                                start_point=node.start_point,
                                end_point=node.end_point,
                                original_text=original,
                                mutated_text=str(value + 1),
                                context=get_context(node.start_byte, node.end_byte),
                            )
                        )
                        site_id += 1

                        # -1 mutation
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.BOUNDARY_MINUS_ONE,
                                start_byte=node.start_byte,
                                end_byte=node.end_byte,
                                start_point=node.start_point,
                                end_point=node.end_point,
                                original_text=original,
                                mutated_text=str(value - 1),
                                context=get_context(node.start_byte, node.end_byte),
                            )
                        )
                        site_id += 1
                except ValueError:
                    pass  # Skip non-decimal integers (hex, octal, etc.)

            # Recurse into children
            for child in node.children:
                visit(child)

        visit(root)
        return sites

    def apply_mutation(self, source_code: str, site: MutationSite) -> str:
        """
        Apply a mutation to the source code.

        Args:
            source_code: Original source code
            site: MutationSite describing the mutation to apply

        Returns:
            Mutated source code
        """
        # Simple byte-level replacement
        before = source_code[: site.start_byte]
        after = source_code[site.end_byte :]
        return before + site.mutated_text + after

    def apply_mutation_by_id(
        self, source_code: str, site_id: int
    ) -> tuple[str, MutationSite] | None:
        """
        Apply a mutation by site ID.

        Args:
            source_code: Original source code
            site_id: ID of the mutation site

        Returns:
            Tuple of (mutated_code, site) or None if site_id not found
        """
        sites = self.list_mutation_sites(source_code)
        for site in sites:
            if site.site_id == site_id:
                return self.apply_mutation(source_code, site), site
        return None

    def get_mutations_by_type(
        self, source_code: str, mutation_type: MutationType
    ) -> list[MutationSite]:
        """Get all mutation sites of a specific type."""
        sites = self.list_mutation_sites(source_code)
        return [s for s in sites if s.mutation_type == mutation_type]


# Convenience functions for module-level usage
_injector = None


def _get_injector() -> Injector:
    """Get or create the singleton injector instance."""
    global _injector
    if _injector is None:
        _injector = Injector()
    return _injector


def list_mutation_sites(source_code: str) -> list[MutationSite]:
    """Find all mutable locations in source code."""
    return _get_injector().list_mutation_sites(source_code)


def apply_mutation(source_code: str, site: MutationSite) -> str:
    """Apply a mutation to source code."""
    return _get_injector().apply_mutation(source_code, site)


def apply_mutation_by_id(
    source_code: str, site_id: int
) -> tuple[str, MutationSite] | None:
    """Apply a mutation by site ID."""
    return _get_injector().apply_mutation_by_id(source_code, site_id)
