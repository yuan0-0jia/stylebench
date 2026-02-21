"""Documentation removal transformers for StyleBench.

Two variants:
- NoDocstringsTransformer: removes function/class/module docstrings only
- NoDocsFullTransformer: removes docstrings + inline comments + type annotations
"""

import re

from tree_sitter import Node

from .base import Transformer, TransformResult

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_docstring_stmt(node: Node) -> bool:
    """Return True if node is an expression_statement wrapping just a string."""
    if node.type != "expression_statement":
        return False
    real_children = [c for c in node.children if c.type != "newline"]
    return len(real_children) == 1 and real_children[0].type in ("string", "concatenated_string")


def _collect_docstrings(root: Node) -> list[tuple[int, int]]:
    """Return (start_byte, end_byte) for every docstring node in the tree."""
    ranges: list[tuple[int, int]] = []

    def walk(node: Node) -> None:
        # Module-level docstring: first non-newline child of module
        if node.type == "module":
            for child in node.children:
                if child.type in ("newline", "comment"):
                    continue
                if _is_docstring_stmt(child):
                    ranges.append((child.start_byte, child.end_byte))
                break

        # Function / class body docstring: first non-trivial child of block
        if node.type == "block":
            for child in node.children:
                if child.type in ("newline", "indent", "comment"):
                    continue
                if _is_docstring_stmt(child):
                    ranges.append((child.start_byte, child.end_byte))
                break

        for child in node.children:
            walk(child)

    walk(root)
    return ranges


def _collect_comments(root: Node, source_bytes: bytes) -> list[tuple[int, int]]:
    """Return (start_byte, end_byte) for every comment node.

    For inline comments (code before # on same line), the range is expanded
    leftward to consume trailing whitespace between the code and the #.
    """
    ranges: list[tuple[int, int]] = []

    def walk(node: Node) -> None:
        if node.type == "comment":
            start = node.start_byte
            # Consume any spaces/tabs immediately before the # (inline comment cleanup)
            while start > 0 and source_bytes[start - 1 : start] in (b" ", b"\t"):
                start -= 1
            ranges.append((start, node.end_byte))
        for child in node.children:
            walk(child)

    walk(root)
    return ranges


def _collect_type_annotations(root: Node, source_bytes: bytes) -> list[tuple[int, int]]:
    """Return (start_byte, end_byte) for parameter and return type annotations.

    Ranges are expanded leftward to consume any whitespace that would be
    left dangling after removal (e.g. the space before '->').
    """
    ranges: list[tuple[int, int]] = []

    def _trim_left(start: int) -> int:
        """Expand start leftward to consume preceding spaces/tabs."""
        while start > 0 and source_bytes[start - 1 : start] in (b" ", b"\t"):
            start -= 1
        return start

    def walk(node: Node) -> None:
        # Return type: -> Type  (consume leading whitespace before ->)
        if node.type == "function_definition":
            children = node.children
            for i, child in enumerate(children):
                if child.type == "->" and i + 1 < len(children):
                    ret = children[i + 1]
                    if ret.type == "type":
                        ranges.append((_trim_left(child.start_byte), ret.end_byte))
                    break

        # Parameter annotation: param: Type  or  param: Type = default
        if node.type in ("typed_parameter", "typed_default_parameter"):
            children = node.children
            for i, child in enumerate(children):
                if child.type == ":" and i + 1 < len(children):
                    ann = children[i + 1]
                    if ann.type == "type":
                        ranges.append((child.start_byte, ann.end_byte))
                    break

        for child in node.children:
            walk(child)

    walk(root)
    return ranges


# ---------------------------------------------------------------------------
# Apply removals to source
# ---------------------------------------------------------------------------


def _expand_to_line(source_bytes: bytearray, start: int, end: int) -> tuple[int, int]:
    """Expand a byte range to cover the whole source line if the line
    would be blank after removal. Otherwise return the range unchanged.

    A 'whole line' means: expand leftward to include the newline before
    (or start of file) and rightward to include the trailing newline.
    """
    # Walk left to find start of line
    line_start = start
    while line_start > 0 and source_bytes[line_start - 1 : line_start] not in (b"\n",):
        line_start -= 1

    # Everything between line_start and start should be whitespace only
    prefix = source_bytes[line_start:start]
    if any(b not in b" \t" for b in prefix):
        # Code before the node on the same line — don't expand
        return start, end

    # Walk right to include the trailing newline
    line_end = end
    if line_end < len(source_bytes) and source_bytes[line_end : line_end + 1] == b"\n":
        line_end += 1

    return line_start, line_end


