"""Which test modules the hermetic canary's child run re-runs: the canary selection rule.

The two stress tests used to re-run the whole unit selection each. Run concurrently on the 4-vCPU
``ubuntu-latest`` runner they shared the CPUs, so each child roughly doubled in time and the job
barely got faster. This module narrows the *canary* child only; the workspace-survival child keeps
running the whole unit selection.

Why the survival child stays whole: whether a test can reach the workspace is not decidable from its
own source. A test can call production code that resolves ``get_workspace_dir()`` several imports
away, and following imports transitively selects about 88 % of the suite's time, which saves
nothing. The survival child runs every module under the same layout as the canary (a throwaway
``HOME`` whose ``.holon-sandbox/workspace`` is the workspace), so a test that reaches the workspace
by any route, direct or indirect, still trips the survival checks. Nothing in the suite goes
unchecked.

The rule for the canary child. A test module (``test_*.py`` under the suite directory) is selected
when either holds:

1. its source contains a :data:`HAZARD_TOKENS` entry: it resolves ``HOME`` or the workspace itself,
   or it deletes directories; or
2. it directly references a *hazard module*: an ``import``/``from ... import`` of it, or a string
   naming it (a ``mock.patch`` target, a ``python -m`` argument). A hazard module is a non-test
   module of the production package or of the suite's own helpers whose source contains a
   :data:`HAZARD_TOKENS` entry. ``conftest.py`` files are not hazard modules: they apply to every
   test, and a selected module runs under them anyway.

The rule is computed when the canary runs, never hand-curated, so a new test module is covered the
moment it matches. :func:`canary_selection` fails rather than return an empty selection.
``test_stress_selection.py`` shows that each canary hazard the negative controls use, written as a
test module, is selected, and that a hermetic module is not.
"""

import ast
import os
from collections.abc import Iterable
from pathlib import Path

#: Source fragments that show a module resolves ``HOME`` or the executor workspace, or deletes a
#: directory tree: the two halves of the workspace-deletion incident.
HAZARD_TOKENS = (
    "get_workspace_dir",
    "cleanup_repo_dir",
    "rmtree",
    "expanduser",
    "Path.home",
    '"HOME"',
    "'HOME'",
    "holon-sandbox",
    "HOLON_TARGET_WORKTREE",
)

#: Production package root, relative to the repository root.
SOURCE_SUBDIR = os.path.join("apps", "sandbox-executor", "src")

#: Import name of the suite's own helper package.
TESTS_PACKAGE = "tests"

#: Directories the child never collects: ``e2e`` is in pytest's ``norecursedirs`` (``pyproject.toml``),
#: which an explicit file argument would bypass.
EXCLUDED_DIRS = frozenset({"e2e", "__pycache__"})


def has_hazard_token(source: str) -> bool:
    """Return whether ``source`` contains a :data:`HAZARD_TOKENS` entry."""
    return any(token in source for token in HAZARD_TOKENS)


def _module_names(root: Path, prefix: str) -> dict[str, Path]:
    names: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        names[".".join([prefix, *parts] if prefix else parts)] = path
    return names


def referenced_names(source: str) -> set[str]:
    """Return every dotted name ``source`` imports, or names as a string, with all its parents.

    ``from a.b import c`` yields ``a``, ``a.b`` and ``a.b.c``, because ``c`` may be a module.
    Relative imports are not resolved; the suite does not use them.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isascii():
            names.add(node.value)
    expanded: set[str] = set()
    for name in names:
        while name:
            expanded.add(name)
            name = name.rpartition(".")[0]
    return expanded


def hazard_modules(source_root: Path, tests_root: Path) -> set[str]:
    """Return the importable names of every non-test module whose source has a hazard token."""
    candidates = _module_names(source_root, "") | _module_names(tests_root, TESTS_PACKAGE)
    return {
        name
        for name, path in candidates.items()
        if not path.name.startswith("test_") and path.name != "conftest.py" and has_hazard_token(path.read_text())
    }


def select_modules(test_files: Iterable[Path], hazards: set[str]) -> list[Path]:
    """Return the test files the rule selects, in the order given."""
    selected = []
    for path in test_files:
        source = path.read_text()
        if has_hazard_token(source) or referenced_names(source) & hazards:
            selected.append(path)
    return selected


def canary_selection(repo_root: str, tests_dir: str) -> list[str]:
    """Return the canary child's test modules, relative to ``repo_root``; never empty.

    Raises:
        RuntimeError: If the rule selects nothing, which would make the canary pass vacuously.
    """
    root = Path(repo_root)
    tests_root = root / tests_dir
    test_files = [
        path
        for path in sorted(tests_root.rglob("test_*.py"))
        if not EXCLUDED_DIRS & set(path.relative_to(tests_root).parts[:-1])
    ]
    hazards = hazard_modules(root / SOURCE_SUBDIR, tests_root)
    selected = [str(path.relative_to(root)) for path in select_modules(test_files, hazards)]
    if not selected:
        raise RuntimeError(f"the canary selection rule selected no test module under {tests_root}")
    return selected
