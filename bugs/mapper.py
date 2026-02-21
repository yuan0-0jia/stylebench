"""
Map mutations from original source to equivalent locations in style variants.

Mutations discovered in the 'original' style variant must be mapped to
identical logical locations in camelcase/snakecase/badnames/formatting
variants so the same bug is tested across all styles.

Key insight: function names change across naming transforms (snake_case ->
camelCase, badnames, etc.) so we match functions by their positional index
within the file (Nth function definition) rather than by name.

Mapping strategies by mutation type:
- Operator mutations: find Nth function in file, count Nth occurrence of operator
- var_swap: map by parameter position index within the Nth function
- if_else_swap: find Nth simple if/else in the Nth function, re-generate swap
"""

from dataclasses import dataclass

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from .injector import MutationSite, MutationType


@dataclass
class MappedMutation:
    """A mutation mapped to a target style variant."""

    file_path: str
    start_byte: int
    end_byte: int
    line_number: int  # 1-indexed
    original_text: str
    mutated_text: str
    context: str
    mutation_type: MutationType


# Operator-based mutation types that use the Nth-occurrence strategy
_OPERATOR_TYPES = {
    MutationType.COMPARISON_EQ_NE,
    MutationType.COMPARISON_LT_GT,
    MutationType.COMPARISON_LE_GE,
    MutationType.BOOLEAN_AND_OR,
    MutationType.MEMBERSHIP_IN,
    MutationType.IDENTITY_IS,
    MutationType.ARITHMETIC_ADD_SUB,
    MutationType.ARITHMETIC_MUL_DIV,
    MutationType.BOOL_TRUE_FALSE,
    MutationType.BOUNDARY_PLUS_ONE,
    MutationType.BOUNDARY_MINUS_ONE,
    MutationType.RETURN_NONE,
}

# Parser singleton
_parser = None


def _get_parser() -> Parser:
    global _parser
    if _parser is None:
        lang = Language(tspython.language())
        _parser = Parser(lang)
    return _parser


def _parse(source: str) -> Node:
    return _get_parser().parse(source.encode()).root_node


# ---------------------------------------------------------------------------
# Function collection (positional matching)
# ---------------------------------------------------------------------------


def _collect_all_functions(root: Node) -> list[Node]:
    """Collect ALL function_definition nodes in DFS/source order.

    This gives a stable positional index that is invariant across naming
    transforms (the Nth function in original corresponds to the Nth
    function in camelcase/snakecase/badnames/formatting).
    """
    results: list[Node] = []

    def visit(node: Node):
        if node.type == "function_definition":
            results.append(node)
        for child in node.children:
            visit(child)

    visit(root)
    results.sort(key=lambda n: n.start_byte)
    return results


def _find_enclosing_function_index(source: str, byte_offset: int) -> int | None:
    """Find the positional index of the function enclosing byte_offset.

    Returns the index into _collect_all_functions() result, or None if
    the byte offset is not inside any function.
    """
    root = _parse(source)
    all_funcs = _collect_all_functions(root)

    # Find the innermost function containing byte_offset
    best_idx = None
    for i, func in enumerate(all_funcs):
        if func.start_byte <= byte_offset < func.end_byte:
            # Prefer the most deeply nested (later in DFS = smaller span)
            best_idx = i

    return best_idx


def _get_function_name(func_node: Node) -> str | None:
    """Get the name identifier from a function_definition node."""
    for child in func_node.children:
        if child.type == "identifier":
            return child.text.decode()
    return None


def _get_function_body(func_node: Node) -> Node | None:
    """Get the block (body) node of a function definition."""
    for child in func_node.children:
        if child.type == "block":
            return child
    return None


def _get_function_params(func_node: Node) -> list[str]:
    """Extract parameter names from a function definition."""
    params = []
    for child in func_node.children:
        if child.type == "parameters":
            for param in child.children:
                if param.type == "identifier":
                    params.append(param.text.decode())
                elif param.type in (
                    "typed_parameter",
                    "default_parameter",
                    "typed_default_parameter",
                ):
                    for p in param.children:
                        if p.type == "identifier":
                            params.append(p.text.decode())
                            break
    return params