def _apply_removals(
    source_bytes: bytes,
    raw_ranges: list[tuple[int, int]],
    expand_lines: bool = True,
) -> str:
    """Apply byte-range removals in reverse order."""
    if not raw_ranges:
        return source_bytes.decode("utf-8", errors="replace")

    buf = bytearray(source_bytes)
    # De-duplicate and sort descending so earlier positions stay valid
    seen: set[tuple[int, int]] = set()
    sorted_ranges: list[tuple[int, int]] = []
    for r in sorted(raw_ranges, key=lambda x: x[0], reverse=True):
        if r not in seen:
            seen.add(r)
            if expand_lines:
                r = _expand_to_line(buf, r[0], r[1])
            sorted_ranges.append(r)

    for start, end in sorted_ranges:
        del buf[start:end]

    text = buf.decode("utf-8", errors="replace")
    # Collapse runs of 3+ blank lines down to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ---------------------------------------------------------------------------
# Transformers
# ---------------------------------------------------------------------------


class NoDocstringsTransformer(Transformer):
    """Remove all module, class, and function docstrings.

    Keeps inline comments and type annotations intact.
    """

    def transform(self, source_code: str) -> TransformResult:
        source_bytes = source_code.encode("utf-8")
        root = self.parse(source_code)

        ranges = _collect_docstrings(root)
        if not ranges:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["No docstrings found"],
            )

        transformed = _apply_removals(source_bytes, ranges, expand_lines=True)
        return TransformResult(
            original_code=source_code,
            transformed_code=transformed,
            changes_made=len(ranges),
            details=[f"Removed {len(ranges)} docstring(s)"],
        )


class NoDocsFullTransformer(Transformer):
    """Remove docstrings, inline comments, and type annotations.

    This is the most aggressive documentation-removal style:
    all semantic metadata is stripped, leaving only executable code.
    """

    def transform(self, source_code: str) -> TransformResult:
        source_bytes = source_code.encode("utf-8")
        root = self.parse(source_code)

        docstrings = _collect_docstrings(root)
        comments = _collect_comments(root, source_bytes)
        annotations = _collect_type_annotations(root, source_bytes)

        # Docstrings and whole-line comments: expand to full line
        # Type annotations and inline comments: do NOT expand (they're mid-line)
        all_ranges: list[tuple[int, int]] = []

        # Expand docstrings to full lines
        buf = bytearray(source_bytes)
        for r in docstrings:
            all_ranges.append(_expand_to_line(buf, r[0], r[1]))

        # Comments: expand whole-line ones, trim inline ones
        for start, end in comments:
            expanded = _expand_to_line(buf, start, end)
            all_ranges.append(expanded)

        # Type annotations: exact range only (mid-expression)
        all_ranges.extend(annotations)

        if not all_ranges:
            return TransformResult(
                original_code=source_code,
                transformed_code=source_code,
                changes_made=0,
                details=["No documentation found"],
            )

        # Apply in reverse order without double-expanding (already expanded above)
        seen: set[tuple[int, int]] = set()
        sorted_ranges = []
        for r in sorted(all_ranges, key=lambda x: x[0], reverse=True):
            if r not in seen:
                seen.add(r)
                sorted_ranges.append(r)

        for start, end in sorted_ranges:
            del buf[start:end]

        text = buf.decode("utf-8", errors="replace")
        text = re.sub(r"\n{3,}", "\n\n", text)

        n = len(docstrings) + len(comments) + len(annotations)
        return TransformResult(
            original_code=source_code,
            transformed_code=text,
            changes_made=n,
            details=[
                f"Removed {len(docstrings)} docstring(s), "
                f"{len(comments)} comment(s), "
                f"{len(annotations)} type annotation(s)"
            ],
        )
