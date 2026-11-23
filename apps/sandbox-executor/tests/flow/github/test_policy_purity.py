"""The four GitHub policy modules stay I/O-free leaves with no complexity suppressions."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import sandbox_executor.flow.github as github_package

GITHUB_DIR = Path(github_package.__file__).resolve().parent
POLICY_MODULES = ("ci_policy", "mergeability_policy", "review_policy", "pr_metadata")
FORBIDDEN_MODULES = (
    "subprocess",
    "shutil",
    "os",
    "time",
    "socket",
    "sandbox_executor.git",
    "sandbox_executor.flow.context",
)
COMPLEXITY_NOQA = re.compile(r"#\s*noqa\b.*\b(C901|PLR09\d\d)\b", re.IGNORECASE)


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                modules.add(f"sandbox_executor.flow.github.{node.module or ''}".rstrip("."))
            else:
                modules.add(node.module or "")
    return modules


@pytest.mark.parametrize("module", POLICY_MODULES)
def test_policy_module_imports_no_io(module):
    imported = imported_modules(GITHUB_DIR / f"{module}.py")
    forbidden = {
        name for name in imported for banned in FORBIDDEN_MODULES if name == banned or name.startswith(f"{banned}.")
    }
    assert forbidden == set()
    github_imports = {name for name in imported if name.startswith("sandbox_executor.flow.github")}
    assert github_imports <= {f"sandbox_executor.flow.github.{policy}" for policy in POLICY_MODULES}


@pytest.mark.parametrize("module", POLICY_MODULES)
def test_policy_module_has_no_complexity_noqa(module):
    source = (GITHUB_DIR / f"{module}.py").read_text(encoding="utf-8")
    assert [line for line in source.splitlines() if COMPLEXITY_NOQA.search(line)] == []
