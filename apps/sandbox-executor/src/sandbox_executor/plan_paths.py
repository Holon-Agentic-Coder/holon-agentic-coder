"""Where a plan file and its calibration report live (pure: no I/O, no git).

A plan file follows its branch name: the plan on ``{parent}/{plan id}/_`` lives at ``plans/{parent}/{plan id}.md`` and
its calibration report beside it at ``plans/{parent}/{plan id}_calibration.md``. The parent is the full bare branch
above the plan, so the layout mirrors every nesting the branch grammar has: a root intent (``I-.../P-...``), a child
intent (``I-...--{index}-{slug}/P-...``) and a plan nested under another plan (``I-.../P-.../P-...``). Keying the
file by its parent removes the old flat ``plans/{plan id}.md`` collision between two intents planned in the same
second by the same runner and model.

The flat legacy locations stay readable (:func:`plan_file_candidates`) until the one-off migration
(``scripts/migrate_plan_layout.py``) has moved them; a plan file that no branch can be found for is parked under
``plans/legacy/``.
"""

from __future__ import annotations

import re

PLANS_DIR = "plans"
LEGACY_DIR = f"{PLANS_DIR}/legacy"
CALIBRATION_REPORT_SUFFIX = "_calibration.md"
PLAN_FILE_SUFFIX = ".md"

# Subject prefix of the merge commit with which Stage 3 refreshes a plan branch onto its target after an admission wait
# (flow/plan_refresh.py); a resumed execution's lineage check (entrypoint/partial_resume.py) recognises it.
REFRESH_MERGE_PREFIX = "execute: refresh plan "

# A plan file, flat (legacy), under plans/legacy/, or nested under its parent's ``I-``/``P-`` branch directories (so
# no ``..`` or foreign directory can ever match).
PLAN_FILE_PATTERN = r"plans/(?:(?:legacy|[IP]-[^/]+)/)*P-[^/]+\.md"

# A child intent segment is ``{parent segment}--{index}-{slug}``; its parent segment may itself be a plan id.
_CHILD_INTENT_MARKER = re.compile(r"--\d+-")
_REF_PREFIXES = ("refs/remotes/origin/", "refs/heads/", "origin/")
_BRANCH_SUFFIXES = ("/calibrated", "/_")


def bare_branch(branch: str) -> str:
    """Strip a remote or ``refs/heads/`` prefix and the ``/_`` or ``/calibrated`` suffix from a branch name."""
    bare = branch.strip()
    for prefix in _REF_PREFIXES:
        if bare.startswith(prefix):
            bare = bare[len(prefix) :]
            break
    for suffix in _BRANCH_SUFFIXES:
        bare = bare.removesuffix(suffix)
    return bare.strip("/")


def is_plan_segment(segment: str) -> bool:
    """True for a plan id segment (``P-...``), false for a child intent segment nested under a plan."""
    return segment.startswith("P-") and not _CHILD_INTENT_MARKER.search(segment)


def split_plan_branch(branch: str) -> tuple[str, str]:
    """Return ``(parent, plan id)`` for any branch at or below a plan; ``("", "")`` when it names no plan.

    The plan is the last plan segment before the first ``E-`` segment, so an execution branch, a calibrated branch
    and a plan nested under another plan all resolve to the plan that owns them. A child intent segment
    (``{parent}--{index}-{slug}``) is never a plan, even when its parent segment is a plan id.
    """
    segments = bare_branch(branch).split("/")
    for index, segment in enumerate(segments):
        if segment.startswith("E-"):
            segments = segments[:index]
            break
    for index in range(len(segments) - 1, -1, -1):
        if is_plan_segment(segments[index]):
            return "/".join(segments[:index]), segments[index]
    return "", ""


def plan_branch_of(branch: str) -> str:
    """Return the bare plan branch (``{parent}/{plan id}``) owning ``branch``, or ``""`` when it names no plan."""
    parent, plan_id = split_plan_branch(branch)
    return f"{parent}/{plan_id}" if parent else plan_id


def plan_file_rel(parent: str, plan_id: str) -> str:
    """Repo-relative plan file: ``plans/{parent}/{plan id}.md``; a parent-less plan id falls back to the flat path."""
    parent_bare = bare_branch(parent)
    return f"{PLANS_DIR}/{parent_bare}/{plan_id}{PLAN_FILE_SUFFIX}" if parent_bare else legacy_plan_file_rel(plan_id)


