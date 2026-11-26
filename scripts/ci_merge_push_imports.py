"""The import scan of the merge-push route: which modules a changed file imports, and which changed files relate.

``scripts.ci_merge_push_gate`` pairs every pull-request file with every base-side file under its related roots; a pair
relates when one file imports a module of the other (a dotted prefix counts) or is its ``test_<stem>.py``. Content comes
from the local checkout when its blob id matches, else from at most ``MAX_BLOB_READS`` blob reads. Standard library
only and Python 3.9 syntax, so it runs on the runner's own ``python3``.
"""

from __future__ import annotations

import ast
import hashlib
import os
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from scripts.ci_bookkeeping_api import GateClient, blob_bytes

MAX_BLOB_READS = 40


def module_names(path: str) -> tuple[str, ...]:
    """Return the names ``path`` imports as: after ``src``, from ``scripts`` or ``tests``, bare; ``__init__`` too."""
    parts = path[: -len(".py")].split("/") if path.endswith(".py") else path.split("/")
    if parts[-1:] == ["__init__"]:
        parts = parts[:-1]
    candidates = [parts[-1:]]
    for anchor, offset in (("src", 1), ("tests", 0)):
        if anchor in parts:
            candidates.append(parts[len(parts) - parts[::-1].index(anchor) - 1 + offset :])
    if parts[:1] == ["scripts"]:
        candidates.append(parts)
    return tuple(sorted({".".join(candidate) for candidate in candidates if candidate}))


def _from_modules(node: ast.ImportFrom, packages: Sequence[str]) -> list[str]:
    """Return the absolute module names of a ``from`` import; a relative one resolves against each package."""
    if node.level == 0:
        return [node.module or ""]
    resolved = []
    for package in packages:
        parts = package.split(".") if package else []
        if node.level - 1 <= len(parts):
            prefix = parts[: len(parts) - (node.level - 1)]
            resolved.append(".".join([*prefix, node.module] if node.module else prefix))
    return resolved


def imports_of(path: str, source: bytes) -> frozenset[str] | None:
    """Return the dotted names a file imports (``from a import b`` gives ``a`` and ``a.b``); ``None`` if unparsable."""
    try:
        syntax = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    init = path.endswith("/__init__.py")
    packages = [name if init else name.rpartition(".")[0] for name in module_names(path)]
    names: set[str] = set()
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            for module in _from_modules(node, packages):
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return frozenset(name.strip(".") for name in names if name.strip("."))


def _imports_any(imports: Iterable[str], names: Sequence[str]) -> bool:
    """Return true when an import equals a module name or one is a dotted prefix of the other."""
    return any(i == n or n.startswith(f"{i}.") or i.startswith(f"{n}.") for i in imports for n in names)


def is_test_pair(first: str, second: str) -> bool:
    """Return true when one file is ``test_<stem>.py`` and the other ``<stem>.py``."""
    first_name, second_name = posixpath.basename(first), posixpath.basename(second)
    return first_name == f"test_{second_name}" or second_name == f"test_{first_name}"


def related_pair(
    pr: Sequence[str], base: Sequence[str], imports: Mapping[str, frozenset[str]]
) -> tuple[str, str] | None:
    """Return the first pull-request and base-side file that import or test each other, in either direction."""
    for pr_path in pr:
        for base_path in base:
            if (
                is_test_pair(pr_path, base_path)
                or _imports_any(imports[pr_path], module_names(base_path))
                or _imports_any(imports[base_path], module_names(pr_path))
            ):
                return pr_path, base_path
    return None


@dataclass
class BlobReader:
    """Read content from the local checkout when its blob id matches, else from at most ``MAX_BLOB_READS`` reads."""

    client: GateClient
    reads: int = 0

    def source(self, path: str, sha: str) -> bytes | None:
        """Return the content of blob ``sha`` at ``path``; ``None`` once the blob-read budget is spent."""
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                data = handle.read()
            if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == sha:
                return data
        if self.reads >= MAX_BLOB_READS:
            return None
        self.reads += 1
        return blob_bytes(self.client, sha)
