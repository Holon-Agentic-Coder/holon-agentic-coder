from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
import sandbox_executor.flow as flow

IMPORTED_NAMES_CALLERS_REACH_THROUGH_FLOW = (
    "SKIP_PUSH_DISPATCH_ERROR",
    "build_pr_review_consensus_report",
    "parse_metrics",
    "redact_agent_secrets",
    "run_git",
)


def binding_in_defining_module(name: str) -> object:
    return getattr(importlib.import_module(DEFINITIONS[name]), name)


FLOW_PACKAGE_DIR = Path(flow.__file__).resolve().parent


def public_definitions_by_owning_module(package_dir: Path, package_name: str) -> dict[str, str]:
    # why: constants and imported names are outside this contract; the imported-names tuple and __all__ cover them
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


def names_listed_in_package_all(package_dir: Path) -> set[str]:
    tree = ast.parse((package_dir / "__init__.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    found = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            if not isinstance(value, (ast.List, ast.Tuple)) or not all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
            ):
                raise ValueError("flow/__init__.py must declare __all__ as a literal list or tuple of strings")
            names.update(element.value for element in value.elts)
            found = True
    if not found:
        raise ValueError("flow/__init__.py does not declare __all__")
    return names


def names_missing_from_all(definitions: dict[str, str], listed: set[str]) -> list[str]:
    return sorted(set(definitions) - listed)


# why: never re-exported when the guard was reworked; the guard fails both ways, so this set can only shrink
KNOWN_UNEXPORTED = frozenset(
    {
        "Adjudication",
        "FinalHeadSeams",
        "MergeResult",
        "PostDecision",
        "PostFacts",
        "PostRequest",
        "adjudication_mismatches",
        "apply_local_test_evidence",
        "approval_pr_number",
        "approved_head_of",
        "bare_detail",
        "blocked_error",
        "check_final_head_ci",
        "checkpoint_exists",
        "child_checkpoint_path",
        "classify_final_head",
        "clean_test_name",
        "consensus_blockers",
        "decide_post",
        "decide_returned_consensus",
        "describe_failure",
        "durations_from_log",
        "durations_from_records",
        "failure_detail",
        "gh_comment_lister",
        "gh_comment_poster",
        "gh_detail_lookup",
        "gh_runner",
        "gh_slowest_lookup",
        "gh_timing_seams",
        "has_recorded_decomposition",
        "initial_progress",
        "integrate_children",
        "interleave_ledger_rows",
        "invalidate_approval",
        "invalidation_marker",
        "is_suppressible",
        "local_test_failure_finding",
        "log_skipped_post",
        "lookup_default_branch",
        "merge_child_pr",
        "merge_into_branch",
        "merge_refusal_reason",
        "next_child_action",
        "not_approved_verdicts",
        "not_pushed_record",
        "parse_run_id",
        "post_review_consensus",
        "published_head_of",
        "reconcile_with_origin",
        "record_nit_rejections",
        "render_final_verdicts",
        "render_invalidation",
        "resolve_ledger_only_conflicts",
        "resume_children",
        "reverify_and_report",
        "reverify_final_head",
        "role_objections",
        "run_children",
        "run_gh",
        "security_objectors",
        "short_sha",
        "Stage5PrerequisiteError",
        "stage5_prerequisite_error",
        "timing_gate_details",
        "timing_gate_finding",
        "top_slowest",
        "view_pr",
    }
)

try:
    DEFINITIONS = public_definitions_by_owning_module(FLOW_PACKAGE_DIR, "sandbox_executor.flow")
    DEFINITIONS_ERROR = None
except ValueError as error:  # why: reported by test_public_definitions_are_unambiguous, not as a collection error
    DEFINITIONS = {}
    DEFINITIONS_ERROR = str(error)


def test_public_definitions_are_unambiguous():
    assert DEFINITIONS_ERROR is None, DEFINITIONS_ERROR


def test_every_public_definition_is_listed_in_all():
    assert DEFINITIONS_ERROR is None, DEFINITIONS_ERROR
    missing = set(names_missing_from_all(DEFINITIONS, set(flow.__all__)))
    assert sorted(missing - KNOWN_UNEXPORTED) == []
    assert sorted(KNOWN_UNEXPORTED - missing) == []


@pytest.mark.parametrize("name", sorted(set(DEFINITIONS) - KNOWN_UNEXPORTED))
def test_package_reexports_each_definition_from_its_module(name):
    assert name in flow.__all__
    assert getattr(flow, name) is binding_in_defining_module(name)


def test_every_definition_listed_in_all_resolves_to_its_owning_module():
    stale = [
        name
        for name in flow.__all__
        if name in DEFINITIONS and getattr(flow, name) is not binding_in_defining_module(name)
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
    listed = names_listed_in_package_all(package)
    assert names_missing_from_all(public_definitions_by_owning_module(package, "fakepkg"), listed) == []

    module.write_text(
        module.read_text(encoding="utf-8")
        + "\n\ndef added():\n    pass\n\n\ndef _private():\n    pass\n"
        + "\n\nclass Engine2:\n    def method(self):\n        pass\n",
        encoding="utf-8",
    )
    assert names_missing_from_all(public_definitions_by_owning_module(package, "fakepkg"), listed) == [
        "Engine2",
        "added",
    ]


@pytest.mark.parametrize("name", IMPORTED_NAMES_CALLERS_REACH_THROUGH_FLOW)
def test_package_reexports_the_imported_names_callers_reach(name):
    assert name in flow.__all__
    assert hasattr(flow, name)
