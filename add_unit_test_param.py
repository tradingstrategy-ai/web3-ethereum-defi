#!/usr/bin/env python3
"""Add unit_test=True parameter to all create_multi_provider_web3() calls in tests/ directory.

This script:
- Uses AST to locate calls precisely
- Uses text replacement to preserve formatting
- Adds unit_test=True if not already present
"""

import ast
import os
from pathlib import Path
from typing import List


class CallFinder(ast.NodeVisitor):
    """Find all calls to create_multi_provider_web3."""

    def __init__(self, source_lines: List[str]):
        self.calls = []
        self.source_lines = source_lines

    def visit_Call(self, node: ast.Call):
        """Visit function call nodes."""
        # Check if this is a call to create_multi_provider_web3
        if isinstance(node.func, ast.Name) and node.func.id == "create_multi_provider_web3":
            # Check if unit_test is already in keywords
            has_unit_test = any(kw.arg == "unit_test" for kw in node.keywords)

            if not has_unit_test:
                # Store the location info for this call
                self.calls.append(
                    {
                        "lineno": node.lineno,
                        "col_offset": node.col_offset,
                        "end_lineno": node.end_lineno,
                        "end_col_offset": node.end_col_offset,
                        "node": node,
                    }
                )

        self.generic_visit(node)


def find_closing_paren(content: str, start_pos: int) -> int:
    """Find the matching closing parenthesis starting from start_pos.

    Args:
        content: The source code
        start_pos: Position of opening parenthesis

    Returns:
        Position of matching closing parenthesis
    """
    depth = 0
    i = start_pos

    while i < len(content):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return -1


def process_file(file_path: Path) -> bool:
    """Process a single Python file to add unit_test=True parameter.

    Returns True if file was modified, False otherwise.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    original_content = content

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        print(f"Syntax error in {file_path}, skipping")
        return False

    source_lines = content.splitlines(keepends=True)
    finder = CallFinder(source_lines)
    finder.visit(tree)

    if not finder.calls:
        return False

    # Process calls in reverse order to preserve positions
    calls = sorted(finder.calls, key=lambda x: (x["lineno"], x["col_offset"]), reverse=True)

    for call_info in calls:
        node = call_info["node"]

        # Convert line/col to absolute position in content
        # AST uses 1-based line numbers
        line_start = sum(len(line) for line in source_lines[: node.lineno - 1])
        call_start = line_start + node.col_offset

        # Find the opening parenthesis of the function call
        func_name = "create_multi_provider_web3"
        func_start = content.find(func_name, call_start)

        if func_start == -1:
            continue

        # Find opening paren after function name
        paren_start = content.find("(", func_start + len(func_name))
        if paren_start == -1:
            continue

        # Find matching closing paren
        paren_end = find_closing_paren(content, paren_start)
        if paren_end == -1:
            continue

        # Extract the content inside parentheses
        args_content = content[paren_start + 1 : paren_end]

        # Check if there are any arguments
        stripped_args = args_content.strip()

        if not stripped_args:
            # No arguments, just add unit_test=True
            new_content = content[: paren_start + 1] + "unit_test=True" + content[paren_end:]
        else:
            # Has arguments
            # We need to add unit_test=True before the closing paren

            # Look backwards from closing paren to find last non-whitespace character
            check_pos = paren_end - 1
            while check_pos > paren_start and content[check_pos] in " \t\n\r":
                check_pos -= 1

            has_trailing_comma = content[check_pos] == ","

            # Check if this is a multi-line call
            # If there's a newline in args_content, it's multi-line
            is_multiline = "\n" in args_content

            if is_multiline:
                # Multi-line call
                # Find the indentation of the closing paren
                # Go back from paren_end to find the start of the line
                line_start_pos = paren_end - 1
                while line_start_pos > 0 and content[line_start_pos - 1] not in "\n":
                    line_start_pos -= 1

                # Get the indentation before the closing paren
                closing_paren_indent = content[line_start_pos:paren_end].replace("\t", " " * 4)
                indent_len = len(closing_paren_indent) - len(closing_paren_indent.lstrip())
                base_indent = closing_paren_indent[:indent_len]

                # Find the indentation of the last argument line
                # Go backwards to find the previous line's indentation
                prev_line_end = check_pos
                while prev_line_end > paren_start and content[prev_line_end] not in "\n":
                    prev_line_end -= 1

                if prev_line_end > paren_start:
                    # Found a newline, get indentation of next line
                    prev_line_start = prev_line_end + 1
                    while prev_line_start < check_pos and content[prev_line_start] in " \t":
                        prev_line_start += 1

                    arg_indent_start = prev_line_end + 1
                    arg_line_content = content[arg_indent_start : check_pos + 1]
                    arg_indent = ""
                    for ch in arg_line_content:
                        if ch in " \t":
                            arg_indent += ch
                        else:
                            break

                    # Use the argument indentation for the new parameter
                    # We need to preserve the whitespace/newline before the closing paren
                    # Find where whitespace starts before closing paren
                    ws_before_paren_start = check_pos + 1
                    while ws_before_paren_start < paren_end and content[ws_before_paren_start] in " \t\n\r":
                        ws_before_paren_start += 1

                    if ws_before_paren_start < paren_end:
                        # There's whitespace, likely on its own line
                        # Insert before the whitespace
                        if has_trailing_comma:
                            # Has trailing comma, just add on a new line with same indentation
                            new_content = content[: check_pos + 1] + "\n" + arg_indent + "unit_test=True," + content[check_pos + 1 :]
                        else:
                            # No trailing comma, add comma then new line
                            new_content = content[: check_pos + 1] + ",\n" + arg_indent + "unit_test=True," + content[check_pos + 1 :]
                    else:
                        # No whitespace before paren
                        if has_trailing_comma:
                            new_content = content[: check_pos + 1] + " unit_test=True" + content[paren_end:]
                        else:
                            new_content = content[: check_pos + 1] + ", unit_test=True" + content[paren_end:]
                else:
                    # Fallback: single argument on multiple lines
                    if has_trailing_comma:
                        new_content = content[: check_pos + 1] + " unit_test=True," + content[paren_end:]
                    else:
                        new_content = content[: check_pos + 1] + ", unit_test=True" + content[paren_end:]
            else:
                # Single line call
                if has_trailing_comma:
                    new_content = content[: check_pos + 1] + " unit_test=True" + content[paren_end:]
                else:
                    new_content = content[: check_pos + 1] + ", unit_test=True" + content[paren_end:]

        content = new_content

    # Write back if modified
    if content != original_content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    return False


def main():
    """Process all Python files in tests/ directory."""
    tests_dir = Path(__file__).parent / "tests"

    if not tests_dir.exists():
        print(f"Error: {tests_dir} does not exist")
        return 1

    modified_files = []
    processed_count = 0

    # Find all .py files recursively
    for py_file in tests_dir.rglob("*.py"):
        processed_count += 1
        if process_file(py_file):
            modified_files.append(py_file)
            print(f"Modified: {py_file.relative_to(tests_dir.parent)}")

    print(f"\nProcessed {processed_count} files")
    print(f"Modified {len(modified_files)} files")

    return 0


if __name__ == "__main__":
    exit(main())
