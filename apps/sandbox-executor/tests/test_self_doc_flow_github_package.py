import ast
import io
import tokenize
from pathlib import Path

import pytest

from scripts.self_explaining_comments import is_allowed_comment

GITHUB_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor" / "flow" / "github"
SCOPED_MODULES = (
    "__init__.py",
    "ci.py",
    "ci_failure_detail.py",
    "ci_head_reruns.py",
    "ci_policy.py",
    "ci_slowest_tests.py",
)
DOCUMENTABLE_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstrings_in(source: str) -> list[tuple[int, str]]:
    return [
        (getattr(node, "lineno", 1), getattr(node, "name", "<module>"))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, DOCUMENTABLE_NODES) and ast.get_docstring(node, clean=False) is not None
    ]


def disallowed_comments_in(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not is_allowed_comment(token.string, token.start[0])
    ]


def scoped_source(module: str) -> str:
    return (GITHUB_PACKAGE_DIR / module).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", SCOPED_MODULES)
def test_scoped_module_has_no_docstring(module: str) -> None:
    assert docstrings_in(scoped_source(module)) == []


@pytest.mark.parametrize("module", SCOPED_MODULES)
def test_scoped_module_has_no_disallowed_comment(module: str) -> None:
    assert disallowed_comments_in(scoped_source(module)) == []


def test_collectors_report_docstrings_and_prose_comments_only() -> None:
    source = (
        "def wait(a, b, c, d, e, f):  # noqa: PLR0913\n"
        '    """Wait for things."""\n'
        "    # prose that restates the code\n"
        "    # why: the gate must not spin\n"
        "    return a\n"
    )
    assert docstrings_in(source) == [(1, "wait")]
    assert disallowed_comments_in(source) == [(3, "# prose that restates the code")]


def test_every_scoped_module_exists() -> None:
    assert [m for m in SCOPED_MODULES if not (GITHUB_PACKAGE_DIR / m).is_file()] == []
