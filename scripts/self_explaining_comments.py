from __future__ import annotations

import ast
import io
import re
import textwrap
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
MARKDOWN_FENCE_ROOTS = frozenset({"plans", "executions"})
FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*(.*)$")
FENCE_CLOSE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")
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


MARKDOWN_FENCE_FINDERS: dict[str, Callable[[str], list[Finding]]] = {
    "python": python_findings,
    "py": python_findings,
    "python3": python_findings,
    "sh": shell_findings,
    "bash": shell_findings,
    "shell": shell_findings,
    "zsh": shell_findings,
    "yaml": yaml_findings,
    "yml": yaml_findings,
}


def is_violation(finding: Finding) -> bool:
    return "docstring" in finding.kind or not is_allowed_comment(finding.text, finding.first_line)


def is_checked_markdown(path: PurePosixPath) -> bool:
    return path.suffix.lower() == ".md" and bool(path.parts) and path.parts[0] in MARKDOWN_FENCE_ROOTS


def parse_fence_open(line: str) -> tuple[str, int, str] | None:
    match = FENCE_OPEN.match(line)
    if match is None:
        return None
    fence, info = match.group(1), match.group(2)
    if fence[0] == "`" and "`" in info:
        return None
    stripped = info.strip()
    language = stripped.split(maxsplit=1)[0].lower() if stripped else ""
    return fence[0], len(fence), language


def is_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
    match = FENCE_CLOSE.match(line)
    return match is not None and match.group(1)[0] == fence_char and len(match.group(1)) >= fence_len


def join_block_lines(lines: list[str]) -> str:
    return "\n".join(lines) + "\n" if lines else ""


def iter_fenced_blocks(text: str) -> Iterable[tuple[int, str, str]]:
    active: tuple[str, int, int, str] | None = None
    body_lines: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if active is None:
            opened = parse_fence_open(line)
            if opened is not None:
                active = (opened[0], opened[1], line_number, opened[2])
                body_lines = []
            continue
        if is_fence_close(line, active[0], active[1]):
            yield active[2], active[3], join_block_lines(body_lines)
            active = None
            body_lines = []
        else:
            body_lines.append(line)
    if active is not None:
        yield active[2], active[3], join_block_lines(body_lines)


def markdown_fenced_findings(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for open_line, language, block_text in iter_fenced_blocks(text):
        finder = MARKDOWN_FENCE_FINDERS.get(language)
        if finder is None:
            continue
        dedented = textwrap.dedent(block_text)
        for finding in finder(dedented):
            if is_violation(finding):
                findings.append(
                    Finding(
                        finding.first_line + open_line,
                        finding.last_line + open_line,
                        finding.kind,
                        finding.text,
                    )
                )
    return findings


SUFFIX_FINDERS: dict[str, Callable[[str], list[Finding]]] = {
    ".py": python_findings,
    ".toml": toml_findings,
    ".mk": makefile_findings,
    **dict.fromkeys(JAVASCRIPT_SUFFIXES, javascript_findings),
    **dict.fromkeys(YAML_SUFFIXES, yaml_findings),
}


def language_finder(path: str, text: str) -> Callable[[str], list[Finding]] | None:
    pure_path = PurePosixPath(path)
    if is_checked_markdown(pure_path):
        return markdown_fenced_findings
    if pure_path.suffix in SUFFIX_FINDERS:
        return SUFFIX_FINDERS[pure_path.suffix]
    if pure_path.name in MAKEFILE_NAMES:
        return makefile_findings
    return shell_findings if is_shell_like(pure_path, text) else None


def find_comments_and_docstrings(path: str, text: str) -> list[Finding]:
    finder = language_finder(path, text)
    if finder is None:
        return []
    return [finding for finding in finder(text) if is_violation(finding)]
