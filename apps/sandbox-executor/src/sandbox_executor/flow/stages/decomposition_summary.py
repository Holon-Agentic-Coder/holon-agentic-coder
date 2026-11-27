from __future__ import annotations

from typing import Any

from sandbox_executor.flow.context import StageResult, StageStatus


def _dict_items(values: Any) -> list[dict[str, Any]]:
    return [value for value in values or [] if isinstance(value, dict)]


def _child_location(child: dict[str, Any]) -> Any:
    return child.get("pr_url") or child.get("execution_branch") or child.get("intent_branch") or "no branch"


def _child_state(child: dict[str, Any], parent_branch: str) -> str:
    return f"merged into '{parent_branch}'" if child.get("merged") else str(child.get("status"))


def _child_line(index: int, child: dict[str, Any], parent_branch: str, indent: str) -> list[str]:
    where = _child_location(child)
    state = _child_state(child, parent_branch)
    if not child.get("decomposed"):
        return [f"{indent}{index}. {child.get('slug')}: {where} ({state})"]
    grandchildren = _dict_items(child.get("children"))
    note = f"decomposed into {len(grandchildren)} sub-intent(s), combined in {where} ({state})"
    nested_parent = str(child.get("plan_branch") or "its plan branch")
    return [
        f"{indent}{index}. {child.get('slug')}: {note}:",
        *_decomposition_child_lines(grandchildren, nested_parent, indent + "   "),
    ]


def _decomposition_child_lines(children: list[Any], parent_branch: str, indent: str) -> list[str]:
    lines: list[str] = []
    for index, child in enumerate(_dict_items(children), start=1):
        lines.extend(_child_line(index, child, parent_branch, indent))
    return lines


def _listed_count(payload: dict[str, Any], children: list[dict[str, Any]]) -> int:
    raw_items = payload.get("sub_intents")
    # why: a failed run stops at its first failing child and a depth refusal runs none, so count what was listed
    return sum(isinstance(i, dict) for i in raw_items) if isinstance(raw_items, list) else len(children)


def _summary_header(listed: int, children: list[dict[str, Any]], plan_branch: str, failed: bool) -> str:
    if not failed:
        return f"Decomposed into {listed} sub-intent(s); each child PR was merged into '{plan_branch}':"
    merged = sum(bool(c.get("merged")) for c in children)
    return (
        f"Decomposed into {listed} sub-intent(s); {len(children)} of {listed} ran and {merged} merged into "
        f"'{plan_branch}' before Stage 3 failed:"
    )


def _combined_pr_line(payload: dict[str, Any], plan_branch: str) -> str:
    target = payload.get("parent_target_branch") or "its target branch"
    source = payload.get("execution_branch") or "the execution branch"
    return f"Combined PR: Stage 4 raises and reviews '{source}' (with '{plan_branch}' merged in) into '{target}'."


def format_decomposition_summary(execute_result: StageResult | None) -> list[str]:
    if execute_result is None or not execute_result.payload.get("decomposed"):
        return []
    payload = execute_result.payload
    children = _dict_items(payload.get("children"))
    plan_branch = payload.get("plan_branch") or "the plan branch"
    failed = execute_result.status == StageStatus.FAILED
    lines = [_summary_header(_listed_count(payload, children), children, plan_branch, failed)]
    lines.extend(_decomposition_child_lines(children, plan_branch, "  "))
    # why: a failed run omits the hand-off to Stage 4 so it never reads as success
    if not failed:
        lines.append(_combined_pr_line(payload, plan_branch))
    return lines
