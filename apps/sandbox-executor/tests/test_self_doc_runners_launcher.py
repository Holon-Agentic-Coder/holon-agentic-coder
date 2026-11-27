from __future__ import annotations

import ast
import dataclasses
import importlib
import io
import re
import tokenize
from pathlib import Path

import pytest

from tests.self_doc_scanner import find_docstrings_and_string_statements, is_allowlisted_comment

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "sandbox_executor"

SLICE_MODULES: tuple[str, ...] = (
    "runners/__init__.py",
    "runners/antigravity.py",
    "runners/claude.py",
    "runners/codex.py",
    "runners/opencode.py",
    "runners/pi.py",
    "runners/spec.py",
    "launcher/run_tracking.py",
    "cli/commands/intent_check.py",
)

_REASON_COMMENT = re.compile(r"^#\s*why:\s*\S")
_FORMATTER_DIRECTIVE = re.compile(r"^#\s*fmt:\s*(off|on|skip)\s*$")


def _is_allowed_slice_comment(line_number: int, text: str, column: int) -> bool:
    return (
        is_allowlisted_comment(line_number, text, column)
        or bool(_REASON_COMMENT.match(text))
        or bool(_FORMATTER_DIRECTIVE.match(text))
    )


def _is_disallowed_slice_comment(token: tokenize.TokenInfo) -> bool:
    line_number, column = token.start
    return token.type == tokenize.COMMENT and not _is_allowed_slice_comment(line_number, token.string, column)


def _disallowed_slice_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if _is_disallowed_slice_comment(token)
    ]


def _source_of(module: str) -> str:
    return (PACKAGE_DIR / module).read_text(encoding="utf-8")


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_exists(module: str) -> None:
    assert (PACKAGE_DIR / module).is_file()


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_has_no_docstrings(module: str) -> None:
    assert find_docstrings_and_string_statements(_source_of(module)) == []


@pytest.mark.parametrize("module", SLICE_MODULES)
def test_slice_module_has_only_reason_or_directive_comments(module: str) -> None:
    assert _disallowed_slice_comments(_source_of(module)) == []


def test_runners_package_exports_are_unchanged() -> None:
    runners = importlib.import_module("sandbox_executor.runners")
    assert runners.__all__ == ["RUNNER_SPECS", "RunnerSpec", "runner_spec_for"]
    assert all(hasattr(runners, name) for name in runners.__all__)
    assert set(runners.RUNNER_SPECS) == {"antigravity", "claude", "pi", "codex", "opencode"}


def _public_module_level_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def test_runner_spec_module_public_names_are_unchanged() -> None:
    assert _public_module_level_names(_source_of("runners/spec.py")) == {
        "RunnerSpec",
        "has_api_key",
        "any_path_exists",
        "generic_missing_message",
    }
    spec_module = importlib.import_module("sandbox_executor.runners.spec")
    field_names = tuple(field.name for field in dataclasses.fields(spec_module.RunnerSpec))
    assert field_names == ("agent_id", "image", "default_model", "ticker", "api_key_env_vars", "credential_check")
    assert callable(spec_module.RunnerSpec.check_credentials)


def test_guard_reports_an_inserted_module_docstring() -> None:
    assert find_docstrings_and_string_statements('"""Explain the module."""\nx = 1\n') != []


def test_guard_reports_a_descriptive_comment() -> None:
    assert _disallowed_slice_comments("# Explain the next line\nx = 1\n") == [(1, "# Explain the next line")]


def test_guard_accepts_reasons_and_tool_directives() -> None:
    source = "# why: docker rejects longer names\nx = 1  # noqa: E501\n# fmt: off\ny = 2\n# fmt: on\n"
    assert _disallowed_slice_comments(source) == []


def test_guard_rejects_an_empty_reason() -> None:
    assert _disallowed_slice_comments("x = 1  # why:\n") == [(1, "# why:")]


def test_guard_reports_a_sphinx_attribute_comment() -> None:
    assert _disallowed_slice_comments("#: attribute doc\nx = 1\n") == [(1, "#: attribute doc")]
