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


def _entrypoint_sources() -> list[Path]:
    """Every entrypoint source file, those of entrypoint subpackages (such as ``executor/``) included."""
    return sorted(path for path in ENTRYPOINT_DIR.rglob("*.py") if "__pycache__" not in path.parts)


def _owning_package(py_file: Path) -> str:
    """The package a source file's relative imports resolve against: its own directory (``__init__`` included)."""
    return ".".join(py_file.parent.relative_to(SRC_DIR).parts)


def _entrypoint_module_names() -> list[str]:
    """The importable entrypoint modules, without the entrypoint package itself and without ``__main__`` modules."""
    names: list[str] = []
    for path in _entrypoint_sources():
        if path.stem == "__main__":
            continue  # importing it would run the entrypoint
        module = _owning_package(path) if path.stem == "__init__" else f"{_owning_package(path)}.{path.stem}"
        if module != ENTRYPOINT_PACKAGE:
            names.append(module)
    return names


def test_entrypoint_scan_covers_the_executor_package() -> None:
    """The recursive scan reaches the executor package, so the import guards cannot quietly scan nothing there."""
    scanned = {path.relative_to(ENTRYPOINT_DIR).as_posix() for path in _entrypoint_sources()}
    assert {"executor/__init__.py", "executor/__main__.py", "executor/run.py"} <= scanned
    assert _owning_package(ENTRYPOINT_DIR / "executor" / "run.py") == "sandbox_executor.entrypoint.executor"
    assert _owning_package(ENTRYPOINT_DIR / "planner.py") == ENTRYPOINT_PACKAGE
    modules = _entrypoint_module_names()
    assert "sandbox_executor.entrypoint.executor" in modules
    assert "sandbox_executor.entrypoint.executor.run" in modules
    assert "sandbox_executor.entrypoint.executor.__main__" not in modules


def test_entrypoints_do_not_import_host_orchestration_or_package_root() -> None:
    """Container entrypoints must remain decoupled from host orchestration (flow.pipeline, flow.stages, cli)."""
    assert ENTRYPOINT_DIR.is_dir(), f"Entrypoint directory not found at {ENTRYPOINT_DIR}"
    py_files = _entrypoint_sources()
    assert py_files, f"No python files found under {ENTRYPOINT_DIR}"

    violations: list[str] = []
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        imports = _collect_module_imports(content, _owning_package(py_file))
        rel_path = py_file.relative_to(ENTRYPOINT_DIR).as_posix()
        for lineno, imported_path in imports:
            if imported_path in FORBIDDEN_MODULES or imported_path.startswith(
                tuple(f"{module}." for module in FORBIDDEN_MODULES)
            ):
                violations.append(f"{rel_path}:{lineno} forbidden import '{imported_path}'")
            elif imported_path == "sandbox_executor.flow":
                # Entrypoints must import specific domain modules, not the full flow package
                violations.append(f"{rel_path}:{lineno} forbidden root package import '{imported_path}'")

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
    sorted(_entrypoint_module_names() + list(LAZILY_IMPORTED)),
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
