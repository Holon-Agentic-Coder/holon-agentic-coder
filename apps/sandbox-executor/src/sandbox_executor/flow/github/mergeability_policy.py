"""I/O-free mergeability and merge refusal verdicts.

How parsed ``gh pr view`` JSON, ``git merge-tree`` exit codes and ref resolution facts become the mergeability result
dict returned by :func:`sandbox_executor.flow.github.pr.check_pr_mergeability`, and the fail-closed merge refusal
verdict used by :mod:`sandbox_executor.flow.github.merge`. This module spawns nothing and reads no environment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROTECTED_BRANCHES = frozenset({"main", "master"})


def mergeability_result(
    mergeable: str,
    merge_state_status: str,
    *,
    source: str = "git_local",
    gh_view: Mapping[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the seven-key mergeability dict; ``is_dirty`` is True only for ``DIRTY`` or ``CONFLICTING``.

    ``gh_view`` is the parsed ``gh pr view`` JSON supplying ``pr_number`` and ``status_check_rollup`` (None without it).
    """
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
    """Return the ``github_cli`` result for parsed ``gh pr view`` JSON and whether GitHub is still computing it."""
    mergeable = str(data.get("mergeable") or "UNKNOWN").upper()
    merge_state_status = str(data.get("mergeStateStatus") or "UNKNOWN").upper()
    result = mergeability_result(mergeable, merge_state_status, source="github_cli", gh_view=data)
    return result, mergeable == "UNKNOWN"


def merge_tree_verdict(returncode: int, stderr: object) -> dict[str, Any]:
    """Map a ``git merge-tree --write-tree`` exit code to the ``git_local`` result.

    0 is MERGEABLE/CLEAN, 1 is CONFLICTING/DIRTY; anything else (unrelated histories, a git too old for
    ``--write-tree``, any other failure) is an honest UNKNOWN with the exit code and stderr in diagnostics.
    """
    if returncode == 0:
        return mergeability_result("MERGEABLE", "CLEAN")
    if returncode == 1:
        return mergeability_result("CONFLICTING", "DIRTY")
    diagnostics = {"merge_tree_exit_code": returncode, "stderr": stderr.strip() if isinstance(stderr, str) else ""}
    return mergeability_result("UNKNOWN", "UNKNOWN", diagnostics=diagnostics)


def missing_ref_result(ref: str, role: str, candidates: list[str], stderr: str) -> dict[str, Any]:
    """Return the MISSING_REF result for a ``role`` ref (``branch`` or ``target``) that resolved to no commit."""
    diagnostics = {
        "missing_ref": ref,
        "ref_role": role,
        "candidates_tried": candidates,
        "stderr": stderr,
        "message": f"cannot evaluate mergeability for ref {ref}",
    }
    return mergeability_result("UNKNOWN", "MISSING_REF", diagnostics=diagnostics)


def not_a_repository_result(repo_dir: str, stderr: str) -> dict[str, Any]:
    """Return the UNKNOWN result for a ``repo_dir`` that is not a git repository."""
    return mergeability_result(
        "UNKNOWN", "UNKNOWN", diagnostics={"error": f"not a git repository: {repo_dir}", "stderr": stderr}
    )


def local_error_result(error: object) -> dict[str, Any]:
    """Return the UNKNOWN result for an exception raised during the local git inspection."""
    return mergeability_result("UNKNOWN", "UNKNOWN", diagnostics={"error": str(error)})


def _bare_branch(name: str | None) -> str:
    """Strip ``refs/heads/``, ``refs/remotes/`` and ``origin/`` prefixes and surrounding blanks from a branch name."""
    clean = str(name or "").strip()
    for prefix in ("refs/heads/", "refs/remotes/", "origin/"):
        clean = clean.removeprefix(prefix)
    return clean


def merge_refusal_reason(base_branch: str | None, expected_base: str | None, default_branch: str | None) -> str | None:
    """Return why a PR with base ``base_branch`` must not be merged into ``expected_base``, or None when it may.

    Fail closed: a failed default-branch lookup (``default_branch`` None or blank), an empty base or expected base,
    ``main``, ``master`` or the default branch on either side, and any base other than the expected plan branch are
    all refused.
    """
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
