from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

REASON_PREFIX = "why:"
MACHINE_DIRECTIVE_PREFIXES = ("noqa", "type:", "pragma", "fmt:", "shellcheck")
ENCODING_DECLARATION = re.compile(r"coding[:=]")
COMMENT_MARKER = re.compile(r"^\s*(?:#+|/{2,}|/\*+)\s*")
BLOCK_COMMENT_END = re.compile(r"\s*\*+/\s*$")
SHELL_SHEBANG = re.compile(r"^#!.*\b(?:ba)?sh\b")
HASH_LANGUAGE_SUFFIXES = frozenset({".sh", ".bash", ".mk", ".dockerfile", ".yml", ".yaml", ".toml"})
HASH_LANGUAGE_NAMES = frozenset({"Makefile", "GNUmakefile", "Dockerfile"})
JAVASCRIPT_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})
HASH_LINE_TOKEN = re.compile(r"\"(?:\\.|[^\"\\])*\"?|'[^']*'?|\\.|(?:^|(?<=\s))#.*")
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
    return BLOCK_COMMENT_END.sub("", COMMENT_MARKER.sub("", comment, count=1)).strip().lower()


def is_allowed_comment(comment: str, line: int) -> bool:
    if line == 1 and comment.startswith("#!"):
        return True
    if line <= 2 and ENCODING_DECLARATION.search(comment):
        return True
    body = comment_body(comment)
    return body.startswith(REASON_PREFIX) or body.startswith(MACHINE_DIRECTIVE_PREFIXES)


def has_shell_shebang(text: str) -> bool:
    return SHELL_SHEBANG.match(text.partition("\n")[0]) is not None


def is_hash_language(path: PurePosixPath, text: str) -> bool:
    if path.name in HASH_LANGUAGE_NAMES or path.name.startswith("Dockerfile."):
        return True
    if path.suffix in HASH_LANGUAGE_SUFFIXES:
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


def hash_language_findings(text: str) -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in HASH_LINE_TOKEN.finditer(line):
            if match.group().startswith("#"):
                findings.append(Finding(number, number, "comment", match.group()))
    return findings


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
    return hash_language_findings if is_hash_language(pure_path, text) else None


def find_comments_and_docstrings(path: str, text: str) -> list[Finding]:
    finder = language_finder(path, text)
    if finder is None:
        return []
    return [
        finding
        for finding in finder(text)
        if "docstring" in finding.kind or not is_allowed_comment(finding.text, finding.first_line)
    ]
