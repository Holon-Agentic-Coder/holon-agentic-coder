from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

try:
    from scripts.self_explaining_hash import numbered_lines, shell_comments, toml_comments, yaml_comments
except ImportError:
    from self_explaining_hash import numbered_lines, shell_comments, toml_comments, yaml_comments

REASON_PREFIX = "why:"
MACHINE_DIRECTIVE = re.compile(r"(?:noqa\b|type:\s*\S|pragma:|fmt:\s*(?:off|on|skip)\b|shellcheck\s)")
ENCODING_DECLARATION = re.compile(r"coding[:=]")
COMMENT_MARKER = re.compile(r"^\s*(?:#+|/{2,}|/\*+)\s*")
BLOCK_COMMENT_END = re.compile(r"\s*\*+/\s*$")
SHELL_SHEBANG = re.compile(r"^#!.*\b(?:ba)?sh\b")
SHELL_LIKE_SUFFIXES = frozenset({".sh", ".bash", ".dockerfile"})
YAML_SUFFIXES = frozenset({".yml", ".yaml"})
SHELL_LIKE_NAMES = frozenset({"Dockerfile"})
MAKEFILE_NAMES = frozenset({"Makefile", "GNUmakefile"})
JAVASCRIPT_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})
JAVASCRIPT_TOKEN = re.compile(
    r"\"(?:\\.|[^\"\\\n])*\"?|'(?:\\.|[^'\\\n])*'?|`(?:\\.|[^`\\])*`?|//[^\n]*|/\*.*?(?:\*/|\Z)",
    re.DOTALL,
)
DOCSTRING_KINDS: dict[type[ast.AST], str] = {
    ast.Module: "module docstring",
    ast.ClassDef: "class docstring",
    ast.FunctionDef: "function docstring",
    ast.AsyncFunctionDef: "function docstring",
}


@dataclass(frozen=True)
class Finding:
    first_line: int
    last_line: int
    kind: str
    text: str


def comment_body(comment: str) -> str:
    return BLOCK_COMMENT_END.sub("", COMMENT_MARKER.sub("", comment, count=1)).strip()


def is_allowed_comment(comment: str, line: int) -> bool:
    if line == 1 and comment.startswith("#!"):
        return True
    if line <= 2 and ENCODING_DECLARATION.search(comment):
        return True
    body = comment_body(comment)
    return body.lower().startswith(REASON_PREFIX) or MACHINE_DIRECTIVE.match(body) is not None


def has_shell_shebang(text: str) -> bool:
    return SHELL_SHEBANG.match(text.partition("\n")[0]) is not None


def is_shell_like(path: PurePosixPath, text: str) -> bool:
    if path.name in SHELL_LIKE_NAMES or path.name.startswith("Dockerfile."):
        return True
    if path.suffix in SHELL_LIKE_SUFFIXES:
        return True
    return path.suffix == "" and has_shell_shebang(text)


def python_docstrings(text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    findings = []
    for node in ast.walk(tree):
        kind = DOCSTRING_KINDS.get(type(node))
        if kind is None or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            findings.append(Finding(first.lineno, first.end_lineno or first.lineno, kind, first.value.value))
    return findings


def python_comments(text: str) -> list[Finding]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    return [
        Finding(token.start[0], token.start[0], "comment", token.string)
        for token in tokens
        if token.type == tokenize.COMMENT
    ]


def python_findings(text: str) -> list[Finding]:
    return python_docstrings(text) + python_comments(text)


def line_comment_findings(comments: Iterable[tuple[int, str]]) -> list[Finding]:
    return [Finding(number, number, "comment", comment) for number, comment in comments]


def shell_findings(text: str) -> list[Finding]:
    return line_comment_findings(shell_comments(numbered_lines(text)))


def makefile_findings(text: str) -> list[Finding]:
    return line_comment_findings(shell_comments(numbered_lines(text), heredocs=False))


def yaml_findings(text: str) -> list[Finding]:
    return line_comment_findings(yaml_comments(numbered_lines(text)))


def toml_findings(text: str) -> list[Finding]:
    return line_comment_findings(toml_comments(text))


def javascript_comment_kind(comment: str) -> str:
    if comment.startswith("/**"):
        return "JSDoc comment"
    return "block comment" if comment.startswith("/*") else "comment"


def javascript_findings(text: str) -> list[Finding]:
    findings = []
    for match in JAVASCRIPT_TOKEN.finditer(text):
        comment = match.group()
        if comment.startswith(("//", "/*")):
            first_line = text.count("\n", 0, match.start()) + 1
            last_line = first_line + comment.count("\n")
            findings.append(Finding(first_line, last_line, javascript_comment_kind(comment), comment))
    return findings


def language_finder(path: str, text: str) -> Callable[[str], list[Finding]] | None:
    pure_path = PurePosixPath(path)
    if pure_path.suffix == ".py":
        return python_findings
    if pure_path.suffix in JAVASCRIPT_SUFFIXES:
        return javascript_findings
    if pure_path.suffix in YAML_SUFFIXES:
        return yaml_findings
    if pure_path.suffix == ".toml":
        return toml_findings
    if pure_path.name in MAKEFILE_NAMES or pure_path.suffix == ".mk":
        return makefile_findings
    return shell_findings if is_shell_like(pure_path, text) else None


def find_comments_and_docstrings(path: str, text: str) -> list[Finding]:
    finder = language_finder(path, text)
    if finder is None:
        return []
    return [
        finding
        for finding in finder(text)
        if "docstring" in finding.kind or not is_allowed_comment(finding.text, finding.first_line)
    ]
