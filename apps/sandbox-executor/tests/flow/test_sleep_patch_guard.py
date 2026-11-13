"""Fail when a test patches ``time.sleep`` process-wide instead of using ``scoped_sleep_patch``."""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
EXEMPT = {Path(__file__).resolve(), Path(__file__).with_name("sleep_patch.py").resolve()}
PATCH_NAMES = {"patch", "setattr"}


def _is_sleep_string(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == "time.sleep" or node.value.endswith(".time.sleep"))
    )


def _is_time_sleep_object_patch(call: ast.Call) -> bool:
    """``patch.object(time, "sleep")`` or ``monkeypatch.setattr(time, "sleep", ...)``."""
    args = call.args
    return (
        len(args) >= 2
        and isinstance(args[0], ast.Name)
        and args[0].id == "time"
        and isinstance(args[1], ast.Constant)
        and args[1].value == "sleep"
    )


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def find_violations(source: str) -> list[int]:
    """Return the line numbers of process-wide ``time.sleep`` patches in ``source``."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _call_name(node) not in PATCH_NAMES | {"object"}:
            continue
        if (node.args and _is_sleep_string(node.args[0])) or _is_time_sleep_object_patch(node):
            lines.append(node.lineno)
    return lines


def test_no_test_patches_time_sleep_process_wide():
    offences = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.resolve() in EXEMPT:
            continue
        offences += [f"{path.relative_to(TESTS_ROOT)}:{line}" for line in find_violations(path.read_text())]
    assert not offences, (
        "process-wide time.sleep patch; use tests.flow.sleep_patch.scoped_sleep_patch instead: " + ", ".join(offences)
    )


def test_guard_flags_dotted_and_object_patches():
    source = (
        "from unittest.mock import patch\n"
        "patch('pkg.mod.time.sleep', s)\n"
        "patch.object(time, 'sleep')\n"
        "monkeypatch.setattr(time, 'sleep', f)\n"
    )
    assert find_violations(source) == [2, 3, 4]


def test_guard_ignores_clean_source():
    source = (
        "patch('pkg.mod.time.monotonic')\n"
        "patch('pkg.mod.sleep', s)\n"
        "monkeypatch.setattr(time, 'perf_counter', f)\n"
        "with scoped_sleep_patch('pkg.mod') as s: pass\n"
    )
    assert find_violations(source) == []
