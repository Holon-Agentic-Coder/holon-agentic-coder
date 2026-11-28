from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PACKAGE_DIR = SRC_DIR / "sandbox_executor"
ROOT_PACKAGE = "sandbox_executor"

BASELINE_CYCLE_EDGE_COUNTS: dict[tuple[str, str], int] = {
    ("agent_defaults", "claude_credential"): 1,
    ("agent_defaults", "runners"): 1,
    ("calibration", "calibration_self_check"): 2,
    ("calibration_self_check", "calibration"): 1,
    ("claude_credential", "local_llm"): 1,
    ("config_env", "preflight"): 1,
    ("entrypoint", "flow"): 12,
    ("entrypoint", "review_ensemble"): 1,
    ("entrypoint", "review_report"): 1,
    ("entrypoint", "review_target"): 1,
    ("flow", "entrypoint"): 15,
    ("flow", "flow_guard"): 4,
    ("flow", "review_ensemble"): 12,
    ("flow", "review_finding_outcomes"): 2,
    ("flow", "review_report"): 10,
    ("flow", "review_target"): 8,
    ("flow_guard", "flow"): 1,
    ("knowledge", "ledger"): 4,
    ("launcher", "config_env"): 1,
    ("ledger", "knowledge"): 1,
    ("local_llm", "agent_defaults"): 1,
    ("preflight", "config_env"): 1,
    ("preflight", "launcher"): 4,
    ("review_ensemble", "review_target"): 1,
    ("review_finding_outcomes", "review_ensemble"): 1,
    ("review_finding_outcomes", "review_report"): 1,
    ("review_report", "review_ensemble"): 1,
    ("review_report", "review_target"): 1,
    ("review_target", "entrypoint"): 1,
    ("review_target", "flow"): 4,
    ("runners", "claude_credential"): 1,
    ("runners", "local_llm"): 3,
}


def _top_level_node_name(child: Path) -> str | None:
    if child.name.startswith(".") or child.name == "__pycache__":
        return None
    if child.is_dir():
        return child.name if any(child.rglob("*.py")) else None
    if child.is_file() and child.suffix == ".py" and child.name != "__init__.py":
        return child.stem
    return None


def discover_top_level_nodes(package_dir: Path) -> frozenset[str]:
    # why: PEP 420 namespace subpackages such as entrypoint omit __init__.py
    return frozenset(name for child in package_dir.iterdir() if (name := _top_level_node_name(child)) is not None)


def owning_package_name(py_file: Path, src_dir: Path) -> str:
    return ".".join(py_file.parent.relative_to(src_dir).parts)


def resolve_import_from(node: ast.ImportFrom, package: str) -> str:
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    keep = max(0, len(parts) - (node.level - 1))
    anchor = parts[:keep]
    return ".".join([*anchor, node.module] if node.module else anchor)


