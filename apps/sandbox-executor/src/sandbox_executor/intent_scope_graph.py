# why: intent-check runs before launch, so git is only queried with ls-files and log and nothing is written.
from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

from sandbox_executor.git import is_metadata_path, run_git

CO_CHANGE_COMMIT_LIMIT = 200
CO_CHANGE_THRESHOLD = 3
_COMMIT_SEPARATOR = "\x1e"


def module_name(path: str) -> str | None:
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return None
    parts = list(pure.with_suffix("").parts)
    if "src" in parts:
        parts = parts[len(parts) - parts[::-1].index("src") :]
    elif "tests" in parts:
        parts = parts[parts.index("tests") :]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or None


def is_test_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if pure.suffix != ".py":
        return False
    return pure.name.startswith("test_") or pure.name.endswith("_test.py") or "tests" in pure.parts[:-1]


def python_files(worktree: str) -> list[str]:
    result = run_git(["ls-files", "-z", "--", "*.py"], cwd=worktree, check=False)
    if result.returncode != 0:
        return []
    return sorted(name for name in result.stdout.split("\0") if name)


def build_module_index(files: Iterable[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in files:
        name = module_name(path)
        if name and not is_test_path(path):
            index[name] = path
    return index


def _resolve_from(own: str, is_package: bool, module: str | None, level: int) -> str | None:
    if level == 0:
        return module
    package = own.split(".") if own else []
    if not is_package:
        package = package[:-1]
    if level - 1 > len(package):
        return None
    anchor = package[: len(package) - (level - 1)]
    return ".".join([*anchor, *(module.split(".") if module else [])]) or None


def imported_modules(worktree: str, path: str) -> set[str]:
    try:
        tree = ast.parse(Path(worktree, path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return set()
    own = module_name(path) or ""
    is_package = PurePosixPath(path).name == "__init__.py"
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(own, is_package, node.module, node.level)
            if base:
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _imports_module(imports: Iterable[str], module: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for name in imports)


def importing_tests(imports_by_path: Mapping[str, set[str]], module: str) -> list[str]:
    return sorted(
        path for path, imports in imports_by_path.items() if is_test_path(path) and _imports_module(imports, module)
    )


def _indexed_prefix(index: Mapping[str, str], name: str) -> str | None:
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in index:
            return candidate
    return None


def imported_sources(index: Mapping[str, str], imports: Iterable[str]) -> list[str]:
    resolved = {found for name in imports if (found := _indexed_prefix(index, name))}
    specific = {name for name in resolved if not any(other.startswith(f"{name}.") for other in resolved)}
    return sorted(index[name] for name in specific)


def reexporting_init(worktree: str, path: str) -> str | None:
    pure = PurePosixPath(path)
    module = module_name(path)
    if pure.name == "__init__.py" or module is None:
        return None
    init = (pure.parent / "__init__.py").as_posix()
    if not Path(worktree, init).is_file():
        return None
    return init if module in imported_modules(worktree, init) else None


def _commit_file_sets(log_output: str) -> list[set[str]]:
    chunks = log_output.split(_COMMIT_SEPARATOR)
    return [files for chunk in chunks if (files := {line.strip() for line in chunk.splitlines() if line.strip()})]


def _pair_counts(commits: Iterable[set[str]], listed: set[str]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for files in commits:
        for source in files & listed:
            counts.update((partner, source) for partner in files - listed)
    return counts


def co_changed(
    worktree: str,
    listed: Iterable[str],
    limit: int = CO_CHANGE_COMMIT_LIMIT,
    threshold: int = CO_CHANGE_THRESHOLD,
) -> dict[str, tuple[int, str]]:
    args = ["-c", "core.quotePath=false", "log", "-n", str(limit), "--no-merges", "--no-renames", "--name-only"]
    result = run_git([*args, "--format=%x1e"], cwd=worktree, check=False)
    if result.returncode != 0:
        return {}
    counts = _pair_counts(_commit_file_sets(result.stdout), set(listed))
    best: dict[str, tuple[int, str]] = {}
    for (partner, source), count in sorted(counts.items()):
        if count < threshold or is_metadata_path(partner) or not Path(worktree, partner).exists():
            continue
        if count > best.get(partner, (0, ""))[0]:
            best[partner] = (count, source)
    return best
