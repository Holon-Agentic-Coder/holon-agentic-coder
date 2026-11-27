from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TESTS_DIR.parent / "src" / "sandbox_executor"
FLOW_DIR = SRC_DIR / "flow"

PATCH_TARGET = re.compile(r'"sandbox_executor\.flow\.(?P<head>\w+)(?P<rest>[\w.]*)')
PACKAGE_OBJECT_PATCH = re.compile(r"(?:setattr|patch\.object)\(\s*(?:flow|flow_module)(?:\.\w+)?,")

QUOTED_FLOW_NAMES_THAT_ARE_NOT_MOCK_TARGETS = frozenset({'"sandbox_executor.flow.record_review_action()'})


def _flow_submodule_names() -> set[str]:
    return {path.stem for path in FLOW_DIR.iterdir() if path.suffix == ".py" or (path / "__init__.py").exists()}


def _names_imported_from_flow_package_inside_functions_outside_flow() -> set[str]:
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
    # why: a package-attribute patch intercepts nothing, as flow submodules never read sandbox_executor.flow.<name>
    submodules = _flow_submodule_names()
    allowed = _names_imported_from_flow_package_inside_functions_outside_flow()
    offenders = []
    for rel_path, source in _test_sources():
        for match in PATCH_TARGET.finditer(source):
            head = match.group("head")
            if head in submodules or head in allowed:
                continue
            if any(source.startswith(text, match.start()) for text in QUOTED_FLOW_NAMES_THAT_ARE_NOT_MOCK_TARGETS):
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

    assert _names_imported_from_flow_package_inside_functions_outside_flow() <= set(flow.__all__)
