"""
Bug injector using tree-sitter for Python code mutation.

Implements semantic mutations:
- Comparison operators: < ↔ >, <= ↔ >=, == ↔ !=
- Boolean operators: and ↔ or
- Boundary mutations: +1 ↔ -1 for integer literals
- Boolean literals: True ↔ False
- Membership operators: in ↔ not in
- Identity operators: is ↔ is not
- Arithmetic operators: + ↔ -, * ↔ /
- Return mutations: return x → return None
- Variable swap: swap a variable with another in the same scope
- If/else swap: swap if and else branch bodies
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
    BOOL_TRUE_FALSE = "true_false"  # True ↔ False
    MEMBERSHIP_IN = "in_not_in"  # in ↔ not in
    IDENTITY_IS = "is_is_not"  # is ↔ is not
    ARITHMETIC_ADD_SUB = "add_sub"  # + ↔ -
    ARITHMETIC_MUL_DIV = "mul_div"  # * ↔ /
    RETURN_NONE = "return_none"  # return x → return None
    VARIABLE_SWAP = "var_swap"  # swap variable with another in scope
    IF_ELSE_SWAP = "if_else_swap"  # swap if/else branch bodies


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

    BOOL_LITERAL_SWAPS = {
        "True": "False",
        "False": "True",
    }

    ARITHMETIC_ADD_SUB_SWAPS = {
        "+": "-",
        "-": "+",
    }

    ARITHMETIC_MUL_DIV_SWAPS = {
        "*": "/",
        "/": "*",
    }

    def __init__(self):
        """Initialize the parser with Python language."""
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)

    def parse(self, source_code: str) -> Node:
        """Parse source code and return the AST root."""
        tree = self.parser.parse(source_code.encode())
        return tree.root_node

    def _get_function_params(self, func_node: Node) -> list[str]:
        """Extract parameter names from a function definition."""
        params = []
        for child in func_node.children:
            if child.type == "parameters":
                for param in child.children:
                    if param.type == "identifier":
                        params.append(param.text.decode())
                    elif param.type == "typed_parameter":
                        # Get the identifier from typed parameter
                        for p in param.children:
                            if p.type == "identifier":
                                params.append(p.text.decode())
                                break
                    elif param.type == "default_parameter":
                        # Get the identifier from default parameter
                        for p in param.children:
                            if p.type == "identifier":
                                params.append(p.text.decode())
                                break
                    elif param.type == "typed_default_parameter":
                        # Get the identifier from typed default parameter
                        for p in param.children:
                            if p.type == "identifier":
                                params.append(p.text.decode())
                                break
        return params

    def _find_variable_usages(self, node: Node, var_names: set[str], usages: list):
        """Find all usages of variables within a node (excluding definitions)."""
        # Skip the parameters themselves and assignment targets
        if node.type == "parameters":
            return
        if node.type == "assignment":
            # Skip the left side (assignment target), only process right side
            for i, child in enumerate(node.children):
                if child.type == "identifier" and i == 0:
                    continue  # Skip assignment target
                self._find_variable_usages(child, var_names, usages)
            return

        if node.type == "identifier":
            name = node.text.decode()
            if name in var_names:
                usages.append(node)
            return

        for child in node.children:
            self._find_variable_usages(child, var_names, usages)

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

            # Comparison operators (including membership and identity)
            if node.type == "comparison_operator":
                for i, child in enumerate(node.children):
                    # Basic comparison swaps: <, >, <=, >=, ==, !=
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

                    # Membership: "in" → "not in"
                    elif child.type == "in":
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.MEMBERSHIP_IN,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text="in",
                                mutated_text="not in",
                                context=get_context(child.start_byte, child.end_byte),
                            )
                        )
                        site_id += 1

                    # Membership: "not in" → "in" (parsed as single node type)
                    elif child.type == "not in":
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.MEMBERSHIP_IN,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text="not in",
                                mutated_text="in",
                                context=get_context(child.start_byte, child.end_byte),
                            )
                        )
                        site_id += 1

                    # Identity: "is" → "is not"
                    elif child.type == "is":
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.IDENTITY_IS,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text="is",
                                mutated_text="is not",
                                context=get_context(child.start_byte, child.end_byte),
                            )
                        )
                        site_id += 1

                    # Identity: "is not" → "is" (parsed as single node type)
                    elif child.type == "is not":
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.IDENTITY_IS,
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                start_point=child.start_point,
                                end_point=child.end_point,
                                original_text="is not",
                                mutated_text="is",
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

            # Boolean literals (True/False)
            elif node.type in ("true", "false"):
                original = node.text.decode()
                mutated = self.BOOL_LITERAL_SWAPS.get(original)
                if mutated:
                    sites.append(
                        MutationSite(
                            site_id=site_id,
                            mutation_type=MutationType.BOOL_TRUE_FALSE,
                            start_byte=node.start_byte,
                            end_byte=node.end_byte,
                            start_point=node.start_point,
                            end_point=node.end_point,
                            original_text=original,
                            mutated_text=mutated,
                            context=get_context(node.start_byte, node.end_byte),
                        )
                    )
                    site_id += 1

            # Binary operators: + ↔ -, * ↔ /
            elif node.type == "binary_operator":
                for child in node.children:
                    original = child.text.decode() if child.text else ""

                    # Addition/subtraction swap
                    if original in self.ARITHMETIC_ADD_SUB_SWAPS:
                        mutated = self.ARITHMETIC_ADD_SUB_SWAPS[original]
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.ARITHMETIC_ADD_SUB,
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

                    # Multiplication/division swap
                    elif original in self.ARITHMETIC_MUL_DIV_SWAPS:
                        mutated = self.ARITHMETIC_MUL_DIV_SWAPS[original]
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.ARITHMETIC_MUL_DIV,
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

            # Return statements: return x → return None
            elif node.type == "return_statement":
                # Only mutate if there's a return value (not bare "return")
                if len(node.children) > 1:  # "return" keyword + value
                    return_value = node.children[-1]
                    # Skip if already returning None
                    if return_value.text and return_value.text.decode() != "None":
                        sites.append(
                            MutationSite(
                                site_id=site_id,
                                mutation_type=MutationType.RETURN_NONE,
                                start_byte=return_value.start_byte,
                                end_byte=return_value.end_byte,
                                start_point=return_value.start_point,
                                end_point=return_value.end_point,
                                original_text=return_value.text.decode(),
                                mutated_text="None",
                                context=get_context(node.start_byte, node.end_byte),
                            )
                        )
                        site_id += 1

            # If/else swap: swap bodies of if and else branches
            elif node.type == "if_statement":
                # Find the if block and else clause
                if_block = None
                else_clause = None
                has_elif = False

                for child in node.children:
                    if child.type == "block" and if_block is None:
                        if_block = child
                    elif child.type == "elif_clause":
                        has_elif = True
                    elif child.type == "else_clause":
                        else_clause = child

                # Only handle simple if/else (no elif) to keep mutations clean
                if if_block and else_clause and not has_elif:
                    # Find the else block
                    else_block = None
                    for child in else_clause.children:
                        if child.type == "block":
                            else_block = child
                            break

                    if else_block:
                        # Get the block contents (without leading/trailing whitespace issues)
                        if_body = source_code[if_block.start_byte : if_block.end_byte]
                        else_body = source_code[else_block.start_byte : else_block.end_byte]

                        # Only create mutation if bodies are different
                        if if_body.strip() != else_body.strip():
                            # Build the swapped version:
                            # Keep everything before if_block, put else_body,
                            # keep middle (else keyword etc), put if_body
                            original_stmt = source_code[node.start_byte : node.end_byte]

                            # Calculate relative positions within the if statement
                            if_block_rel_start = if_block.start_byte - node.start_byte
                            if_block_rel_end = if_block.end_byte - node.start_byte
                            else_block_rel_start = else_block.start_byte - node.start_byte
                            else_block_rel_end = else_block.end_byte - node.start_byte

                            # Build mutated statement by swapping block contents
                            mutated_stmt = (
                                original_stmt[:if_block_rel_start]
                                + else_body
                                + original_stmt[if_block_rel_end:else_block_rel_start]
                                + if_body
                                + original_stmt[else_block_rel_end:]
                            )

                            sites.append(
                                MutationSite(
                                    site_id=site_id,
                                    mutation_type=MutationType.IF_ELSE_SWAP,
                                    start_byte=node.start_byte,
                                    end_byte=node.end_byte,
                                    start_point=node.start_point,
                                    end_point=node.end_point,
                                    original_text=original_stmt,
                                    mutated_text=mutated_stmt,
                                    context=get_context(node.start_byte, node.end_byte),
                                )
                            )
                            site_id += 1

            # Variable swap mutations for function parameters
            elif node.type == "function_definition":
                params = self._get_function_params(node)
                # Only create swap mutations if there are 2+ parameters
                if len(params) >= 2:
                    # Find the function body
                    body = None
                    for child in node.children:
                        if child.type == "block":
                            body = child
                            break

                    if body:
                        # Find all usages of parameters in the body
                        usages = []
                        self._find_variable_usages(body, set(params), usages)

                        # For each usage, create swap mutations with other params
                        for usage in usages:
                            original_name = usage.text.decode()
                            for other_param in params:
                                if other_param != original_name:
                                    sites.append(
                                        MutationSite(
                                            site_id=site_id,
                                            mutation_type=MutationType.VARIABLE_SWAP,
                                            start_byte=usage.start_byte,
                                            end_byte=usage.end_byte,
                                            start_point=usage.start_point,
                                            end_point=usage.end_point,
                                            original_text=original_name,
                                            mutated_text=other_param,
                                            context=get_context(usage.start_byte, usage.end_byte),
                                        )
                                    )
                                    site_id += 1

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
