"""Guard: tests patch flow names at the submodule that looks them up, never at the package re-export.

A patch of ``sandbox_executor.flow.<name>`` replaces only the package attribute, which no flow submodule reads, so it
silently intercepts nothing. The one exception is a name that a caller outside the package imports from
``sandbox_executor.flow`` inside a function body: that caller reads the package attribute at call time.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TESTS_DIR.parent / "src" / "sandbox_executor"
FLOW_DIR = SRC_DIR / "flow"

PATCH_TARGET = re.compile(r'"sandbox_executor\.flow\.(?P<head>\w+)(?P<rest>[\w.]*)')
PACKAGE_OBJECT_PATCH = re.compile(r"(?:setattr|patch\.object)\(\s*(?:flow|flow_module)(?:\.\w+)?,")

# Quoted text that names a flow function without patching it (a prompt invariant, not a mock target).
NOT_A_PATCH = frozenset({'"sandbox_executor.flow.record_review_action()'})


def _flow_submodule_names() -> set[str]:
    return {path.stem for path in FLOW_DIR.iterdir() if path.suffix == ".py" or (path / "__init__.py").exists()}


def _lazily_imported_from_package() -> set[str]:
    """Names that modules outside the flow package import from ``sandbox_executor.flow`` inside a function body."""
    names: set[str] = set()
    for path in SRC_DIR.rglob("*.py"):
        if FLOW_DIR in path.parents:
            continue
        for func in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.ImportFrom) and node.module == "sandbox_executor.flow":
                    names.update(alias.name for alias in node.names)
    return names


def _test_sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(TESTS_DIR).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if path != Path(__file__).resolve()
    ]


def test_no_test_patches_a_flow_name_at_the_package_reexport():
    submodules = _flow_submodule_names()
    allowed = _lazily_imported_from_package()
    offenders = []
    for rel_path, source in _test_sources():
        for match in PATCH_TARGET.finditer(source):
            head = match.group("head")
            if head in submodules or head in allowed:
                continue
            if any(source.startswith(text, match.start()) for text in NOT_A_PATCH):
                continue
            line = source.count("\n", 0, match.start()) + 1
            offenders.append(f"{rel_path}:{line}: sandbox_executor.flow.{head}{match.group('rest')}")
    assert offenders == [], "patch the flow submodule that looks the name up, not the package:\n" + "\n".join(offenders)


def test_no_test_patches_an_attribute_of_the_package_object():
    offenders = [
        f"{rel_path}:{source.count(chr(10), 0, match.start()) + 1}"
        for rel_path, source in _test_sources()
        for match in PACKAGE_OBJECT_PATCH.finditer(source)
    ]
    assert offenders == [], f"use a dotted submodule target instead of patching the flow package object: {offenders}"


def test_the_lazy_package_lookups_are_real_flow_exports():
    import sandbox_executor.flow as flow

    assert _lazily_imported_from_package() <= set(flow.__all__)
