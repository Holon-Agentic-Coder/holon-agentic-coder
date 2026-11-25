from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROTECTED_BRANCHES = frozenset({"main", "master"})
_BRANCH_REF_PREFIXES = ("refs/heads/", "refs/remotes/", "origin/")
_MERGE_TREE_CLEAN_EXIT = 0
_MERGE_TREE_CONFLICT_EXIT = 1


def mergeability_result(
    mergeable: str,
    merge_state_status: str,
    *,
    source: str = "git_local",
    gh_view: Mapping[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = gh_view or {}
    return {
        "mergeable": mergeable,
        "merge_state_status": merge_state_status,
        "is_dirty": (merge_state_status == "DIRTY") or (mergeable == "CONFLICTING"),
        "source": source,
        "pr_number": view.get("number"),
        "status_check_rollup": view.get("statusCheckRollup"),
        "diagnostics": {} if diagnostics is None else diagnostics,
    }


def gh_view_verdict(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    mergeable = str(data.get("mergeable") or "UNKNOWN").upper()
    merge_state_status = str(data.get("mergeStateStatus") or "UNKNOWN").upper()
    result = mergeability_result(mergeable, merge_state_status, source="github_cli", gh_view=data)
    return result, mergeable == "UNKNOWN"


def merge_tree_verdict(returncode: int, stderr: object) -> dict[str, Any]:
    if returncode == _MERGE_TREE_CLEAN_EXIT:
        return mergeability_result("MERGEABLE", "CLEAN")
    if returncode == _MERGE_TREE_CONFLICT_EXIT:
        return mergeability_result("CONFLICTING", "DIRTY")
    diagnostics = {"merge_tree_exit_code": returncode, "stderr": stderr.strip() if isinstance(stderr, str) else ""}
    return mergeability_result("UNKNOWN", "UNKNOWN", diagnostics=diagnostics)


def missing_ref_result(ref: str, role: str, candidates: list[str], stderr: str) -> dict[str, Any]:
    diagnostics = {
        "missing_ref": ref,
        "ref_role": role,
        "candidates_tried": candidates,
        "stderr": stderr,
        "message": f"cannot evaluate mergeability for ref {ref}",
    }
    return mergeability_result("UNKNOWN", "MISSING_REF", diagnostics=diagnostics)


def not_a_repository_result(repo_dir: str, stderr: str) -> dict[str, Any]:
    return mergeability_result(
        "UNKNOWN", "UNKNOWN", diagnostics={"error": f"not a git repository: {repo_dir}", "stderr": stderr}
    )


def local_error_result(error: object) -> dict[str, Any]:
    return mergeability_result("UNKNOWN", "UNKNOWN", diagnostics={"error": str(error)})


def _bare_branch(name: str | None) -> str:
    clean = str(name or "").strip()
    for prefix in _BRANCH_REF_PREFIXES:
        clean = clean.removeprefix(prefix)
    return clean


def merge_refusal_reason(base_branch: str | None, expected_base: str | None, default_branch: str | None) -> str | None:
    default = _bare_branch(default_branch)
    if not default:
        return "the repository default branch could not be determined, so the merge is refused (fail closed)"
    base, expected = _bare_branch(base_branch), _bare_branch(expected_base)
    if not base or not expected:
        return "the PR base or the expected plan branch is empty"
    for name, label in ((expected, "expected base"), (base, "PR base")):
        if name in PROTECTED_BRANCHES or name == default:
            return f"the {label} '{name}' is main, master or the repository default branch '{default}'"
    if base != expected:
        return f"the PR base '{base}' is not the expected plan branch '{expected}'"
    return None