def _build_context(source: str, start_byte: int, end_byte: int) -> str:
    """Build a context string around a byte range."""
    ctx_start = max(0, start_byte - 40)
    ctx_end = min(len(source), end_byte + 40)
    context = source[ctx_start:ctx_end].replace("\n", " ").strip()
    if ctx_start > 0:
        context = "..." + context
    if ctx_end < len(source):
        context = context + "..."
    return context


# ---------------------------------------------------------------------------
# Operator occurrence counting
# ---------------------------------------------------------------------------


def _collect_operator_nodes(
    source: str, func_node: Node, op_text: str, mutation_type: MutationType
) -> list[Node]:
    """Collect all AST nodes matching operator text within a function body.

    Returns nodes in source order (by start_byte).
    """
    body = _get_function_body(func_node)
    if not body:
        return []

    nodes: list[Node] = []

    def visit(node: Node):
        if mutation_type == MutationType.RETURN_NONE:
            if node.type == "return_statement" and len(node.children) > 1:
                ret_val = node.children[-1]
                if ret_val.text and ret_val.text.decode() != "None":
                    nodes.append(ret_val)
        elif mutation_type in (MutationType.BOUNDARY_PLUS_ONE, MutationType.BOUNDARY_MINUS_ONE):
            if node.type == "integer":
                try:
                    val = int(node.text.decode())
                    if abs(val) < 1000000 and node.text.decode() == op_text:
                        nodes.append(node)
                except ValueError:
                    pass
        elif mutation_type == MutationType.BOOL_TRUE_FALSE:
            if node.type in ("true", "false") and node.text.decode() == op_text:
                parent = node.parent
                skip = False
                if parent and parent.type == "assignment":
                    for child in parent.children:
                        if child.type == "identifier":
                            name = child.text.decode() if child.text else ""
                            if name.isupper() or name in ("TYPE_CHECKING", "DEBUG", "TESTING"):
                                skip = True
                                break
                if not skip:
                    nodes.append(node)
        else:
            if node.text and node.text.decode() == op_text:
                if _is_operator_context(node, mutation_type):
                    nodes.append(node)

        for child in node.children:
            visit(child)

    visit(body)
    nodes.sort(key=lambda n: n.start_byte)
    return nodes


def _is_operator_context(node: Node, mutation_type: MutationType) -> bool:
    """Check if a node is in the right AST context for the mutation type."""
    parent = node.parent
    if parent is None:
        return False

    if mutation_type in (
        MutationType.COMPARISON_EQ_NE,
        MutationType.COMPARISON_LT_GT,
        MutationType.COMPARISON_LE_GE,
        MutationType.MEMBERSHIP_IN,
        MutationType.IDENTITY_IS,
    ):
        return parent.type == "comparison_operator"
    elif mutation_type == MutationType.BOOLEAN_AND_OR:
        return parent.type == "boolean_operator"
    elif mutation_type in (
        MutationType.ARITHMETIC_ADD_SUB,
        MutationType.ARITHMETIC_MUL_DIV,
    ):
        return parent.type == "binary_operator"
    return True


# ---------------------------------------------------------------------------
# Mapping functions by mutation type
# ---------------------------------------------------------------------------


def _map_operator_mutation(
    original_source: str,
    target_source: str,
    site: MutationSite,
) -> MappedMutation | None:
    """Map an operator-based mutation from original to target.

    Uses positional function index (Nth function in file) and Nth operator
    occurrence within that function.
    """
    # Find which function index this mutation is in
    func_idx = _find_enclosing_function_index(original_source, site.start_byte)
    if func_idx is None:
        return None

    orig_root = _parse(original_source)
    orig_funcs = _collect_all_functions(orig_root)
    orig_func = orig_funcs[func_idx]

    # Count which Nth occurrence of the operator this is
    op_nodes = _collect_operator_nodes(
        original_source, orig_func, site.original_text, site.mutation_type
    )
    n = None
    for i, node in enumerate(op_nodes):
        if node.start_byte == site.start_byte:
            n = i
            break

    # For return_none, the site.start_byte points to the return value,
    # not the return keyword. Match by start_byte of the return value.
    if n is None:
        return None

    # Find the corresponding function in the target by index
    tgt_root = _parse(target_source)
    tgt_funcs = _collect_all_functions(tgt_root)
    if func_idx >= len(tgt_funcs):
        return None

    tgt_func = tgt_funcs[func_idx]

    # Find the Nth occurrence in the target function
    tgt_op_nodes = _collect_operator_nodes(
        target_source, tgt_func, site.original_text, site.mutation_type
    )
    if n >= len(tgt_op_nodes):
        return None

    tgt_node = tgt_op_nodes[n]
    line = tgt_node.start_point[0] + 1

    # For return_none, the return value expression changes across naming
    # transforms (e.g., `suffix` vs `sfx`), so use the target's actual text.
    # Include the "return" keyword to avoid ambiguity when apply_bug() does
    # line-level string replacement (e.g., "ret" is a substring of "return").
    # For operators/literals, the text is invariant (==, True, 42, etc.).
    if site.mutation_type == MutationType.RETURN_NONE:
        orig_text = "return " + tgt_node.text.decode()
        mut_text = "return None"
    else:
        orig_text = site.original_text
        mut_text = site.mutated_text

    return MappedMutation(
        file_path="",  # caller fills this in
        start_byte=tgt_node.start_byte,
        end_byte=tgt_node.end_byte,
        line_number=line,
        original_text=orig_text,
        mutated_text=mut_text,
        context=_build_context(target_source, tgt_node.start_byte, tgt_node.end_byte),
        mutation_type=site.mutation_type,
    )


