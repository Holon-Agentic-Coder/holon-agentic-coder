"""Hermetic AST import guard tests enforcing that container entrypoints do not import host orchestration."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
ENTRYPOINT_DIR = SRC_DIR / "sandbox_executor" / "entrypoint"
ENTRYPOINT_PACKAGE = "sandbox_executor.entrypoint"

FORBIDDEN_MODULES = (
    "sandbox_executor.flow.pipeline",
    "sandbox_executor.flow.stages",
    "sandbox_executor.cli",
)


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    """The absolute module an ``ImportFrom`` names, resolving a relative one against ``package``."""
    if not node.level:
        return node.module or ""
    base = package.split(".")
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _collect_module_imports(source: str, package: str = ENTRYPOINT_PACKAGE) -> list[tuple[int, str]]:
    """Return all imported module paths found in Python source code, relative imports resolved against package."""
    tree = ast.parse(source)
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = _absolute_module(node, package)
            imports.append((node.lineno, mod))
            for alias in node.names:
                imports.append((node.lineno, f"{mod}.{alias.name}" if mod else alias.name))
    return imports


def test_entrypoints_do_not_import_host_orchestration_or_package_root() -> None:
    """Container entrypoints must remain decoupled from host orchestration (flow.pipeline, flow.stages, cli)."""
    assert ENTRYPOINT_DIR.is_dir(), f"Entrypoint directory not found at {ENTRYPOINT_DIR}"
    py_files = sorted(ENTRYPOINT_DIR.glob("*.py"))
    assert py_files, f"No python files found under {ENTRYPOINT_DIR}"

    violations: list[str] = []
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        imports = _collect_module_imports(content)
        for lineno, imported_path in imports:
            if imported_path in FORBIDDEN_MODULES or imported_path.startswith(
                tuple(f"{module}." for module in FORBIDDEN_MODULES)
            ):
                violations.append(f"{py_file.name}:{lineno} forbidden import '{imported_path}'")
            elif imported_path == "sandbox_executor.flow":
                # Entrypoints must import specific domain modules, not the full flow package
                violations.append(f"{py_file.name}:{lineno} forbidden root package import '{imported_path}'")

    assert not violations, "Found forbidden orchestration imports in container entrypoints:\n" + "\n".join(violations)


def test_ast_guard_detects_forbidden_imports() -> None:
    """Verify that the AST collector correctly flags forbidden imports."""
    forbidden_sample = (
        "import sandbox_executor.cli\n"
        "from sandbox_executor.flow.pipeline import run_flow_pipeline\n"
        "from sandbox_executor.flow.stages.execute import run_execute_stage\n"
        "from sandbox_executor.flow import check_admission\n"
    )
    imports = [imp for _, imp in _collect_module_imports(forbidden_sample)]
    assert "sandbox_executor.cli" in imports
    assert "sandbox_executor.flow.pipeline" in imports
    assert "sandbox_executor.flow.stages.execute" in imports
    assert "sandbox_executor.flow" in imports


def test_ast_guard_resolves_relative_imports() -> None:
    relative_sample = "from ..flow import pipeline\nfrom ..flow.stages import execute\nfrom . import planner\n"
    imports = [imp for _, imp in _collect_module_imports(relative_sample)]
    assert "sandbox_executor.flow.pipeline" in imports
    assert "sandbox_executor.flow.stages" in imports
    assert "sandbox_executor.entrypoint.planner" in imports


# Modules an entrypoint imports lazily (inside a function) are not loaded by importing the entrypoint itself.
LAZILY_IMPORTED = (
    "sandbox_executor.flow.intent_questions",
    "sandbox_executor.flow.partial_head",
    "sandbox_executor.flow.stage_conflicts",
)


@pytest.mark.parametrize(
    "module",
    sorted(
        [f"{ENTRYPOINT_PACKAGE}.{path.stem}" for path in ENTRYPOINT_DIR.glob("*.py") if path.stem != "__init__"]
        + list(LAZILY_IMPORTED)
    ),
)
def test_importing_an_entrypoint_loads_no_host_orchestration(module: str) -> None:
    """At runtime too: package ``__init__`` side effects must not drag the host engine into an entrypoint."""
    probe = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        f"print('\\n'.join(sorted(n for n in sys.modules if n.startswith({FORBIDDEN_MODULES!r}))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=SRC_DIR, capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.split() == []
