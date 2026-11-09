"""Helpers for the flow.py move proof: definition digests, the package parser and the committed snapshot."""

from __future__ import annotations

import ast
import functools
import hashlib
import json
from pathlib import Path

FLOW_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "src" / "sandbox_executor" / "flow"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "flow_py_definitions.json"

# Package plumbing that flow.py did not have (or had once, at module level): the per-module loggers, the re-export
# list and the late binding of PipelineEngine into stages.children (stages.decomposition before the child loop moved
# there). Excluded on both sides of the comparison.
PLUMBING_KEYS = frozenset({"<const>:logger", "<const>:__all__", "<const>:_children.PipelineEngine"})
LOGGER_SOURCE = "logger = logging.getLogger(__name__)"


def _digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()


def assign_key(node: ast.Assign | ast.AnnAssign) -> str:
    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
    return f"<const>:{ast.unparse(target)}"


def definition_digests(tree: ast.Module) -> dict[str, str]:
    """Map each top-level definition, nested method and module-level assignment to the digest of its ``ast.dump``.

    Keys are qualified names (``FlowContext``, ``FlowContext.to_dict``, ``<const>:LEDGER_REL_PATHS``); plumbing keys
    are left out. A key that appears twice in one module is recorded with a ``#n`` suffix so neither copy is lost.
    """
    digests: dict[str, str] = {}

    def add(key: str, node: ast.AST) -> None:
        if key in PLUMBING_KEYS:
            return
        suffix, n = "", 1
        while key + suffix in digests:
            n += 1
            suffix = f"#{n}"
        digests[key + suffix] = _digest(node)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            add(node.name, node)
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    add(f"{node.name}.{member.name}", member)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            add(assign_key(node), node)
    return digests


def package_modules() -> dict[str, ast.Module]:
    """Parse every module of the flow package from disk, keyed by its path inside the package."""
    return {
        path.relative_to(FLOW_PACKAGE_DIR).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(FLOW_PACKAGE_DIR.rglob("*.py"))
    }


@functools.cache
def package_definitions() -> dict[str, list[tuple[str, str]]]:
    """Map each qualified name to every ``(module path, digest)`` that defines it across the package."""
    found: dict[str, list[tuple[str, str]]] = {}
    for module_path, tree in package_modules().items():
        for key, digest in definition_digests(tree).items():
            found.setdefault(key, []).append((module_path, digest))
    return found


@functools.cache
def load_snapshot() -> dict:
    """Return the committed snapshot of flow.py's definition digests."""
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