def _map_var_swap(
    original_source: str,
    target_source: str,
    site: MutationSite,
) -> MappedMutation | None:
    """Map a var_swap mutation by parameter position index."""
    func_idx = _find_enclosing_function_index(original_source, site.start_byte)
    if func_idx is None:
        return None

    orig_root = _parse(original_source)
    orig_funcs = _collect_all_functions(orig_root)
    orig_func = orig_funcs[func_idx]
    orig_params = _get_function_params(orig_func)

    # Determine which param positions are being swapped
    orig_name = site.original_text
    swap_name = site.mutated_text

    if orig_name not in orig_params or swap_name not in orig_params:
        return None

    orig_idx = orig_params.index(orig_name)
    swap_idx = orig_params.index(swap_name)

    # Find corresponding function in target by position
    tgt_root = _parse(target_source)
    tgt_funcs = _collect_all_functions(tgt_root)
    if func_idx >= len(tgt_funcs):
        return None

    tgt_func = tgt_funcs[func_idx]
    tgt_params = _get_function_params(tgt_func)
    if orig_idx >= len(tgt_params) or swap_idx >= len(tgt_params):
        return None

    tgt_orig_name = tgt_params[orig_idx]
    tgt_swap_name = tgt_params[swap_idx]

    # Find which Nth usage of the original param this is in the original
    orig_body = _get_function_body(orig_func)
    if orig_body is None:
        return None

    orig_usages: list[Node] = []
    _find_var_usages(orig_body, orig_name, orig_usages)
    orig_usages.sort(key=lambda n: n.start_byte)

    usage_n = None
    for i, u in enumerate(orig_usages):
        if u.start_byte == site.start_byte:
            usage_n = i
            break
    if usage_n is None:
        return None

    # Find the Nth usage of the corresponding param in the target
    tgt_body = _get_function_body(tgt_func)
    if tgt_body is None:
        return None

    tgt_usages: list[Node] = []
    _find_var_usages(tgt_body, tgt_orig_name, tgt_usages)
    tgt_usages.sort(key=lambda n: n.start_byte)

    if usage_n >= len(tgt_usages):
        return None

    tgt_node = tgt_usages[usage_n]
    line = tgt_node.start_point[0] + 1

    return MappedMutation(
        file_path="",
        start_byte=tgt_node.start_byte,
        end_byte=tgt_node.end_byte,
        line_number=line,
        original_text=tgt_orig_name,
        mutated_text=tgt_swap_name,
        context=_build_context(target_source, tgt_node.start_byte, tgt_node.end_byte),
        mutation_type=MutationType.VARIABLE_SWAP,
    )


def _find_var_usages(node: Node, var_name: str, usages: list[Node]):
    """Find all identifier usages of var_name (excluding definitions)."""
    if node.type == "parameters":
        return
    if node.type == "assignment":
        for i, child in enumerate(node.children):
            if child.type == "identifier" and i == 0:
                continue
            _find_var_usages(child, var_name, usages)
        return
    if node.type == "identifier" and node.text.decode() == var_name:
        usages.append(node)
        return
    for child in node.children:
        _find_var_usages(child, var_name, usages)


