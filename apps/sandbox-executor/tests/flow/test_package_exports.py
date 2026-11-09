"""The sandbox_executor.flow package re-exports every name flow.py bound, from the module that now defines it."""

from __future__ import annotations

import sys

import pytest
import sandbox_executor.flow as flow
from sandbox_executor.flow import pipeline
from sandbox_executor.flow.stages import decomposition

from tests.flow.move_proof import load_snapshot, package_definitions

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


def _reexported_keys() -> list[str]:
    """Snapshot keys of top-level definitions and named constants (methods and subscript registrations excluded)."""
    return sorted(key for key in load_snapshot()["definitions"] if "." not in key and "[" not in key)


@pytest.mark.parametrize("key", _reexported_keys())
def test_package_reexports_each_definition_from_its_module(key):
    name = key.removeprefix("<const>:")
    (module_path,) = {path for path, _ in package_definitions()[key]}
    module_name = "sandbox_executor.flow." + module_path.removesuffix(".py").replace("/", ".")
    assert name in flow.__all__
    assert FLOW_NAMESPACES["sandbox_executor.flow"][name] is FLOW_NAMESPACES[module_name][name]


@pytest.mark.parametrize("name", REEXPORTED_IMPORTS)
def test_package_reexports_the_imported_names_callers_reach(name):
    assert name in flow.__all__
    assert hasattr(flow, name)


def test_decomposition_sees_the_pipeline_engine_through_the_late_binding():
    assert decomposition.PipelineEngine is pipeline.PipelineEngine
