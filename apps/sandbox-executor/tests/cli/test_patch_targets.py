"""Guard: tests patch CLI submodule-local symbols at their owning submodule, never on ``sandbox_executor.cli``.

When a function moves from ``cli/__init__.py`` into ``cli/<module>.py``, any ``patch("sandbox_executor.cli.<name>")``
or ``patch.object(cli, "<name>")`` silently patches only the re-export on the package object and never intercepts
lookups inside ``cli/<module>.py``. Names intentionally accessed via ``cli.<name>`` by ``cli/commands/*.py`` remain
valid package-level patch targets.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TESTS_DIR.parent / "src" / "sandbox_executor"
CLI_DIR = SRC_DIR / "cli"
COMMANDS_DIR = CLI_DIR / "commands"
MAX_CLI_SUBMODULE_LINES = 200


def _module_local_names(path: Path) -> set[str]:
    """Names bound at module level in ``path`` and loaded inside its top-level definitions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound: set[str] = set()
    defs: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
            defs.append(node)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
    referenced = {
        inner.id
        for definition in defs
        for inner in ast.walk(definition)
        if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
    }
    return bound & referenced


def _submodule_local_symbols() -> dict[str, set[str]]:
    """Map each symbol resolved inside a direct ``cli/*.py`` submodule to its owning submodule name(s)."""
    owners: dict[str, set[str]] = {}
    for path in sorted(CLI_DIR.glob("*.py")):
        if path.name in ("__init__.py", "__main__.py"):
            continue
        for name in _module_local_names(path):
            owners.setdefault(name, set()).add(f"sandbox_executor.cli.{path.stem}")
    return owners


def _command_dispatched_cli_attrs() -> set[str]:
    """Names accessed through ``cli.<attr>`` inside ``sandbox_executor/cli/commands/*.py``."""
    attrs: set[str] = set()
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "cli":
                attrs.add(node.attr)
    return attrs


def _stale_cli_patch_targets_in_source(
    source: str,
    rel_path: str,
    forbidden: dict[str, set[str]],
) -> list[str]:
    """Return formatted violation strings for stale ``sandbox_executor.cli.<name>`` patch calls in ``source``."""
    tree = ast.parse(source, filename=rel_path)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # patch("sandbox_executor.cli.<name>", ...)
        if (
            (isinstance(func, ast.Name) and func.id == "patch")
            or (isinstance(func, ast.Attribute) and func.attr == "patch")
        ) and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                prefix = "sandbox_executor.cli."
                if arg0.value.startswith(prefix):
                    rest = arg0.value[len(prefix) :]
                    head = rest.split(".", 1)[0]
                    if head in forbidden:
                        targets = ", ".join(sorted(forbidden[head]))
                        offenders.append(f"{rel_path}:{node.lineno}: {arg0.value} -> patch {targets}")
        # patch.object(cli, "<name>", ...) or monkeypatch.setattr(cli, "<name>", ...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr in ("object", "setattr")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "cli"
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            name = node.args[1].value
            if name in forbidden:
                targets = ", ".join(sorted(forbidden[name]))
                offenders.append(f"{rel_path}:{node.lineno}: cli.{name} -> patch {targets}")
    return offenders


def _forbidden_package_patch_names() -> dict[str, set[str]]:
    local_symbols = _submodule_local_symbols()
    dispatched = _command_dispatched_cli_attrs()
    return {name: mods for name, mods in local_symbols.items() if name not in dispatched}


def test_no_test_patches_submodule_local_cli_symbol_on_package():
    forbidden = _forbidden_package_patch_names()
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() == this_file:
            continue
        rel_path = path.relative_to(TESTS_DIR).as_posix()
        offenders.extend(_stale_cli_patch_targets_in_source(path.read_text(encoding="utf-8"), rel_path, forbidden))
    assert offenders == [], "patch the owning cli submodule instead of sandbox_executor.cli:\n" + "\n".join(offenders)


def test_guard_catches_synthetic_stale_cli_patches():
    forbidden = _forbidden_package_patch_names()
    synthetic = "\n".join(
        [
            'patch("sandbox_executor.cli.run_preflight_probe")',
            'patch.object(cli, "build_parser")',
            'monkeypatch.setattr(cli, "_configuration_section", fake)',
        ]
    )
    hits = _stale_cli_patch_targets_in_source(synthetic, "synthetic.py", forbidden)
    assert len(hits) == 3
    assert any("run_preflight_probe" in h for h in hits)
    assert any("build_parser" in h for h in hits)
    assert any("_configuration_section" in h for h in hits)


def test_guard_allows_command_dispatched_cli_patches():
    forbidden = _forbidden_package_patch_names()
    valid = "\n".join(
        [
            'patch("sandbox_executor.cli.run_flow_pipeline")',
            'patch("sandbox_executor.cli.dispatch_role_agent")',
            'patch.object(cli, "detect_holon_coherence")',
        ]
    )
    assert _stale_cli_patch_targets_in_source(valid, "valid.py", forbidden) == []


def test_cli_init_contains_no_definitions_and_submodules_respect_line_budget():
    init_tree = ast.parse((CLI_DIR / "__init__.py").read_text(encoding="utf-8"))
    defs = [
        node.name for node in init_tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    assert defs == [], f"cli/__init__.py must be a pure re-export facade; found definitions: {defs}"

    oversized = []
    for path in sorted(CLI_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_CLI_SUBMODULE_LINES:
            oversized.append(f"{path.name}: {line_count} lines > {MAX_CLI_SUBMODULE_LINES}")
    assert oversized == [], f"CLI submodules exceed {MAX_CLI_SUBMODULE_LINES}-line budget: {oversized}"
