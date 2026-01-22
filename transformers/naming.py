"""
Naming convention transformers.

Supports:
- snake_case ↔ camelCase conversion
- Bad naming (single-letter variables)

Uses AST analysis to:
- Auto-detect imported names (don't transform external APIs)
- Track attribute accesses (don't transform module.method patterns)
- Only transform locally-defined names (whitelist approach)
- Sync pytest.mark.parametrize string arguments
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from .base import Transformer, TransformResult

# Python built-in names that should never be transformed
PYTHON_BUILTINS = {
    # Built-in functions
    "abs", "aiter", "all", "any", "anext", "ascii", "bin", "bool", "breakpoint",
    "bytearray", "bytes", "callable", "chr", "classmethod", "compile", "complex",
    "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter",
    "float", "format", "frozenset", "getattr", "globals", "hasattr", "hash",
    "help", "hex", "id", "input", "int", "isinstance", "issubclass", "iter",
    "len", "list", "locals", "map", "max", "memoryview", "min", "next", "object",
    "oct", "open", "ord", "pow", "print", "property", "range", "repr", "reversed",
    "round", "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum",
    "super", "tuple", "type", "vars", "zip",
    # Built-in constants
    "True", "False", "None", "Ellipsis", "NotImplemented",
    # Built-in exceptions (common ones)
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "ImportError", "RuntimeError", "StopIteration",
    # Special names
    "self", "cls", "_",
    # pytest special method names (fixtures)
    "setup_method", "teardown_method", "setup_class", "teardown_class",
    "setup_module", "teardown_module", "setup_function", "teardown_function",
    "setup", "teardown",
    # HTMLParser methods (stdlib html.parser) - all public methods that might be overridden
    "handle_starttag", "handle_endtag", "handle_startendtag", "handle_data",
    "handle_comment", "handle_decl", "handle_pi", "handle_charref", "handle_entityref",
    "parse_starttag", "parse_endtag", "parse_bogus_comment", "parse_comment",
    "parse_declaration", "parse_html_declaration", "parse_marked_section", "parse_pi",
    "check_for_whole_start_tag", "clear_cdata_mode", "set_cdata_mode",
    "get_starttag_text", "getpos", "goahead", "reset", "unknown_decl", "updatepos",
    # xml.etree.ElementTree/xml.sax callback methods
    "start_element", "end_element", "char_data",
    # unittest callback methods
    "set_up", "tear_down", "set_up_class", "tear_down_class",
    # asyncio callback methods
    "connection_made", "connection_lost", "data_received", "eof_received",
}


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    if not name or "_" not in name:
        return name

    # Handle leading underscores (private/protected)
    leading_underscores = ""
    while name.startswith("_"):
        leading_underscores += "_"
        name = name[1:]

    if not name:
        return leading_underscores

    # Split and convert
    parts = name.split("_")
    # First part stays lowercase, rest are capitalized
    result = parts[0].lower() + "".join(word.capitalize() for word in parts[1:] if word)

    return leading_underscores + result


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    if not name:
        return name

    # Handle leading underscores
    leading_underscores = ""
    while name.startswith("_"):
        leading_underscores += "_"
        name = name[1:]

    if not name:
        return leading_underscores

    # Insert underscore before uppercase letters
    result = re.sub(r"([A-Z])", r"_\1", name).lower()

    # Remove leading underscore if one was added
    if result.startswith("_"):
        result = result[1:]

    return leading_underscores + result


@dataclass
class CodeContext:
    """Tracks context about names in a source file."""

    # Names imported from external modules (should NOT be transformed)
    imported_names: set[str] = field(default_factory=set)

    # Module names/aliases (for attribute access detection)
    module_names: set[str] = field(default_factory=set)

    # Names defined locally (SHOULD be transformed)
    local_definitions: set[str] = field(default_factory=set)

    # Positions of attribute accesses (should NOT be transformed)
    # Maps (start_byte, end_byte) -> True for identifiers that are attributes
    attribute_positions: set[tuple[int, int]] = field(default_factory=set)

    # Parametrize decorator string positions
    # List of (start_byte, end_byte, original_params_string)
    parametrize_strings: list[tuple[int, int, str]] = field(default_factory=list)

    # __all__ string positions (names that should be transformed in string form)
    # List of (start_byte, end_byte, original_string)
    all_export_strings: list[tuple[int, int, str]] = field(default_factory=list)

    # Project package names (imports from these are same-project)
    project_packages: set[str] = field(default_factory=set)

    # Names that are submodules of project packages (e.g., 'time' from 'from humanize import time')
    project_module_aliases: set[str] = field(default_factory=set)

    # Positions of identifiers that are part of module paths (should NOT be transformed)
    # These are the module names in import statements like 'from .crypto_addresses import X'
    module_path_positions: set[tuple[int, int]] = field(default_factory=set)

    # Positions of keyword argument names in calls to external functions
    # (should NOT be transformed). E.g., 'strict_parsing' in parse_qs(strict_parsing=True)
    external_kwarg_positions: set[tuple[int, int]] = field(default_factory=set)

    # Format string placeholders that should be transformed
    # List of (start_byte, end_byte, placeholder_name) for {name} patterns in .format() calls
    format_string_placeholders: list[tuple[int, int, str]] = field(default_factory=list)

    # Dict key strings that should be transformed (e.g., __dict__["name"], getattr(obj, "name"))
    # List of (start_byte, end_byte, key_name)
    dict_key_strings: list[tuple[int, int, str]] = field(default_factory=list)

    # Variables that hold external objects (assigned from external function calls)
    # Attribute access on these should NOT be transformed
    external_object_vars: set[str] = field(default_factory=set)

    # Functions/methods that use **kwargs - callers' kwargs should NOT be transformed
    # because these functions typically access kwargs via string keys like kwargs['key']
    kwargs_functions: set[str] = field(default_factory=set)

    # Attribute positions on external modules (e.g., importlib.util.find_spec)
    # These should NEVER be transformed, even if the name is in project_definitions
    external_module_attribute_positions: set[tuple[int, int]] = field(default_factory=set)

    # Attribute positions on self/cls (e.g., self.attr_name)
    # These require the name to be a project definition to transform (avoid inherited attrs)
    self_attribute_positions: set[tuple[int, int]] = field(default_factory=set)

    # Function parameter positions - these should NOT be transformed
    # to maintain consistency with kwargs and config string keys
    parameter_positions: set[tuple[int, int]] = field(default_factory=set)


class NameAnalyzer:
    """Analyzes source code to extract naming context."""

    def __init__(self, source_bytes: bytes, project_packages: set[str] | None = None):
        self.source_bytes = source_bytes
        self.project_packages = project_packages or set()

    def _get_text(self, node: Node) -> str:
        """Get text for a node using byte positions."""
        return self.source_bytes[node.start_byte:node.end_byte].decode('utf-8')

    def analyze(self, root: Node) -> CodeContext:
        """Analyze the AST and return context about names."""
        ctx = CodeContext()
        ctx.project_packages = self.project_packages
        self._collect_imports(root, ctx)
        self._collect_local_definitions(root, ctx)
        self._collect_kwargs_functions(root, ctx)  # Before _collect_external_kwargs
        self._collect_external_object_vars(root, ctx)  # Before _collect_attribute_accesses
        self._collect_attribute_accesses(root, ctx)
        self._collect_external_kwargs(root, ctx)
        self._collect_format_strings(root, ctx)
        self._collect_dict_key_strings(root, ctx)
        self._collect_parametrize_strings(root, ctx)
        self._collect_all_export_strings(root, ctx)
        return ctx

    def _collect_imports(self, root: Node, ctx: CodeContext):
        """Collect imported module names.

        For direct imports (import X), adds X to both module_names and imported_names.
        For from imports (from X import Y):
          - If X is a project package, don't add Y (allow transformation)
          - If X is external, add Y to imported_names (prevent transformation)
        """

        def visit(node: Node):
            # import module
            # import module as alias
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        # Get the first part (module name)
                        name = self._get_text(child).split(".")[0]
                        ctx.module_names.add(name)
                        ctx.imported_names.add(name)
                    elif child.type == "aliased_import":
                        # import module as alias
                        for subchild in child.children:
                            if subchild.type == "dotted_name":
                                name = self._get_text(subchild).split(".")[0]
                                ctx.imported_names.add(name)
                            elif subchild.type == "identifier":
                                # This is the alias
                                alias = self._get_text(subchild)
                                ctx.module_names.add(alias)
                                ctx.imported_names.add(alias)

            # from module import name
            # from module import name as alias
            elif node.type == "import_from_statement":
                module_name = None
                is_relative_import = False
                imported_identifiers = []
                found_module = False

                for child in node.children:
                    if child.type == "relative_import":
                        # Relative import like "from .foo import X"
                        is_relative_import = True
                        module_name = self._get_text(child)
                        found_module = True
                        # Mark all identifiers in the relative import as module path
                        self._mark_module_path_identifiers(child, ctx)
                    elif child.type == "dotted_name":
                        name = self._get_text(child)
                        if not found_module:
                            # First dotted_name is the module being imported from
                            module_name = name
                            ctx.module_names.add(module_name.split(".")[0])
                            found_module = True
                            # Mark all identifiers in this module path as non-transformable
                            self._mark_module_path_identifiers(child, ctx)
                        else:
                            # Subsequent dotted_names are imported names
                            imported_identifiers.append(name)
                    elif child.type == "identifier":
                        # Single identifier import
                        if found_module:
                            imported_identifiers.append(self._get_text(child))
                    elif child.type == "aliased_import":
                        for subchild in child.children:
                            if subchild.type == "dotted_name":
                                imported_identifiers.append(self._get_text(subchild))
                            elif subchild.type == "identifier":
                                # The alias - also track it
                                imported_identifiers.append(self._get_text(subchild))

                # Check if this is an external import
                if module_name:
                    # Relative imports are always same-project
                    if is_relative_import:
                        is_same_project = True
                    else:
                        root_module = module_name.split(".")[0]
                        is_same_project = root_module in ctx.project_packages

                    if is_same_project:
                        # Track imported names as project module aliases
                        # (e.g., 'time' from 'from humanize import time')
                        for name in imported_identifiers:
                            ctx.project_module_aliases.add(name)
                    else:
                        # External import - protect imported names from transformation
                        for name in imported_identifiers:
                            ctx.imported_names.add(name)

            for child in node.children:
                visit(child)

        visit(root)

    def _mark_module_path_identifiers(self, node: Node, ctx: CodeContext):
        """Mark all identifier positions within a module path as non-transformable."""
        if node.type == "identifier":
            ctx.module_path_positions.add((node.start_byte, node.end_byte))
        for child in node.children:
            self._mark_module_path_identifiers(child, ctx)

    def _collect_local_definitions(self, root: Node, ctx: CodeContext):
        """Collect locally defined names (functions, classes, variables)."""

        def visit(node: Node):
            # Function definitions
            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    ctx.local_definitions.add(self._get_text(name_node))

                # Also collect parameters
                params_node = node.child_by_field_name("parameters")
                if params_node:
                    self._collect_parameters(params_node, ctx)

            # Class definitions
            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    ctx.local_definitions.add(self._get_text(name_node))

            # Variable assignments
            elif node.type == "assignment":
                left = node.child_by_field_name("left")
                if left:
                    self._collect_assignment_targets(left, ctx)

            # Augmented assignments (+=, -=, etc.)
            elif node.type == "augmented_assignment":
                left = node.child_by_field_name("left")
                if left and left.type == "identifier":
                    ctx.local_definitions.add(self._get_text(left))

            # For loop variables
            elif node.type == "for_statement":
                left = node.child_by_field_name("left")
                if left:
                    self._collect_assignment_targets(left, ctx)

            # With statement variables
            elif node.type == "with_clause":
                for child in node.children:
                    if child.type == "with_item":
                        for subchild in child.children:
                            if subchild.type == "as_pattern":
                                for item in subchild.children:
                                    if item.type == "identifier":
                                        ctx.local_definitions.add(self._get_text(item))

            # Exception handler variables
            elif node.type == "except_clause":
                for child in node.children:
                    if child.type == "identifier":
                        ctx.local_definitions.add(self._get_text(child))

            # Named expressions (walrus operator)
            elif node.type == "named_expression":
                name = node.child_by_field_name("name")
                if name:
                    ctx.local_definitions.add(self._get_text(name))

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_kwargs_functions(self, root: Node, ctx: CodeContext):
        """Find functions/methods that have **kwargs in their signature.

        Calls to these functions should NOT have their kwargs transformed,
        because these functions typically access kwargs via string keys
        like kwargs['key'] or kwargs.get('key').

        Also tracks class names whose __init__ has **kwargs.
        """
        current_class = None

        def visit(node: Node):
            nonlocal current_class

            if node.type == "class_definition":
                # Track current class name
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = self._get_text(name_node)
                    old_class = current_class
                    current_class = class_name

                    # Visit children to find __init__
                    for child in node.children:
                        visit(child)

                    current_class = old_class
                    return  # Don't visit children again

            if node.type == "function_definition":
                # Get function name
                name_node = node.child_by_field_name("name")
                if name_node:
                    func_name = self._get_text(name_node)

                    # Check if it has **kwargs parameter
                    params = node.child_by_field_name("parameters")
                    if params:
                        for child in params.children:
                            if child.type == "dictionary_splat_pattern":
                                # This function has **kwargs
                                ctx.kwargs_functions.add(func_name)

                                # If this is __init__, also add the class name
                                if func_name == "__init__" and current_class:
                                    ctx.kwargs_functions.add(current_class)
                                break

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_parameters(self, params_node: Node, ctx: CodeContext):
        """Collect parameter names from a parameters node."""
        for child in params_node.children:
            if child.type == "identifier":
                ctx.local_definitions.add(self._get_text(child))
            elif child.type in ("default_parameter", "typed_parameter",
                               "typed_default_parameter", "list_splat_pattern",
                               "dictionary_splat_pattern"):
                for subchild in child.children:
                    if subchild.type == "identifier":
                        ctx.local_definitions.add(self._get_text(subchild))
                        break

    def _collect_assignment_targets(self, node: Node, ctx: CodeContext):
        """Collect names from assignment targets (handles unpacking)."""
        if node.type == "identifier":
            ctx.local_definitions.add(self._get_text(node))
        elif node.type == "attribute":
            # Handle self.x and cls.x attribute assignments
            obj = node.child_by_field_name("object")
            attr = node.child_by_field_name("attribute")
            if obj and attr:
                obj_text = self._get_text(obj)
                if obj_text in ("self", "cls"):
                    ctx.local_definitions.add(self._get_text(attr))
        elif node.type in ("tuple_pattern", "list_pattern", "pattern_list", "tuple", "list"):
            for child in node.children:
                self._collect_assignment_targets(child, ctx)

    def _collect_attribute_accesses(self, root: Node, ctx: CodeContext):
        """Mark positions of attribute accesses that should NOT be transformed.

        Allows transformation for:
        - self.x and cls.x (class attributes)
        - project_package.x (accessing functions on project modules)
        - project_package.submodule.x (chained access starting with project)

        All other attribute accesses are marked as non-transformable.
        """

        def get_root_name(node: Node) -> str:
            """Get the root object name from a potentially chained attribute access."""
            while node.type == "attribute":
                obj = node.child_by_field_name("object")
                if obj:
                    node = obj
                else:
                    break
            return self._get_text(node)

        def is_external_module_root(node: Node) -> bool:
            """Check if the root of an attribute chain is an external module."""
            root_name = get_root_name(node)
            return root_name in ctx.imported_names or root_name in ctx.module_names

        def is_accessed_as_object(node: Node) -> bool:
            """Check if this node is the object of another attribute access.

            If `a.b.c` and we're checking `b`, this returns True because `b` is
            accessed as an object (for `.c`). This indicates `b` is likely a
            submodule, not a function.
            """
            parent = node.parent
            if parent and parent.type == "attribute":
                obj = parent.child_by_field_name("object")
                if obj == node:
                    return True
            return False

        def visit(node: Node):
            # Attribute access: obj.attr
            if node.type == "attribute":
                obj_node = node.child_by_field_name("object")
                attr_node = node.child_by_field_name("attribute")

                if obj_node and attr_node:
                    obj_text = self._get_text(obj_node)

                    # Track self.x and cls.x for special handling (inherited attrs check)
                    if obj_text in ("self", "cls"):
                        ctx.self_attribute_positions.add((attr_node.start_byte, attr_node.end_byte))
                        # Don't mark in attribute_positions - allow transformation
                    # Allow transformation for project package attribute access
                    elif obj_text in ctx.project_packages:
                        # If accessed as object (e.g., markdown.extensions.submod),
                        # it's a submodule that must match filesystem - don't transform
                        if is_accessed_as_object(node):
                            pos = (attr_node.start_byte, attr_node.end_byte)
                            ctx.attribute_positions.add(pos)
                        # Otherwise, allow transformation (e.g., markdown.some_func())
                    # Allow for project module aliases (from humanize import time)
                    elif obj_text in ctx.project_module_aliases:
                        # Same submodule check
                        if is_accessed_as_object(node):
                            pos = (attr_node.start_byte, attr_node.end_byte)
                            ctx.attribute_positions.add(pos)
                    # Mark external object attribute access as non-transformable
                    # (e.g., options.output_format where options = parser.parse_args())
                    elif obj_text in ctx.external_object_vars:
                        pos = (attr_node.start_byte, attr_node.end_byte)
                        ctx.attribute_positions.add(pos)
                        # Also mark as external module access (never transform)
                        ctx.external_module_attribute_positions.add(pos)
                    # For chained access, check if root is a project package or alias
                    elif obj_node.type == "attribute":
                        root_name = get_root_name(obj_node)
                        is_project = (root_name in ctx.project_packages
                                      or root_name in ctx.project_module_aliases)
                        if is_project:
                            # If accessed as object, it's likely a submodule
                            if is_accessed_as_object(node):
                                pos = (attr_node.start_byte, attr_node.end_byte)
                                ctx.attribute_positions.add(pos)
                            # Otherwise allow transformation
                        else:
                            pos = (attr_node.start_byte, attr_node.end_byte)
                            ctx.attribute_positions.add(pos)
                            # If external module or object, mark as never transform
                            is_external = (root_name in ctx.imported_names
                                           or root_name in ctx.module_names
                                           or root_name in ctx.external_object_vars)
                            if is_external:
                                ctx.external_module_attribute_positions.add(pos)
                    # Direct external module attribute access (e.g., importlib.util)
                    elif obj_text in ctx.imported_names or obj_text in ctx.module_names:
                        pos = (attr_node.start_byte, attr_node.end_byte)
                        ctx.attribute_positions.add(pos)
                        ctx.external_module_attribute_positions.add(pos)
                    # All other attribute accesses are non-transformable
                    else:
                        ctx.attribute_positions.add((attr_node.start_byte, attr_node.end_byte))

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_external_kwargs(self, root: Node, ctx: CodeContext):
        """Mark keyword argument positions in calls to external functions or **kwargs functions.

        When calling an external function like parse_qs(query, strict_parsing=True),
        the keyword argument name 'strict_parsing' should NOT be transformed since
        it's part of the external API.

        Similarly, when calling a function that uses **kwargs (like Extension.__init__),
        the kwargs should NOT be transformed because the function accesses them via
        string keys like kwargs['key'].
        """

        def get_root_name(node: Node) -> str:
            """Get the root name from a potentially chained attribute access."""
            while node.type == "attribute":
                obj = node.child_by_field_name("object")
                if obj:
                    node = obj
                else:
                    break
            return self._get_text(node)

        def get_function_name(call_node: Node) -> str | None:
            """Get the function/method name from a call node."""
            func = call_node.child_by_field_name("function")
            if not func:
                return None

            if func.type == "identifier":
                return self._get_text(func)
            elif func.type == "attribute":
                # Get the method name (e.g., 'join' from os.path.join)
                attr = func.child_by_field_name("attribute")
                if attr:
                    return self._get_text(attr)
            return None

        def is_external_function(call_node: Node) -> bool:
            """Check if this is a call to an external function."""
            func = call_node.child_by_field_name("function")
            if not func:
                return False

            if func.type == "identifier":
                # Direct call like parse_qs(...)
                name = self._get_text(func)
                # It's external if it's in imported_names and NOT in local definitions
                return name in ctx.imported_names and name not in ctx.local_definitions
            elif func.type == "attribute":
                # Module.function call like urllib.parse.parse_qs(...)
                obj = func.child_by_field_name("object")
                if obj:
                    root_name = get_root_name(obj)
                    # If it's a project package, it's NOT external
                    if root_name in ctx.project_packages:
                        return False
                    # If it's a project module alias, it's NOT external
                    if root_name in ctx.project_module_aliases:
                        return False
                    # If the root is an external module, it's external
                    if root_name in ctx.module_names or root_name in ctx.imported_names:
                        return True
                    # If root is not self/cls and not local, likely external
                    if root_name not in ("self", "cls") and root_name not in ctx.local_definitions:
                        return True
            return False

        def is_kwargs_function(call_node: Node) -> bool:
            """Check if this is a call to a function that uses **kwargs."""
            func_name = get_function_name(call_node)
            if func_name and func_name in ctx.kwargs_functions:
                return True
            # Also check for super().__init__() which often passes to **kwargs parent
            func = call_node.child_by_field_name("function")
            if func and func.type == "attribute":
                attr = func.child_by_field_name("attribute")
                if attr and self._get_text(attr) == "__init__":
                    # Could be super().__init__() or ClassName.__init__()
                    # Be conservative and mark as kwargs function
                    return True
            return False

        def visit(node: Node):
            if node.type == "call":
                if is_external_function(node) or is_kwargs_function(node):
                    # Find all keyword arguments in this call
                    args = node.child_by_field_name("arguments")
                    if args:
                        for child in args.children:
                            if child.type == "keyword_argument":
                                # Get the name (key) of the keyword argument
                                name_node = child.child_by_field_name("name")
                                if name_node and name_node.type == "identifier":
                                    ctx.external_kwarg_positions.add(
                                        (name_node.start_byte, name_node.end_byte)
                                    )

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_format_strings(self, root: Node, ctx: CodeContext):
        """Find .format() calls and track placeholders that match keyword arguments.

        When we have: "Hello {user_name}".format(user_name=x)
        And user_name gets transformed to userName, we need to also transform
        the {user_name} placeholder in the string.
        """

        def find_format_string(call_node: Node) -> Node | None:
            """Find the string being formatted in a .format() call."""
            func = call_node.child_by_field_name("function")
            if not func or func.type != "attribute":
                return None

            attr = func.child_by_field_name("attribute")
            if not attr or self._get_text(attr) != "format":
                return None

            # Get the object being formatted
            obj = func.child_by_field_name("object")
            if not obj:
                return None

            # Handle direct string or parenthesized string
            if obj.type == "string":
                return obj
            elif obj.type == "parenthesized_expression":
                # Check if it contains a string
                for child in obj.children:
                    if child.type == "string":
                        return child
            elif obj.type == "concatenated_string":
                # Return the whole concatenated string
                return obj

            return None

        def get_format_kwargs(call_node: Node) -> set[str]:
            """Extract keyword argument names from a .format() call."""
            kwargs = set()
            args = call_node.child_by_field_name("arguments")
            if args:
                for child in args.children:
                    if child.type == "keyword_argument":
                        name_node = child.child_by_field_name("name")
                        if name_node and name_node.type == "identifier":
                            kwargs.add(self._get_text(name_node))
            return kwargs

        def find_placeholders_in_string(string_node: Node, kwargs: set[str], ctx: CodeContext):
            """Find {placeholder} patterns in a string that match kwargs."""
            text = self._get_text(string_node)
            string_start = string_node.start_byte

            # Handle different string types (regular, raw, f-string parts)
            # Find all {name} patterns (simple placeholders, not {name:format} for now)
            import re
            # Match {identifier} but not {{escaped}} or {expr:format}
            # We look for {word} where word is a valid Python identifier
            pattern = r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}'

            for match in re.finditer(pattern, text):
                placeholder_name = match.group(1)
                if placeholder_name in kwargs:
                    # Calculate byte position within the source
                    # match.start() gives position in the string text
                    # We need to find the actual byte position of the placeholder name
                    # The {name} starts at match.start(), name starts at match.start()+1
                    placeholder_start = string_start + match.start(1)
                    placeholder_end = string_start + match.end(1)

                    # Verify the positions are correct by checking the source
                    try:
                        src_slice = self.source_bytes[placeholder_start:placeholder_end]
                        actual = src_slice.decode('utf-8')
                        if actual == placeholder_name:
                            ctx.format_string_placeholders.append(
                                (placeholder_start, placeholder_end, placeholder_name)
                            )
                    except (UnicodeDecodeError, IndexError):
                        pass  # Position calculation was off, skip

        def visit(node: Node):
            if node.type == "call":
                string_node = find_format_string(node)
                if string_node:
                    kwargs = get_format_kwargs(node)
                    if kwargs:
                        if string_node.type == "concatenated_string":
                            # Handle each part of concatenated string
                            for child in string_node.children:
                                if child.type == "string":
                                    find_placeholders_in_string(child, kwargs, ctx)
                        else:
                            find_placeholders_in_string(string_node, kwargs, ctx)

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_dict_key_strings(self, root: Node, ctx: CodeContext):
        """Find string literals used as dict keys for attribute access.

        Patterns:
        - obj.__dict__["name"]
        - getattr(obj, "name")
        - setattr(obj, "name", value)
        - hasattr(obj, "name")
        """

        def extract_string_value(string_node: Node) -> tuple[int, int, str] | None:
            """Extract the inner value and positions from a string node."""
            text = self._get_text(string_node)
            if len(text) >= 2 and text[0] in ('"', "'") and text[-1] == text[0]:
                inner = text[1:-1]
                if inner.isidentifier():
                    # Position of just the inner content (excluding quotes)
                    inner_start = string_node.start_byte + 1
                    inner_end = string_node.end_byte - 1
                    return (inner_start, inner_end, inner)
            return None

        def visit(node: Node):
            # Pattern 1: obj.__dict__["name"] - only transform __dict__ subscripts
            # NOTE: We don't transform general subscript keys like config["base_url"]
            # because they might be accessing external dicts or kwargs
            if node.type == "subscript":
                obj = node.child_by_field_name("value")
                subscript = node.child_by_field_name("subscript")

                if obj and obj.type == "attribute":
                    attr = obj.child_by_field_name("attribute")
                    if attr and self._get_text(attr) == "__dict__":
                        if subscript and subscript.type == "string":
                            result = extract_string_value(subscript)
                            if result:
                                ctx.dict_key_strings.append(result)

            # Pattern 2: getattr(obj, "name"), setattr(obj, "name", val), hasattr(obj, "name")
            elif node.type == "call":
                func = node.child_by_field_name("function")
                if func and func.type == "identifier":
                    func_name = self._get_text(func)
                    if func_name in ("getattr", "setattr", "hasattr", "delattr"):
                        args = node.child_by_field_name("arguments")
                        if args:
                            # The second argument should be the attribute name string
                            arg_count = 0
                            for child in args.children:
                                if child.type == "string":
                                    arg_count += 1
                                    if arg_count == 1:  # First string arg (after obj)
                                        result = extract_string_value(child)
                                        if result:
                                            ctx.dict_key_strings.append(result)
                                        break
                                elif child.type not in (",", "(", ")"):
                                    arg_count += 1

            # NOTE: We intentionally do NOT transform general dictionary literal keys
            # like {'base_url': value} because we can't reliably transform all the
            # corresponding subscript accesses like config['base_url']. Transforming
            # one but not the other causes KeyError mismatches.

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_external_object_vars(self, root: Node, ctx: CodeContext):
        """Track variables that are assigned from external function/method calls.

        Examples:
            options = parser.parse_args()  # options is external
            result = external_func()        # result is external
            x, y = external_func()          # x, y are external
        """

        def get_root_name(node: Node) -> str:
            """Get the root object name from a potentially chained attribute access."""
            while node.type == "attribute":
                obj = node.child_by_field_name("object")
                if obj:
                    node = obj
                else:
                    break
            return self._get_text(node)

        def is_external_call(call_node: Node) -> bool:
            """Check if this is a call to an external function/method."""
            func = call_node.child_by_field_name("function")
            if not func:
                return False

            if func.type == "identifier":
                name = self._get_text(func)
                return name in ctx.imported_names and name not in ctx.local_definitions
            elif func.type == "attribute":
                obj = func.child_by_field_name("object")
                if obj:
                    # Get the root of the object chain (for cases like importlib.util.find_spec)
                    root_name = get_root_name(obj)
                    # If root is a project package, the call is NOT external
                    if root_name in ctx.project_packages or root_name in ctx.project_module_aliases:
                        return False
                    # If root is an external module, the call is external
                    if root_name in ctx.imported_names or root_name in ctx.module_names:
                        return True
                    if root_name in ctx.external_object_vars:
                        return True
                    # For direct calls like parser.parse_args(), check if base is external
                    if obj.type == "identifier" and root_name not in ("self", "cls"):
                        if root_name not in ctx.local_definitions:
                            if root_name not in ctx.project_packages:
                                if root_name not in ctx.project_module_aliases:
                                    return True
            return False

        def extract_targets(node: Node) -> list[str]:
            """Extract variable names from assignment target."""
            targets = []
            if node.type == "identifier":
                targets.append(self._get_text(node))
            elif node.type in ("tuple_pattern", "pattern_list", "tuple"):
                for child in node.children:
                    targets.extend(extract_targets(child))
            return targets

        def visit(node: Node):
            # Assignment: x = external_call()
            if node.type == "assignment":
                right = node.child_by_field_name("right")
                if right and right.type == "call":
                    if is_external_call(right):
                        left = node.child_by_field_name("left")
                        if left:
                            for var in extract_targets(left):
                                ctx.external_object_vars.add(var)

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_parametrize_strings(self, root: Node, ctx: CodeContext):
        """Find pytest.mark.parametrize decorator strings."""

        def visit(node: Node):
            # Look for decorators
            if node.type == "decorator":
                # Check if it's a parametrize decorator
                decorator_text = self._get_text(node)
                if "parametrize" in decorator_text:
                    # Find the string argument(s)
                    self._find_parametrize_args(node, ctx)

            for child in node.children:
                visit(child)

        visit(root)

    def _find_parametrize_args(self, node: Node, ctx: CodeContext):
        """Extract parametrize argument strings."""

        def visit(n: Node):
            # Look for string nodes that contain parameter names
            if n.type == "string":
                text = self._get_text(n)
                # Check if this looks like a parameter name or comma-separated names
                # Strip quotes
                inner = text[1:-1] if len(text) >= 2 else text
                # Skip if it contains spaces (probably not a param name)
                if inner and " " not in inner.strip():
                    ctx.parametrize_strings.append((n.start_byte, n.end_byte, text))
                elif "," in inner:
                    # Could be "param1, param2" format
                    ctx.parametrize_strings.append((n.start_byte, n.end_byte, text))

            for child in n.children:
                visit(child)

        visit(node)

    def _collect_all_export_strings(self, root: Node, ctx: CodeContext):
        """Find __all__ assignment and collect string elements."""

        def visit(node: Node):
            # Look for assignments to __all__
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                if left and self._get_text(left) == "__all__":
                    # Found __all__ = [...], collect all strings in the list
                    right = node.child_by_field_name("right")
                    if right:
                        self._collect_strings_in_node(right, ctx)

            for child in node.children:
                visit(child)

        visit(root)

    def _collect_strings_in_node(self, node: Node, ctx: CodeContext):
        """Collect all string literals within a node (for __all__ lists)."""
        if node.type == "string":
            text = self._get_text(node)
            # Only collect single-word strings (identifier names)
            inner = text[1:-1] if len(text) >= 2 else text
            if inner and inner.isidentifier():
                ctx.all_export_strings.append((node.start_byte, node.end_byte, text))

        for child in node.children:
            self._collect_strings_in_node(child, ctx)


class CamelCaseTransformer(Transformer):
    """Transform snake_case identifiers to camelCase."""

    def __init__(
        self,
        project_packages: set[str] | None = None,
        project_definitions: set[str] | None = None,
        project_kwargs_functions: set[str] | None = None,
    ):
        super().__init__()
        self.name_mappings: dict[str, str] = {}
        self.project_packages = project_packages or set()
        # Project-wide definitions - names defined anywhere in the project
        # Used to allow attribute access transformation across files
        self.project_definitions = project_definitions or set()
        # Project-wide functions that use **kwargs - kwargs should not be transformed
        self.project_kwargs_functions = project_kwargs_functions or set()

    def _collect_identifiers(self, node: Node, source_bytes: bytes) -> list[tuple[str, int, int]]:
        """Collect all identifiers from the AST."""
        identifiers = []

        def visit(n: Node):
            if n.type == "identifier":
                name = source_bytes[n.start_byte:n.end_byte].decode('utf-8')
                identifiers.append((name, n.start_byte, n.end_byte))

            for child in n.children:
                visit(child)

        visit(node)
        return identifiers

    def _is_transformable(self, name: str, pos: tuple[int, int], ctx: CodeContext) -> bool:
        """Check if a name at a position should be transformed."""
        # Never transform builtins
        if name in PYTHON_BUILTINS:
            return False

        # Never transform dunder names
        if name.startswith("__") and name.endswith("__"):
            return False

        # Never transform ALL_CAPS constants
        if name.isupper():
            return False

        # Never transform single-char names
        if len(name.lstrip("_")) <= 1:
            return False

        # Don't transform imported names
        if name in ctx.imported_names:
            return False

        # Never transform attribute accesses on external modules (e.g., importlib.util.find_spec)
        # even if the name happens to be defined in the project
        if pos in ctx.external_module_attribute_positions:
            return False

        # Don't transform attribute accesses (module.method patterns)
        # UNLESS the name is a known project-wide definition (cross-file attribute sync)
        if pos in ctx.attribute_positions:
            if name not in self.project_definitions:
                return False

        # Don't transform module path identifiers (from .crypto_addresses import X)
        if pos in ctx.module_path_positions:
            return False

        # Don't transform keyword argument names in calls to external functions
        if pos in ctx.external_kwarg_positions:
            return False

        # For self.x patterns, only transform if x is a project definition
        # This prevents transforming inherited attributes from parent classes
        if pos in ctx.self_attribute_positions:
            if name not in ctx.local_definitions and name not in self.project_definitions:
                return False

        # Must look like snake_case (has underscore, not all caps)
        if "_" not in name or name.isupper():
            return False

        return True

    def collect_definitions_from_directory(
        self,
        directory: Path | str,
        pattern: str = "**/*.py",
        exclude_patterns: list[str] | None = None,
    ) -> set[str]:
        """
        Collect all snake_case definitions from a directory.

        This is used for two-pass transformation: first collect all definitions,
        then transform with project-wide knowledge.

        Also collects functions that use **kwargs and stores them in
        self.project_kwargs_functions.

        Returns:
            Set of snake_case names defined in the project
        """
        from pathlib import Path

        directory = Path(directory)
        exclude_patterns = exclude_patterns or []
        definitions = set()

        for file_path in directory.glob(pattern):
            # Skip excluded patterns
            skip = False
            for exclude in exclude_patterns:
                if file_path.match(exclude):
                    skip = True
                    break
            if skip or not file_path.is_file():
                continue

            try:
                source_code = file_path.read_text()
                source_bytes = source_code.encode('utf-8')
                root = self.parse(source_code)

                analyzer = NameAnalyzer(source_bytes, self.project_packages)
                ctx = analyzer.analyze(root)

                # Add all local definitions that look like snake_case
                for name in ctx.local_definitions:
                    if "_" in name and not name.isupper() and name not in PYTHON_BUILTINS:
                        definitions.add(name)

                # Add all kwargs functions to project-wide set
                self.project_kwargs_functions.update(ctx.kwargs_functions)
            except Exception:
                continue

        return definitions

    def _build_name_mappings(
        self, identifiers: list[tuple[str, int, int]], ctx: CodeContext
    ) -> dict[str, str]:
        """Build consistent name mappings for transformable identifiers."""
        mappings = {}

        for name, start, end in identifiers:
            if name in mappings:
                continue

            if not self._is_transformable(name, (start, end), ctx):
                continue

            transformed = snake_to_camel(name)
            if transformed != name:
                mappings[name] = transformed

        return mappings

    def _transform_parametrize_strings(
        self, source_bytes: bytes, ctx: CodeContext, mappings: dict[str, str]
    ) -> bytes:
        """Transform parameter names inside pytest.mark.parametrize strings."""
        result = source_bytes

        # Sort by position in reverse order
        sorted_strings = sorted(ctx.parametrize_strings, key=lambda x: x[0], reverse=True)

        for start, end, original in sorted_strings:
            # Get the current text at this position
            try:
                current = result[start:end].decode('utf-8')
            except UnicodeDecodeError:
                continue  # Position has shifted, skip

            if current != original:
                continue  # Position has shifted, skip

            # Determine quote style
            quote = original[0]
            inner = original[1:-1]

            # Transform parameter names in the string
            new_inner = inner
            for old_name, new_name in mappings.items():
                # Replace whole words only
                new_inner = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, new_inner)

            if new_inner != inner:
                new_string = quote + new_inner + quote
                result = result[:start] + new_string.encode('utf-8') + result[end:]

        return result

    def _transform_all_export_strings(
        self, source_bytes: bytes, ctx: CodeContext, mappings: dict[str, str]
    ) -> bytes:
        """Transform names inside __all__ list strings."""
        result = source_bytes

        # Sort by position in reverse order
        sorted_strings = sorted(ctx.all_export_strings, key=lambda x: x[0], reverse=True)

        for start, end, original in sorted_strings:
            # Get the current text at this position
            try:
                current = result[start:end].decode('utf-8')
            except UnicodeDecodeError:
                continue  # Position has shifted, skip

            if current != original:
                continue  # Position has shifted, skip

            # Determine quote style
            quote = original[0]
            inner = original[1:-1]

            # Check if this name should be transformed
            if inner in mappings:
                new_string = quote + mappings[inner] + quote
                result = result[:start] + new_string.encode('utf-8') + result[end:]

        return result

    def transform(self, source_code: str) -> TransformResult:
        """Transform snake_case identifiers to camelCase."""
        source_bytes = source_code.encode('utf-8')
        root = self.parse(source_code)

        # Analyze the code to understand context
        analyzer = NameAnalyzer(source_bytes, self.project_packages)
        ctx = analyzer.analyze(root)

        # Add project-wide kwargs functions (from first pass) to context
        # This allows cross-file detection of **kwargs functions
        ctx.kwargs_functions.update(self.project_kwargs_functions)

        # Re-run external kwargs collection with updated kwargs_functions
        analyzer._collect_external_kwargs(root, ctx)

        # Collect all identifiers
        identifiers = self._collect_identifiers(root, source_bytes)

        # Build mappings (only for transformable names)
        self.name_mappings = self._build_name_mappings(identifiers, ctx)

        if not self.name_mappings:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["No snake_case identifiers found to transform"],
            )

        # Collect ALL replacement positions (identifiers + strings)
        replacements = []

        # Add identifier replacements
        for name, start, end in identifiers:
            if name in self.name_mappings:
                if self._is_transformable(name, (start, end), ctx):
                    replacements.append((start, end, name, self.name_mappings[name]))

        # Add parametrize string replacements
        for start, end, original in ctx.parametrize_strings:
            quote = original[0]
            inner = original[1:-1]
            new_inner = inner
            for old_name, new_name in self.name_mappings.items():
                new_inner = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, new_inner)
            if new_inner != inner:
                replacements.append((start, end, original, quote + new_inner + quote))

        # Add __all__ string replacements
        for start, end, original in ctx.all_export_strings:
            quote = original[0]
            inner = original[1:-1]
            if inner in self.name_mappings:
                new_str = quote + self.name_mappings[inner] + quote
                replacements.append((start, end, original, new_str))

        # Add format string placeholder replacements
        for start, end, placeholder_name in ctx.format_string_placeholders:
            if placeholder_name in self.name_mappings:
                new_name = self.name_mappings[placeholder_name]
                replacements.append((start, end, placeholder_name, new_name))

        # Add dict key string replacements (__dict__["name"], getattr, etc.)
        for start, end, key_name in ctx.dict_key_strings:
            if key_name in self.name_mappings:
                replacements.append((start, end, key_name, self.name_mappings[key_name]))

        # Sort by position in reverse order (process from end to start)
        replacements.sort(key=lambda x: x[0], reverse=True)

        # Apply all replacements in one pass
        result_bytes = source_bytes
        changes_applied = 0
        for start, end, original, transformed in replacements:
            try:
                actual = result_bytes[start:end].decode('utf-8')
            except UnicodeDecodeError:
                continue
            if actual != original:
                continue
            result_bytes = result_bytes[:start] + transformed.encode('utf-8') + result_bytes[end:]
            changes_applied += 1

        result = result_bytes.decode('utf-8')

        # Generate details
        details = [f"Renamed {len(self.name_mappings)} identifier types:"]
        for original, transformed in sorted(self.name_mappings.items()):
            count = sum(1 for n, s, e in identifiers
                       if n == original and self._is_transformable(n, (s, e), ctx))
            details.append(f"  {original} → {transformed} ({count} occurrences)")

        return TransformResult(
            original_code=source_code,
            transformed_code=result,
            changes_made=changes_applied,
            details=details,
        )


class SnakeCaseTransformer(Transformer):
    """Transform camelCase identifiers to snake_case."""

    def __init__(self):
        super().__init__()
        self.name_mappings: dict[str, str] = {}

    def _collect_identifiers(self, node: Node, source_bytes: bytes) -> list[tuple[str, int, int]]:
        """Collect all identifiers from the AST."""
        identifiers = []

        def visit(n: Node):
            if n.type == "identifier":
                name = source_bytes[n.start_byte:n.end_byte].decode('utf-8')
                identifiers.append((name, n.start_byte, n.end_byte))

            for child in n.children:
                visit(child)

        visit(node)
        return identifiers

    def _is_transformable(self, name: str, pos: tuple[int, int], ctx: CodeContext) -> bool:
        """Check if a name should be transformed."""
        if name in PYTHON_BUILTINS:
            return False
        if name.startswith("__") and name.endswith("__"):
            return False
        if name.isupper():
            return False
        if len(name.lstrip("_")) <= 1:
            return False
        if name in ctx.imported_names:
            return False
        if pos in ctx.attribute_positions:
            return False

        # Must look like camelCase (has lowercase followed by uppercase)
        if not re.search(r"[a-z][A-Z]", name):
            return False

        return True

    def _build_name_mappings(
        self, identifiers: list[tuple[str, int, int]], ctx: CodeContext
    ) -> dict[str, str]:
        """Build consistent name mappings."""
        mappings = {}

        for name, start, end in identifiers:
            if name in mappings:
                continue

            if not self._is_transformable(name, (start, end), ctx):
                continue

            transformed = camel_to_snake(name)
            if transformed != name:
                mappings[name] = transformed

        return mappings

    def transform(self, source_code: str) -> TransformResult:
        """Transform camelCase identifiers to snake_case."""
        source_bytes = source_code.encode('utf-8')
        root = self.parse(source_code)

        analyzer = NameAnalyzer(source_bytes)
        ctx = analyzer.analyze(root)

        identifiers = self._collect_identifiers(root, source_bytes)
        self.name_mappings = self._build_name_mappings(identifiers, ctx)

        if not self.name_mappings:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["No camelCase identifiers found to transform"],
            )

        replacements = []
        for name, start, end in identifiers:
            if name in self.name_mappings:
                if self._is_transformable(name, (start, end), ctx):
                    replacements.append((start, end, name, self.name_mappings[name]))

        replacements.sort(key=lambda x: x[0], reverse=True)

        result_bytes = source_bytes
        changes_applied = 0
        for start, end, original, transformed in replacements:
            actual = result_bytes[start:end].decode('utf-8')
            if actual != original:
                continue
            result_bytes = result_bytes[:start] + transformed.encode('utf-8') + result_bytes[end:]
            changes_applied += 1

        result = result_bytes.decode('utf-8')

        details = [f"Renamed {len(self.name_mappings)} identifier types:"]
        for original, transformed in sorted(self.name_mappings.items()):
            count = sum(1 for n, s, e in identifiers
                       if n == original and self._is_transformable(n, (s, e), ctx))
            details.append(f"  {original} → {transformed} ({count} occurrences)")

        return TransformResult(
            original_code=source_code,
            transformed_code=result,
            changes_made=changes_applied,
            details=details,
        )


class BadNamingTransformer(Transformer):
    """
    Transform descriptive variable names to single-letter names.

    Only transforms locally-defined variables within functions.
    """

    def __init__(self, rename_parameters: bool = False):
        super().__init__()
        self.rename_parameters = rename_parameters
        self.name_mappings: dict[str, str] = {}

    def _get_next_name(self, used_names: set[str]) -> str:
        """Generate the next available single-letter name."""
        for c in "abcdefghijklmnopqrstuvwxyz":
            if c not in used_names:
                return c
        for c1 in "abcdefghijklmnopqrstuvwxyz":
            for c2 in "abcdefghijklmnopqrstuvwxyz":
                name = c1 + c2
                if name not in used_names:
                    return name
        raise ValueError("Ran out of short names")

    def _collect_local_variables(
        self, node: Node, source_bytes: bytes, ctx: CodeContext
    ) -> tuple[set[str], list[tuple[str, int, int]]]:
        """Collect local variable names defined within functions."""
        defined_names: set[str] = set()
        all_identifiers: list[tuple[str, int, int]] = []

        def collect_definitions(n: Node, in_function: bool = False):
            if n.type in ("function_definition", "lambda"):
                in_function = True

                if self.rename_parameters and n.type == "function_definition":
                    params = n.child_by_field_name("parameters")
                    if params:
                        for child in params.children:
                            if child.type == "identifier":
                                name = source_bytes[child.start_byte:child.end_byte].decode('utf-8')
                                defined_names.add(name)

            if in_function:
                if n.type == "assignment":
                    left = n.child_by_field_name("left")
                    if left and left.type == "identifier":
                        name = source_bytes[left.start_byte:left.end_byte].decode('utf-8')
                        defined_names.add(name)

                elif n.type == "for_statement":
                    left = n.child_by_field_name("left")
                    if left and left.type == "identifier":
                        name = source_bytes[left.start_byte:left.end_byte].decode('utf-8')
                        defined_names.add(name)

            for child in n.children:
                collect_definitions(child, in_function)

        def collect_all_identifiers(n: Node):
            if n.type == "identifier":
                name = source_bytes[n.start_byte:n.end_byte].decode('utf-8')
                all_identifiers.append((name, n.start_byte, n.end_byte))
            for child in n.children:
                collect_all_identifiers(child)

        collect_definitions(node)
        collect_all_identifiers(node)
        return defined_names, all_identifiers

    def _is_transformable(self, name: str, ctx: CodeContext) -> bool:
        """Check if a name should be transformed."""
        if name in PYTHON_BUILTINS:
            return False
        if name.startswith("__") and name.endswith("__"):
            return False
        if name.isupper():
            return False
        if len(name) <= 1:
            return False
        if name in ctx.imported_names:
            return False
        return True

    def transform(self, source_code: str) -> TransformResult:
        """Transform local variable names to single-letter names."""
        source_bytes = source_code.encode('utf-8')
        root = self.parse(source_code)

        analyzer = NameAnalyzer(source_bytes)
        ctx = analyzer.analyze(root)

        defined_names, all_identifiers = self._collect_local_variables(root, source_bytes, ctx)

        # Filter to transformable names
        transformable = {n for n in defined_names if self._is_transformable(n, ctx)}

        if not transformable:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["No local variables found to transform"],
            )

        # Build mappings
        used_names = PYTHON_BUILTINS | ctx.imported_names
        self.name_mappings = {}
        for name in sorted(transformable):
            new_name = self._get_next_name(used_names)
            self.name_mappings[name] = new_name
            used_names.add(new_name)

        # Apply replacements
        replacements = []
        for name, start, end in all_identifiers:
            if name in self.name_mappings:
                replacements.append((start, end, name, self.name_mappings[name]))

        replacements.sort(key=lambda x: x[0], reverse=True)

        result_bytes = source_bytes
        changes_applied = 0
        for start, end, original, transformed in replacements:
            actual = result_bytes[start:end].decode('utf-8')
            if actual != original:
                continue
            result_bytes = result_bytes[:start] + transformed.encode('utf-8') + result_bytes[end:]
            changes_applied += 1

        result = result_bytes.decode('utf-8')

        details = [f"Renamed {len(self.name_mappings)} variables to short names:"]
        for original, transformed in sorted(self.name_mappings.items()):
            count = sum(1 for n, _, _ in all_identifiers if n == original)
            details.append(f"  {original} → {transformed} ({count} occurrences)")

        return TransformResult(
            original_code=source_code,
            transformed_code=result,
            changes_made=changes_applied,
            details=details,
        )
