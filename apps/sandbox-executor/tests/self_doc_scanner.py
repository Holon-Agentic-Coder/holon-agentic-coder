from __future__ import annotations

import ast
import io
import re
import tokenize

_DOCSTRING_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_ENCODING_COOKIE_RE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
_CODE_LIST = r"[A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*"
_PRAGMA_COMMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^#\s*noqa(?::\s*{_CODE_LIST})?\s*$", re.IGNORECASE),
    re.compile(rf"^#\s*type:\s*ignore(?:\[\s*{_CODE_LIST}\s*\])?\s*$"),
    re.compile(r"^#\s*pragma:\s*no\s+cover\s*$", re.IGNORECASE),
    re.compile(r"^#\s*fmt:\s*(?:off|on|skip)\s*$"),
)
_WHY_COMMENT_RE = re.compile(r"^#\s*why:\s*\S")


def _is_string_statement(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _docstring_findings(tree: ast.Module) -> dict[tuple[int, int], tuple[int, str]]:
    findings: dict[tuple[int, int], tuple[int, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_NODE_TYPES) and ast.get_docstring(node, clean=False) is not None:
            first_statement = node.body[0]
            label = f"docstring:{getattr(node, 'name', '<module>')}"
            findings[(first_statement.lineno, first_statement.col_offset)] = (first_statement.lineno, label)
    return findings


def find_docstrings_and_string_statements(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    findings = _docstring_findings(tree)
    for node in ast.walk(tree):
        if _is_string_statement(node):
            position = (node.lineno, node.col_offset)
            findings.setdefault(position, (node.lineno, "string statement"))
    return sorted(findings.values())


def is_allowlisted_comment(line_number: int, comment_text: str, column: int = 0, *, allow_why: bool = False) -> bool:
    if allow_why and _WHY_COMMENT_RE.match(comment_text):
        return True
    if comment_text.startswith("#!"):
        return line_number == 1 and column == 0
    if line_number in (1, 2) and _ENCODING_COOKIE_RE.search(comment_text):
        return True
    return any(pattern.match(comment_text) for pattern in _PRAGMA_COMMENT_PATTERNS)


def find_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]


def find_disallowed_comments(source: str, *, allow_why: bool = False) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line_number, column = token.start
        if not is_allowlisted_comment(line_number, token.string, column, allow_why=allow_why):
            findings.append((line_number, token.string))
    return findings


def count_lines(source: str) -> int:
    return len(source.splitlines())
