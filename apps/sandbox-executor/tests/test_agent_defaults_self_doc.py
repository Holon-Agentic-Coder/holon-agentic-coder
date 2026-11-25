import ast
import io
import re
import tokenize
from pathlib import Path

TARGET_MODULE = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "agent_defaults.py"

_ENCODING_RE = re.compile(r"^#.*?coding[:=]\s*[-\w.]+")
_PRAGMA_RE = re.compile(
    r"^#\s*(?:"
    r"noqa(?:\s*:\s*[A-Z0-9]+(?:\s*,\s*[A-Z0-9]+)*)?"
    r"|type:\s*ignore(?:\s*\[[^\]]+\])?"
    r"|pragma:\s*no\s+cover"
    r")\s*$"
)


def _is_standalone_string_expr(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def find_docstrings(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    findings: list[tuple[int, str]] = []
    seen_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node, clean=False) is not None
            and node.body
        ):
            lineno = node.body[0].lineno
            seen_lines.add(lineno)
            label = getattr(node, "name", "<module>")
            findings.append((lineno, f"{type(node).__name__}:{label}"))
        if isinstance(node, (ast.Module, ast.ClassDef)):
            for stmt in node.body:
                if _is_standalone_string_expr(stmt) and stmt.lineno not in seen_lines:
                    seen_lines.add(stmt.lineno)
                    findings.append((stmt.lineno, "attribute-docstring"))
    return sorted(findings)


def is_allowed_comment(comment: str, lineno: int) -> bool:
    if lineno == 1 and comment.startswith("#!"):
        return True
    if lineno in (1, 2) and _ENCODING_RE.match(comment):
        return True
    return bool(_PRAGMA_RE.match(comment))


def find_disallowed_comments(source: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok_type, tok_str, (lineno, _), _, _ in tokens:
        if tok_type == tokenize.COMMENT and not is_allowed_comment(tok_str, lineno):
            findings.append((lineno, tok_str))
    return findings


def test_target_module_exists() -> None:
    assert TARGET_MODULE.is_file(), f"Missing target module: {TARGET_MODULE}"


def test_agent_defaults_has_no_docstrings() -> None:
    source = TARGET_MODULE.read_text(encoding="utf-8")
    assert find_docstrings(source) == [], "Unexpected docstrings in agent_defaults.py"


def test_agent_defaults_has_no_disallowed_comments() -> None:
    source = TARGET_MODULE.read_text(encoding="utf-8")
    assert find_disallowed_comments(source) == [], "Unexpected comments in agent_defaults.py"


def test_comment_allowlist_and_docstring_detectors() -> None:
    allowed_source = "\n".join(
        [
            "#!/usr/bin/env python3",
            "# -*- coding: utf-8 -*-",
            "x = 1  # noqa",
            "y = 2  # noqa: E501, F401",
            "z = 3  # type: ignore",
            "w = 4  # type: ignore[arg-type]",
            "if False:  # pragma: no cover",
            "    pass",
        ]
    )
    assert find_disallowed_comments(allowed_source) == []
    assert find_docstrings(allowed_source) == []

    disallowed_source = "\n".join(
        [
            '"""module docstring"""',
            "#!/usr/bin/env python3",
            "# coding: utf-8",
            "#: sphinx attribute comment",
            "# explanatory prose mentioning noqa and pragma",
            "x = 1  # noqa because reasons",
            '"""module attribute docstring"""',
            "class Example:",
            '    """class docstring"""',
            "    value: int = 1",
            '    """attribute docstring"""',
            "    def method(self) -> None:",
            '        """method docstring"""',
            "        return None",
            "async def async_fn() -> None:",
            '    """async docstring"""',
            "    return None",
        ]
    )
    disallowed_comments = find_disallowed_comments(disallowed_source)
    assert [line for line, _ in disallowed_comments] == [2, 3, 4, 5, 6]
    found_docstrings = find_docstrings(disallowed_source)
    assert [line for line, _ in found_docstrings] == [1, 7, 9, 11, 13, 16]
