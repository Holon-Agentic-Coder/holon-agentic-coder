"""The single ``gh`` boundary of the flow engine, enforced over the source tree by AST.

``flow/github/adapter.py`` (with its one spawn in ``adapter_runner.py``) is the only flow module that may build a
``gh`` argv for the PR, CI-check, review, comment, merge and rerun operations, so a future change that spawns one of
them elsewhere fails here instead of bypassing the injected adapter, its proxy environment and the tests' fake. The
review stage, the loop and the GitHub orchestrators must also ask the adapter, not ``shutil.which``, whether ``gh``
exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

import sandbox_executor.flow as flow

FLOW_DIR = Path(flow.__file__).resolve().parent
IN_SCOPE = {
    ("pr", "create"),
    ("pr", "edit"),
    ("pr", "checks"),
    ("pr", "review"),
    ("pr", "merge"),
    ("pr", "view"),
    ("pr", "comment"),
    ("run", "rerun"),
}
ADAPTER_MODULES = {"github/adapter.py", "github/adapter_runner.py"}
# Module -> the in-scope operations it may still spawn itself, each with the reason it is outside the adapter.
ALLOWLIST = {
    # Approval-invalidation comments list and post through their own gh runner; outside this boundary's intent.
    "github/approval_invalidation.py": {("pr", "view"), ("pr", "comment")},
}
NO_WHICH_GH = (
    "review/loop.py",
    "stages/review.py",
    "github/ci_final_head.py",
    "github/pr.py",
    "github/ci.py",
    "github/review_post.py",
)


def gh_operations(source: str) -> set[tuple[str, ...]]:
    """The operations of every list or tuple literal starting with ``"gh"``: its next two string constants."""
    operations: set[tuple[str, ...]] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.List | ast.Tuple) or not node.elts:
            continue
        words = []
        for element in node.elts:
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                break
            words.append(element.value)
        if words[:1] == ["gh"]:
            operations.add(tuple(words[1:3]))
    return operations


def calls_which_gh(source: str) -> bool:
    """Whether the source calls ``<anything>.which("gh")`` or ``which("gh")``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name == "which" and node.args[0].value == "gh":
                return True
    return False


def imports_subprocess(source: str) -> bool:
    """Whether the module imports ``subprocess`` at any level."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(alias.name == "subprocess" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            return True
    return False


def imports_spawn_gh(source: str) -> bool:
    """Whether the module imports the single gh spawn, ``spawn_gh``, or reaches it as a module attribute."""
    return any(
        (isinstance(node, ast.ImportFrom) and any(alias.name == "spawn_gh" for alias in node.names))
        or (isinstance(node, ast.Attribute) and node.attr == "spawn_gh")
        for node in ast.walk(ast.parse(source))
    )


def _read(relative: str) -> str:
    return (FLOW_DIR / relative).read_text(encoding="utf-8")


def test_the_detector_reports_an_in_scope_argv_and_ignores_generic_spawns():
    source = 'run(["gh", "pr", "review", n, "--approve"])\nrun(("gh", "run", "rerun", i))\nrun(["gh", *args])'
    assert gh_operations(source) == {("pr", "review"), ("run", "rerun"), ()}
    assert calls_which_gh('shutil.which("gh")') and not calls_which_gh('shutil.which("git")')
    assert imports_subprocess("import os, subprocess") and not imports_subprocess("import shutil")
    assert imports_spawn_gh("from sandbox_executor.flow.github.adapter_runner import GhResult, spawn_gh")
    assert imports_spawn_gh("from pkg import adapter_runner\nadapter_runner.spawn_gh(run, [], '.', 1, {})")
    assert not imports_spawn_gh('from x import GhResult\nEXPORTS = {"x": "GhResult gh_env spawn_gh"}')


def test_only_the_adapter_spawns_the_in_scope_gh_operations():
    violations = {}
    for path in sorted(FLOW_DIR.rglob("*.py")):
        relative = path.relative_to(FLOW_DIR).as_posix()
        if relative in ADAPTER_MODULES:
            continue
        found = (gh_operations(path.read_text(encoding="utf-8")) & IN_SCOPE) - ALLOWLIST.get(relative, set())
        if found:
            violations[relative] = sorted(found)
    assert violations == {}


def test_only_the_adapter_imports_the_gh_spawn():
    """A ``gh``-less argv handed to ``spawn_gh`` is invisible to ``gh_operations``, so any import or use is refused."""
    offenders = [
        relative
        for path in sorted(FLOW_DIR.rglob("*.py"))
        if (relative := path.relative_to(FLOW_DIR).as_posix()) not in ADAPTER_MODULES
        and imports_spawn_gh(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_allowlist_names_only_operations_its_modules_still_spawn():
    for relative, operations in ALLOWLIST.items():
        assert operations <= gh_operations(_read(relative)), relative


def test_the_review_path_asks_the_adapter_whether_gh_exists():
    assert [relative for relative in NO_WHICH_GH if calls_which_gh(_read(relative))] == []
    spawning = [relative for relative in ("review/loop.py", "stages/review.py") if imports_subprocess(_read(relative))]
    assert spawning == []
