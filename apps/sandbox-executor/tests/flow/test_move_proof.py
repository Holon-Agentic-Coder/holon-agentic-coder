"""Proof that the flow.py split moved code only: every definition's AST is unchanged and none is missing or extra.

The snapshot in ``fixtures/flow_py_definitions.json`` was produced from the original ``flow.py`` blob with
:func:`definition_digests`, the same function this test applies to the package, so producer and checker cannot drift.
The package is parsed from disk rather than imported, so the proof still runs when an import is broken.
"""

from __future__ import annotations

import ast
import sys

from tests.flow.move_proof import LOGGER_SOURCE, assign_key, load_snapshot, package_definitions, package_modules


def test_snapshot_was_taken_with_this_python_minor_version():
    recorded = load_snapshot()["python"]
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert recorded == running, f"ast.dump output is only stable within a minor version: {recorded} vs {running}"


def test_snapshot_covers_every_flow_py_definition():
    definitions = load_snapshot()["definitions"]
    top_level = [key for key in definitions if "." not in key and not key.startswith("<const>:")]
    assert len(top_level) >= 124


def test_every_definition_moved_unchanged():
    expected: dict[str, str] = load_snapshot()["definitions"]
    added = load_snapshot().get("added_definitions", {})
    found = package_definitions()

    missing = sorted(set(expected) - set(found))
    extra = sorted(
        f"{key} ({', '.join(path for path, _ in found[key])})" for key in set(found) - set(expected) - set(added)
    )
    duplicated = sorted(
        f"{key} ({', '.join(path for path, _ in sites)})" for key, sites in found.items() if len(sites) > 1
    )
    differing = sorted(
        f"{key} ({path})"
        for key, sites in found.items()
        if key in expected
        for path, digest in sites
        if digest != expected[key]
    )

    assert missing == [], f"definitions missing from the package: {missing}"
    assert extra == [], f"definitions not in flow.py: {extra}"
    assert duplicated == [], f"definitions repeated across modules: {duplicated}"
    assert differing == [], f"definitions whose body changed: {differing}"


def test_each_declared_addition_is_defined_once_in_its_declared_module():
    """A definition added after the split is declared with its module, so a stale or misplaced entry fails."""
    added: dict[str, dict[str, str]] = load_snapshot().get("added_definitions", {})
    found = package_definitions()

    misplaced = sorted(
        key for key, entry in added.items() if [path for path, _ in found.get(key, [])] != [entry["module"]]
    )
    overlapping = sorted(set(added) & set(load_snapshot()["definitions"]))

    assert misplaced == [], f"declared additions missing or not in their declared module: {misplaced}"
    assert overlapping == [], f"declared additions that are also original flow.py definitions: {overlapping}"
    assert all(entry.get("reason") for entry in added.values())


def test_package_plumbing_is_only_loggers_reexports_and_the_late_binding():
    for module_path, tree in package_modules().items():
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign) and assign_key(node) == "<const>:logger":
                assert ast.unparse(node) == LOGGER_SOURCE, module_path
            if isinstance(node, ast.Assign | ast.AnnAssign) and assign_key(node) in {
                "<const>:__all__",
                "<const>:_children.PipelineEngine",
            }:
                assert module_path == "__init__.py", module_path