def calibration_report_rel(parent: str, plan_id: str) -> str:
    """Repo-relative calibration report, beside the plan file: ``plans/{parent}/{plan id}_calibration.md``."""
    return plan_file_rel(parent, plan_id).removesuffix(PLAN_FILE_SUFFIX) + CALIBRATION_REPORT_SUFFIX


def plan_file_for_branch(branch: str) -> str:
    """Plan file of the plan owning ``branch`` (``""`` when it names no plan)."""
    parent, plan_id = split_plan_branch(branch)
    return plan_file_rel(parent, plan_id) if plan_id else ""


def calibration_report_for_branch(branch: str) -> str:
    """Calibration report of the plan owning ``branch`` (``""`` when it names no plan)."""
    parent, plan_id = split_plan_branch(branch)
    return calibration_report_rel(parent, plan_id) if plan_id else ""


def legacy_plan_file_rel(plan_id: str) -> str:
    """The flat pre-migration plan file ``plans/{plan id}.md``."""
    return f"{PLANS_DIR}/{plan_id}{PLAN_FILE_SUFFIX}"


def legacy_calibration_report_rel(plan_id: str) -> str:
    """The flat pre-migration calibration report ``plans/{plan id}_calibration.md``."""
    return f"{PLANS_DIR}/{plan_id}{CALIBRATION_REPORT_SUFFIX}"


def plan_file_candidates(parent: str, plan_id: str) -> list[str]:
    """Plan file locations to try, new layout first, then the flat legacy location (de-duplicated)."""
    candidates = [plan_file_rel(parent, plan_id), legacy_plan_file_rel(plan_id)]
    return list(dict.fromkeys(candidates))


def calibration_report_candidates(parent: str, plan_id: str) -> list[str]:
    """Calibration report locations to try, new layout first, then the flat legacy location (de-duplicated)."""
    candidates = [calibration_report_rel(parent, plan_id), legacy_calibration_report_rel(plan_id)]
    return list(dict.fromkeys(candidates))


def plan_branch_from_path(path: str) -> str:
    """Return the bare plan branch a plan file or calibration report path encodes (``""`` for a non-plan path).

    A flat legacy path or a ``plans/legacy/`` path encodes only the plan id, with no parent.
    """
    if not path.startswith(f"{PLANS_DIR}/") or not path.endswith(PLAN_FILE_SUFFIX):
        return ""
    inner = path[len(PLANS_DIR) + 1 :].removesuffix(CALIBRATION_REPORT_SUFFIX).removesuffix(PLAN_FILE_SUFFIX)
    inner = inner.removeprefix("legacy/")
    return inner if inner.rsplit("/", 1)[-1].startswith("P-") else ""


def is_calibration_report_path(path: str) -> bool:
    """True for a calibration report under ``plans/``: flat legacy, or nested under ``I-``/``P-`` branch directories."""
    if not path.endswith(CALIBRATION_REPORT_SUFFIX) or path.startswith(f"{LEGACY_DIR}/"):
        return False
    segments = plan_branch_from_path(path).split("/")
    parents = segments[:-1]
    rooted = not parents or parents[0].startswith("I-")
    return bool(segments[-1]) and rooted and all(s.startswith(("I-", "P-")) for s in parents)


def recorded_plan_file(recorded: object, plan_branch: str) -> str:
    """The plan file a ``plans.jsonl`` row records when it is plan-file shaped, else the one ``plan_branch`` implies."""
    value = str(recorded or "")
    return value if re.fullmatch(PLAN_FILE_PATTERN, value) else plan_file_for_branch(plan_branch)


def is_row_of_plan(row: dict, plan_branch: str) -> bool:
    """True when a ``plans.jsonl`` row is the plan on ``plan_branch``: same plan id *and* same parent (intent).

    A plan is never matched on its id alone, so two intents planned in the same second by the same runner and model
    resolve to their own rows.
    """
    parent, plan_id = split_plan_branch(plan_branch)
    if not plan_id or row.get("plan_id") != plan_id:
        return False
    return bare_branch(str(row.get("intent_branch") or "")) == parent


def is_row_of_execution(row: dict, execution_id: str, branch: str = "") -> bool:
    """True when an ``executions.jsonl`` row is execution ``execution_id``, keyed on its full path when known.

    A new-generation execution id carries no hex, so the same id may repeat under another plan. With ``branch`` (any
    ref at or below the execution branch) the row must also belong to that plan; a row naming no plan, or a lookup
    without a branch, matches on the id alone.
    """
    if not execution_id or row.get("execution_id") != execution_id:
        return False
    wanted, recorded = plan_branch_of(branch), plan_branch_of(str(row.get("plan_branch") or ""))
    return not (wanted and recorded) or wanted == recorded
