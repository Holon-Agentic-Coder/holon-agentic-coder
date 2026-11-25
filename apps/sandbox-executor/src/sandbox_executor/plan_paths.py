from __future__ import annotations

import re

PLANS_DIR = "plans"
LEGACY_DIR = f"{PLANS_DIR}/legacy"
CALIBRATION_REPORT_SUFFIX = "_calibration.md"
PLAN_FILE_SUFFIX = ".md"

REFRESH_MERGE_PREFIX = "execute: refresh plan "
PLAN_FILE_PATTERN = r"plans/(?:(?:legacy|[IP]-[^/]+)/)*P-[^/]+\.md"

_CHILD_INTENT_MARKER = re.compile(r"--\d+-")
_REF_PREFIXES = ("refs/remotes/origin/", "refs/heads/", "origin/")
_BRANCH_SUFFIXES = ("/calibrated", "/_")


def bare_branch(branch: str) -> str:
    bare = branch.strip()
    for prefix in _REF_PREFIXES:
        if bare.startswith(prefix):
            bare = bare[len(prefix) :]
            break
    for suffix in _BRANCH_SUFFIXES:
        bare = bare.removesuffix(suffix)
    return bare.strip("/")


def is_plan_segment(segment: str) -> bool:
    return segment.startswith("P-") and not _CHILD_INTENT_MARKER.search(segment)


def split_plan_branch(branch: str) -> tuple[str, str]:
    segments_before_execution = bare_branch(branch).split("/")
    for index, segment in enumerate(segments_before_execution):
        if segment.startswith("E-"):
            segments_before_execution = segments_before_execution[:index]
            break
    for index in range(len(segments_before_execution) - 1, -1, -1):
        if is_plan_segment(segments_before_execution[index]):
            return "/".join(segments_before_execution[:index]), segments_before_execution[index]
    return "", ""


def plan_branch_of(branch: str) -> str:
    parent, plan_id = split_plan_branch(branch)
    return f"{parent}/{plan_id}" if parent else plan_id


def plan_file_rel(parent: str, plan_id: str) -> str:
    parent_bare = bare_branch(parent)
    return f"{PLANS_DIR}/{parent_bare}/{plan_id}{PLAN_FILE_SUFFIX}" if parent_bare else legacy_plan_file_rel(plan_id)


def calibration_report_rel(parent: str, plan_id: str) -> str:
    return plan_file_rel(parent, plan_id).removesuffix(PLAN_FILE_SUFFIX) + CALIBRATION_REPORT_SUFFIX


def plan_file_for_branch(branch: str) -> str:
    parent, plan_id = split_plan_branch(branch)
    return plan_file_rel(parent, plan_id) if plan_id else ""


def calibration_report_for_branch(branch: str) -> str:
    parent, plan_id = split_plan_branch(branch)
    return calibration_report_rel(parent, plan_id) if plan_id else ""


def legacy_plan_file_rel(plan_id: str) -> str:
    return f"{PLANS_DIR}/{plan_id}{PLAN_FILE_SUFFIX}"


def legacy_calibration_report_rel(plan_id: str) -> str:
    return f"{PLANS_DIR}/{plan_id}{CALIBRATION_REPORT_SUFFIX}"


def plan_file_candidates(parent: str, plan_id: str) -> list[str]:
    candidates = [plan_file_rel(parent, plan_id), legacy_plan_file_rel(plan_id)]
    return list(dict.fromkeys(candidates))


def calibration_report_candidates(parent: str, plan_id: str) -> list[str]:
    candidates = [calibration_report_rel(parent, plan_id), legacy_calibration_report_rel(plan_id)]
    return list(dict.fromkeys(candidates))


def plan_branch_from_path(path: str) -> str:
    if not path.startswith(f"{PLANS_DIR}/") or not path.endswith(PLAN_FILE_SUFFIX):
        return ""
    inner = path[len(PLANS_DIR) + 1 :].removesuffix(CALIBRATION_REPORT_SUFFIX).removesuffix(PLAN_FILE_SUFFIX)
    inner = inner.removeprefix("legacy/")
    return inner if inner.rsplit("/", 1)[-1].startswith("P-") else ""


def is_calibration_report_path(path: str) -> bool:
    if not path.endswith(CALIBRATION_REPORT_SUFFIX) or path.startswith(f"{LEGACY_DIR}/"):
        return False
    segments = plan_branch_from_path(path).split("/")
    parent_segments = segments[:-1]
    starts_with_intent = not parent_segments or parent_segments[0].startswith("I-")
    return bool(segments[-1]) and starts_with_intent and all(s.startswith(("I-", "P-")) for s in parent_segments)


def recorded_plan_file(recorded: object, plan_branch: str) -> str:
    value = str(recorded or "")
    return value if re.fullmatch(PLAN_FILE_PATTERN, value) else plan_file_for_branch(plan_branch)


def is_row_of_plan(row: dict, plan_branch: str) -> bool:
    parent, plan_id = split_plan_branch(plan_branch)
    if not plan_id or row.get("plan_id") != plan_id:
        return False
    return bare_branch(str(row.get("intent_branch") or "")) == parent


def is_row_of_execution(row: dict, execution_id: str, branch: str = "") -> bool:
    if not execution_id or row.get("execution_id") != execution_id:
        return False
    wanted_plan_branch = plan_branch_of(branch)
    recorded_plan_branch = plan_branch_of(str(row.get("plan_branch") or ""))
    return not (wanted_plan_branch and recorded_plan_branch) or wanted_plan_branch == recorded_plan_branch