def extract_imported_targets(source: str, package: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_import_from(node, package)
            if base:
                targets.append((node.lineno, base))
            for alias in node.names:
                if alias.name != "*":
                    full = f"{base}.{alias.name}" if base else alias.name
                    targets.append((node.lineno, full))
    return targets


def target_node(target: str, root_package: str, nodes: frozenset[str]) -> str | None:
    prefix = f"{root_package}."
    if not target.startswith(prefix):
        return None
    segment = target[len(prefix) :].split(".", 1)[0]
    return segment if segment in nodes else None


def _python_files_for_node(package_dir: Path, node: str) -> list[Path]:
    files: list[Path] = []
    subpackage_dir = package_dir / node
    if subpackage_dir.is_dir():
        files.extend(path for path in subpackage_dir.rglob("*.py") if "__pycache__" not in path.parts)
    root_module = package_dir / f"{node}.py"
    if root_module.is_file():
        files.append(root_module)
    return sorted(files)


def _file_cross_node_sites(
    py_file: Path,
    package_dir: Path,
    root_package: str,
    nodes: frozenset[str],
) -> set[tuple[str, int, str]]:
    src_dir = package_dir.parent
    rel_parts = py_file.relative_to(package_dir).parts
    source_node = py_file.stem if len(rel_parts) == 1 else rel_parts[0]
    source_text = py_file.read_text(encoding="utf-8")
    package = owning_package_name(py_file, src_dir)
    rel_path = py_file.relative_to(src_dir).as_posix()
    sites: set[tuple[str, int, str]] = set()
    for lineno, target in extract_imported_targets(source_text, package):
        dst_node = target_node(target, root_package, nodes)
        if dst_node is not None and dst_node != source_node:
            sites.add((rel_path, lineno, dst_node))
    return sites


def collect_node_import_sites(
    package_dir: Path,
    root_package: str = ROOT_PACKAGE,
) -> dict[tuple[str, str], list[str]]:
    nodes = discover_top_level_nodes(package_dir)
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source_node in sorted(nodes):
        for py_file in _python_files_for_node(package_dir, source_node):
            for rel_path, lineno, dst_node in _file_cross_node_sites(py_file, package_dir, root_package, nodes):
                grouped[(source_node, dst_node)].add(f"{rel_path}:{lineno} -> {dst_node}")
    return {edge: sorted(sites) for edge, sites in sorted(grouped.items())}


def _positive_adjacency(edges: dict[tuple[str, str], int]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for (src, dst), count in edges.items():
        if count > 0:
            adjacency[src].add(dst)
    return adjacency


def _reachable_from(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        for neighbor in adjacency.get(node, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                stack.append(neighbor)
    return visited


def _mutual_component(node: str, nodes: set[str], reachability: dict[str, set[str]]) -> frozenset[str]:
    return frozenset(
        other for other in nodes if other == node or (other in reachability[node] and node in reachability[other])
    )


def strongly_connected_components(edges: dict[tuple[str, str], int]) -> list[frozenset[str]]:
    adjacency = _positive_adjacency(edges)
    nodes = {node for src, dsts in adjacency.items() for node in (src, *dsts)}
    reachability = {node: _reachable_from(node, adjacency) for node in nodes}
    seen: set[str] = set()
    components: list[frozenset[str]] = []
    for node in sorted(nodes):
        if node in seen:
            continue
        component = _mutual_component(node, nodes, reachability)
        seen.update(component)
        if len(component) >= 2:
            components.append(component)
    return components


def cycle_edge_counts(edge_counts: dict[tuple[str, str], int]) -> dict[tuple[str, str], int]:
    node_to_component: dict[str, int] = {}
    for index, component in enumerate(strongly_connected_components(edge_counts)):
        for node in component:
            node_to_component[node] = index
    return {
        (src, dst): count
        for (src, dst), count in sorted(edge_counts.items())
        if count > 0 and src in node_to_component and node_to_component.get(dst) == node_to_component[src]
    }


def ratchet_violations(
    actual: dict[tuple[str, str], int],
    baseline: dict[tuple[str, str], int],
) -> list[str]:
    violations: list[str] = []
    for (src, dst), count in sorted(actual.items()):
        allowed = baseline.get((src, dst))
        if allowed is None:
            violations.append(f"new cycle edge {src} -> {dst} ({count} import site(s))")
        elif count > allowed:
            violations.append(f"cycle edge {src} -> {dst} grew from {allowed} to {count}")
    return violations


def _format_violation_report(
    violations: list[str],
    actual: dict[tuple[str, str], int],
    sites: dict[tuple[str, str], list[str]],
) -> str:
    lines = ["Package import-cycle ratchet failed:", *violations, "Offending cycle edge sites:"]
    for edge in sorted(actual):
        for site in sites.get(edge, ()):
            lines.append(f"  {edge[0]} -> {edge[1]}: {site}")
    return "\n".join(lines)


def test_sandbox_executor_package_import_cycle_ratchet() -> None:
    nodes = discover_top_level_nodes(PACKAGE_DIR)
    assert {"entrypoint", "flow", "knowledge", "ledger", "calibration"} <= nodes
    assert {"calibration_self_check", "review_target", "flow_guard"} <= nodes
    sites = collect_node_import_sites(PACKAGE_DIR)
    counts = {edge: len(edge_sites) for edge, edge_sites in sites.items()}
    actual = cycle_edge_counts(counts)
    violations = ratchet_violations(actual, BASELINE_CYCLE_EDGE_COUNTS)
    assert violations == [], _format_violation_report(violations, actual, sites)


def test_acyclic_graph_has_no_cycle_edges_or_violations() -> None:
    edges = {("cli", "flow"): 3, ("flow", "ledger"): 2, ("ledger", "runners"): 1}
    actual = cycle_edge_counts(edges)
    assert actual == {}
    assert ratchet_violations(actual, {}) == []


def test_new_two_node_cycle_fails_ratchet() -> None:
    edges = {("alpha", "beta"): 1, ("beta", "alpha"): 2}
    actual = cycle_edge_counts(edges)
    assert actual == {("alpha", "beta"): 1, ("beta", "alpha"): 2}
    violations = ratchet_violations(actual, {})
    assert len(violations) == 2
    assert "new cycle edge alpha -> beta (1 import site(s))" in violations
    assert "new cycle edge beta -> alpha (2 import site(s))" in violations


def test_new_three_node_cycle_fails_ratchet() -> None:
    edges = {("a", "b"): 1, ("b", "c"): 1, ("c", "a"): 1, ("d", "a"): 4}
    actual = cycle_edge_counts(edges)
    assert actual == {("a", "b"): 1, ("b", "c"): 1, ("c", "a"): 1}
    violations = ratchet_violations(actual, {})
    assert len(violations) == 3


def test_existing_cycle_edge_increase_fails_ratchet() -> None:
    baseline = {("alpha", "beta"): 2, ("beta", "alpha"): 1}
    actual = cycle_edge_counts({("alpha", "beta"): 3, ("beta", "alpha"): 1})
    violations = ratchet_violations(actual, baseline)
    assert violations == ["cycle edge alpha -> beta grew from 2 to 3"]


def test_existing_cycle_equal_edge_counts_passes_ratchet() -> None:
    baseline = {("alpha", "beta"): 2, ("beta", "alpha"): 1}
    actual = cycle_edge_counts({("alpha", "beta"): 2, ("beta", "alpha"): 1})
    assert ratchet_violations(actual, baseline) == []


def test_existing_cycle_reduced_edge_count_passes_ratchet() -> None:
    baseline = {("alpha", "beta"): 3, ("beta", "alpha"): 2}
    actual = cycle_edge_counts({("alpha", "beta"): 1, ("beta", "alpha"): 2})
    assert ratchet_violations(actual, baseline) == []


def test_breaking_existing_cycle_completely_passes_ratchet() -> None:
    baseline = {("alpha", "beta"): 2, ("beta", "alpha"): 1}
    actual = cycle_edge_counts({("alpha", "beta"): 2})
    assert actual == {}
    assert ratchet_violations(actual, baseline) == []


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _cycle_counts(pkg_dir: Path, root_package: str = "demo_pkg") -> dict[tuple[str, str], int]:
    sites = collect_node_import_sites(pkg_dir, root_package=root_package)
    return cycle_edge_counts({edge: len(edge_sites) for edge, edge_sites in sites.items()})


def test_synthetic_package_tree_detects_imports_and_ratchet_transitions(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "src" / "demo_pkg"
    _write_file(pkg_dir / "__init__.py", "")
    _write_file(pkg_dir / "root_mod.py", "VALUE = 1\n")
    _write_file(pkg_dir / "alpha" / "__init__.py", "")
    _write_file(
        pkg_dir / "alpha" / "mod.py",
        "import demo_pkg.alpha.helper\nimport demo_pkg.root_mod\nfrom demo_pkg.beta import sub\n"
        "def lazy():\n    from demo_pkg.beta.sub import fn\n    return fn\n",
    )
    _write_file(pkg_dir / "alpha" / "helper.py", "X = 1\n")
    _write_file(
        pkg_dir / "beta" / "sub.py",
        "from ..alpha import mod\nfrom demo_pkg import alpha\ndef fn():\n    return (mod, alpha)\n",
    )

    assert discover_top_level_nodes(pkg_dir) == frozenset({"alpha", "beta", "root_mod"})
    baseline = _cycle_counts(pkg_dir)
    assert baseline == {("alpha", "beta"): 2, ("beta", "alpha"): 2}
    assert ratchet_violations(baseline, baseline) == []

    _write_file(pkg_dir / "alpha" / "extra.py", "import demo_pkg.beta.sub\n")
    assert ratchet_violations(_cycle_counts(pkg_dir), baseline) == ["cycle edge alpha -> beta grew from 2 to 3"]

    (pkg_dir / "alpha" / "extra.py").unlink()
    _write_file(pkg_dir / "beta" / "sub.py", "def fn():\n    return 1\n")
    broken = _cycle_counts(pkg_dir)
    assert broken == {}
    assert ratchet_violations(broken, baseline) == []


def test_synthetic_root_subpackage_and_root_root_cycle_ratchet(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "src" / "demo_pkg"
    _write_file(pkg_dir / "__init__.py", "")
    _write_file(pkg_dir / "sub" / "mod.py", "import demo_pkg.root_a\n")
    _write_file(pkg_dir / "root_a.py", "import demo_pkg.root_b\n")
    _write_file(pkg_dir / "root_b.py", "VALUE = 1\n")
    assert discover_top_level_nodes(pkg_dir) == frozenset({"sub", "root_a", "root_b"})
    assert _cycle_counts(pkg_dir) == {}
    assert ratchet_violations({}, {}) == []

    _write_file(pkg_dir / "root_a.py", "import demo_pkg.root_b\nimport demo_pkg.sub.mod\nfrom .sub import mod\n")
    root_sub = _cycle_counts(pkg_dir)
    assert root_sub == {("root_a", "sub"): 2, ("sub", "root_a"): 1}
    assert len(ratchet_violations(root_sub, {})) == 2

    _write_file(pkg_dir / "root_b.py", "from . import root_a\n")
    combined = _cycle_counts(pkg_dir)
    assert combined == {("root_a", "root_b"): 1, ("root_a", "sub"): 2, ("root_b", "root_a"): 1, ("sub", "root_a"): 1}
    assert len(ratchet_violations(combined, root_sub)) == 2

    _write_file(pkg_dir / "root_a.py", "import demo_pkg.root_b\nfrom .sub import mod\n")
    shrunk = _cycle_counts(pkg_dir)
    assert shrunk[("root_a", "sub")] == 1
    assert ratchet_violations(shrunk, combined) == []
