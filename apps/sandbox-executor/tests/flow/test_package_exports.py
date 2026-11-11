"""The sandbox_executor.flow package re-exports the names flow.py bound, from the module that now defines it.

Deliberate deviation: the public definitions in ``KNOWN_UNEXPORTED`` are not re-exported (they are reached through
their owning submodules). The guard ratchets: new unexported definitions fail, and the set can only shrink.
Constants are intentionally outside the definitions contract.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import sandbox_executor.flow as flow
from sandbox_executor.flow import pipeline
from sandbox_executor.flow.stages import children

# Names flow.py imported from elsewhere that callers and tests still reach as ``sandbox_executor.flow.<name>``.
REEXPORTED_IMPORTS = (
    "SKIP_PUSH_DISPATCH_ERROR",
    "build_pr_review_consensus_report",
    "parse_metrics",
    "redact_agent_secrets",
    "run_git",
)

# Namespaces captured at import (collection) time, before any fixture patches a submodule binding.
FLOW_NAMESPACES = {
    name: dict(vars(module))
    for name, module in list(sys.modules.items())
    if name == "sandbox_executor.flow" or name.startswith("sandbox_executor.flow.")
}


FLOW_PACKAGE_DIR = Path(flow.__file__).resolve().parent


def public_definitions(package_dir: Path, package_name: str) -> dict[str, str]:
    """Map each public top-level function and class in the package's submodules to its owning module's dotted name.

    Private (``_``-prefixed) names, ``__init__.py``, nested definitions and constants are not definitions for this
    contract: constants and imported names are covered by ``REEXPORTED_IMPORTS`` and ``__all__`` membership.
    Raises ``ValueError`` when two modules define the same public name, since the name would be ambiguous.
    """
    definitions: dict[str, str] = {}
    duplicates: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        dotted = ".".join((package_name, *path.relative_to(package_dir).with_suffix("").parts))
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and not node.name.startswith(
                "_"
            ):
                if node.name in definitions:
                    duplicates.append(f"{node.name} ({definitions[node.name]}, {dotted})")
                definitions[node.name] = dotted
    if duplicates:
        raise ValueError(f"public name defined in more than one module: {', '.join(sorted(duplicates))}")
    return definitions


def declared_all(package_dir: Path) -> set[str]:
    """Names listed in the package's ``__all__``, read from its ``__init__.py`` without importing it."""
    tree = ast.parse((package_dir / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            return {element.value for element in node.value.elts}
    return set()


def names_missing_from_all(definitions: dict[str, str], listed: set[str]) -> list[str]:
    return sorted(set(definitions) - listed)


# Public definitions that were never re-exported when the guard was reworked. Reachable only through their owning
# module today; the guard fails for any definition not listed here, and for any entry here that is re-exported or gone,
# so the set can only shrink. Re-export an entry from ``flow/__init__.py`` and delete it here.
KNOWN_UNEXPORTED = frozenset(
    {
        "Adjudication",
        "MergeResult",
        "adjudication_mismatches",
        "apply_local_test_evidence",
        "checkpoint_exists",
        "child_checkpoint_path",
        "consensus_blockers",
        "decide_returned_consensus",
        "gh_runner",
        "has_recorded_decomposition",
        "initial_progress",
        "integrate_children",
        "interleave_ledger_rows",
        "is_suppressible",
        "lookup_default_branch",
        "merge_child_pr",
        "merge_into_branch",
        "merge_refusal_reason",
        "next_child_action",
        "not_approved_verdicts",
        "reconcile_with_origin",
        "record_nit_rejections",
        "render_final_verdicts",
        "resolve_ledger_only_conflicts",
        "resume_children",
        "role_objections",
        "run_children",
        "run_gh",
        "security_objectors",
        "view_pr",
    }
)

try:
    DEFINITIONS = public_definitions(FLOW_PACKAGE_DIR, "sandbox_executor.flow")
    DEFINITIONS_ERROR = None
except ValueError as error:  # reported by test_public_definitions_are_unambiguous, not as a collection error
    DEFINITIONS = {}
    DEFINITIONS_ERROR = str(error)


def test_public_definitions_are_unambiguous():
    assert DEFINITIONS_ERROR is None, DEFINITIONS_ERROR


def test_every_public_definition_is_listed_in_all():
    assert DEFINITIONS_ERROR is None, DEFINITIONS_ERROR
    missing = set(names_missing_from_all(DEFINITIONS, declared_all(FLOW_PACKAGE_DIR)))
    assert sorted(missing - KNOWN_UNEXPORTED) == []
    assert sorted(KNOWN_UNEXPORTED - missing) == []


@pytest.mark.parametrize("name", sorted(set(DEFINITIONS) - KNOWN_UNEXPORTED))
def test_package_reexports_each_definition_from_its_module(name):
    owner = FLOW_NAMESPACES[DEFINITIONS[name]][name]
    assert name in flow.__all__
    assert FLOW_NAMESPACES["sandbox_executor.flow"][name] is owner


def test_every_definition_listed_in_all_resolves_to_its_owning_module():
    stale = [
        name
        for name in flow.__all__
        if name in DEFINITIONS
        and FLOW_NAMESPACES["sandbox_executor.flow"][name] is not FLOW_NAMESPACES[DEFINITIONS[name]][name]
    ]
    assert stale == []


def test_the_check_reports_a_definition_added_without_being_re_exported(tmp_path):
    package = tmp_path / "fakepkg"
    package.mkdir()
    module = package / "engine.py"
    module.write_text("def run():\n    pass\n\n\nclass Engine:\n    pass\n", encoding="utf-8")
    (package / "__init__.py").write_text(
        'from fakepkg.engine import Engine, run\n\n__all__ = ["Engine", "run"]\n', encoding="utf-8"
    )
    listed = declared_all(package)
    assert names_missing_from_all(public_definitions(package, "fakepkg"), listed) == []

    module.write_text(
        module.read_text(encoding="utf-8")
        + "\n\ndef added():\n    pass\n\n\ndef _private():\n    pass\n"
        + "\n\nclass Engine2:\n    def method(self):\n        pass\n",
        encoding="utf-8",
    )
    assert names_missing_from_all(public_definitions(package, "fakepkg"), listed) == ["Engine2", "added"]


@pytest.mark.parametrize("name", REEXPORTED_IMPORTS)
def test_package_reexports_the_imported_names_callers_reach(name):
    assert name in flow.__all__
    assert hasattr(flow, name)


def test_the_child_loop_sees_the_pipeline_engine_through_the_late_binding():
    assert children.PipelineEngine is pipeline.PipelineEngine
