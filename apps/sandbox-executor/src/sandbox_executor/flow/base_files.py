"""Stage 3 admission input: the files that exist on the flow's base branch, listed read-only and fail closed.

:mod:`touch_set_overlap` expands folder and glob entries only against these files, so a new file never overlaps them.
The base branch is the intent's ``target_branch`` (``main`` by default), resolved origin tracking ref first; the listing
neither fetches nor writes. A branch that does not resolve, or a ``git ls-tree`` failure, returns
:class:`flow.in_flight.AdmissionUnavailable`, so admission refuses rather than compares against nothing. The helpers
are private to the admission path (imported by :mod:`flow.admission_decision`), so the package exports do not grow.
"""

from __future__ import annotations

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.git_refs import _resolve_branch_ref
from sandbox_executor.flow.in_flight import AdmissionUnavailable, GitRunner, _failure, _host_git
from sandbox_executor.git import is_metadata_path
from sandbox_executor.touch_set import normalise_touch_path


def _base_branch_name(context: FlowContext) -> str:
    """The flow's target branch (the same rule as ``flow.plan_refresh._target_branch``)."""
    return str(context.intent_data.get("target_branch") or "main")


def _list_base_files(
    repo_dir: str, branch: str, run_git: GitRunner | None = None
) -> frozenset[str] | AdmissionUnavailable:
    """The normalised non-metadata paths of ``branch``'s tree, or why they could not be listed."""
    ref = _resolve_branch_ref(repo_dir, branch)
    if ref is None:
        return AdmissionUnavailable(f"the base branch {branch} could not be resolved")
    result = (run_git or _host_git)(["ls-tree", "-r", "--name-only", "-z", ref], repo_dir)
    if result.returncode != 0:
        return _failure(f"git ls-tree {ref}", result)
    paths = (normalise_touch_path(path) for path in (result.stdout or "").split("\0"))
    return frozenset(path for path in paths if path and not is_metadata_path(path))