def _map_if_else_swap(
    original_source: str,
    target_source: str,
    site: MutationSite,
) -> MappedMutation | None:
    """Map an if_else_swap by finding the Nth simple if/else in the Nth function."""
    func_idx = _find_enclosing_function_index(original_source, site.start_byte)
    if func_idx is None:
        return None

    orig_root = _parse(original_source)
    orig_funcs = _collect_all_functions(orig_root)
    orig_func = orig_funcs[func_idx]

    orig_if_elses = _collect_simple_if_else(orig_func)
    n = None
    for i, ie_node in enumerate(orig_if_elses):
        if ie_node.start_byte == site.start_byte:
            n = i
            break
    if n is None:
        return None

    # Find corresponding function in target by position
    tgt_root = _parse(target_source)
    tgt_funcs = _collect_all_functions(tgt_root)
    if func_idx >= len(tgt_funcs):
        return None

    tgt_func = tgt_funcs[func_idx]
    tgt_if_elses = _collect_simple_if_else(tgt_func)
    if n >= len(tgt_if_elses):
        return None

    tgt_node = tgt_if_elses[n]

    # Re-generate the swap from the target's own code
    if_block = None
    else_clause = None
    for child in tgt_node.children:
        if child.type == "block" and if_block is None:
            if_block = child
        elif child.type == "else_clause":
            else_clause = child

    if if_block is None or else_clause is None:
        return None

    else_block = None
    for child in else_clause.children:
        if child.type == "block":
            else_block = child
            break

    if else_block is None:
        return None

    if_body = target_source[if_block.start_byte : if_block.end_byte]
    else_body = target_source[else_block.start_byte : else_block.end_byte]

    if if_body.strip() == else_body.strip():
        return None

    original_stmt = target_source[tgt_node.start_byte : tgt_node.end_byte]

    if_rel_start = if_block.start_byte - tgt_node.start_byte
    if_rel_end = if_block.end_byte - tgt_node.start_byte
    else_rel_start = else_block.start_byte - tgt_node.start_byte
    else_rel_end = else_block.end_byte - tgt_node.start_byte

    mutated_stmt = (
        original_stmt[:if_rel_start]
        + else_body
        + original_stmt[if_rel_end:else_rel_start]
        + if_body
        + original_stmt[else_rel_end:]
    )

    line = tgt_node.start_point[0] + 1

    return MappedMutation(
        file_path="",
        start_byte=tgt_node.start_byte,
        end_byte=tgt_node.end_byte,
        line_number=line,
        original_text=original_stmt,
        mutated_text=mutated_stmt,
        context=_build_context(target_source, tgt_node.start_byte, tgt_node.end_byte),
        mutation_type=MutationType.IF_ELSE_SWAP,
    )


def _collect_simple_if_else(func_node: Node) -> list[Node]:
    """Collect all simple if/else statements (no elif) within a function, in source order."""
    results: list[Node] = []

    def visit(node: Node):
        if node.type == "if_statement":
            has_elif = False
            if_block = None
            else_clause = None
            for child in node.children:
                if child.type == "block" and if_block is None:
                    if_block = child
                elif child.type == "elif_clause":
                    has_elif = True
                elif child.type == "else_clause":
                    else_clause = child

            if if_block and else_clause and not has_elif:
                results.append(node)

        for child in node.children:
            visit(child)

    body = _get_function_body(func_node)
    if body:
        visit(body)
    results.sort(key=lambda n: n.start_byte)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def map_mutation(
    original_source: str,
    target_source: str,
    site: MutationSite,
    style: str,
) -> MappedMutation | None:
    """Map a mutation from original source to the target style variant.

    Uses positional function matching (Nth function in file) to handle
    function name changes across naming transforms.

    Args:
        original_source: Source code of the original file
        target_source: Source code of the target style variant file
        site: MutationSite discovered in the original
        style: Target style name (for diagnostics, not used in logic)

    Returns:
        MappedMutation if mapping succeeds, None if the mutation cannot be mapped.
    """
    if site.mutation_type in _OPERATOR_TYPES:
        return _map_operator_mutation(original_source, target_source, site)
    elif site.mutation_type == MutationType.VARIABLE_SWAP:
        return _map_var_swap(original_source, target_source, site)
    elif site.mutation_type == MutationType.IF_ELSE_SWAP:
        return _map_if_else_swap(original_source, target_source, site)
    return None
