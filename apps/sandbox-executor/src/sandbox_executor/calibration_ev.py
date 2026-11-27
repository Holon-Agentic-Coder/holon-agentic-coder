from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sandbox_executor.calibration_outcome import (
    has_delivered,
    has_unresolved_failure,
    latest_row_per_execution,
)
from sandbox_executor.calibration_rows import is_execution_row
from sandbox_executor.plan_paths import plan_branch_of


@dataclass
class DualEVMetrics:
    ev_clean: float = 0.0
    ev_with_bugs: float = 0.0
    p_success_clean: float = 0.0
    p_success_with_bugs: float = 0.0
    impact_clean: float = 0.0
    impact_with_bugs: float = 0.0
    bug_ev_drag: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def is_matching_record(record: dict[str, Any], execution_id: str, plan_branch: str) -> bool:
    wanted, recorded = plan_branch_of(plan_branch or ""), plan_branch_of(str(record.get("plan_branch", "")))
    same_id = bool(execution_id) and str(record.get("execution_id", "")) == execution_id
    return record.get("entry_type", "summary") == "summary" and (recorded == wanted if wanted and recorded else same_id)


def is_matching_execution_record(record: dict[str, Any], execution_id: str, plan_branch: str) -> bool:
    return is_matching_record(record, execution_id, plan_branch) and is_execution_row(record, execution_id)


# why: Stage 4 and Stage 5 rows never add drag; without any execution row of the plan, legacy ledgers use every one.
def dual_ev_rows(
    records: list[dict[str, Any]], execution_id: str, plan_branch: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matching = [r for r in records if is_matching_execution_record(r, execution_id, plan_branch)]
    if not matching:
        matching = [r for r in records if r.get("entry_type", "summary") == "summary" and is_execution_row(r)]
    execution_rows = latest_row_per_execution(matching)
    plan_rows = [r for r in records if is_matching_record(r, execution_id, plan_branch)] or execution_rows
    return execution_rows, plan_rows


def evaluate_dual_ev_outcomes(
    records: list[dict[str, Any]],
    execution_id: str,
    plan_branch: str,
    predicted_impact: float,
    cost_effort: float,
) -> DualEVMetrics:
    sorted_records, plan_rows = dual_ev_rows(records, execution_id, plan_branch)
    if not sorted_records:
        imp_c = float(predicted_impact)
        c_ev = round(imp_c - cost_effort, 2)
        return DualEVMetrics(c_ev, c_ev, 1.0, 1.0, imp_c, imp_c, 0.0)

    is_converged = has_delivered(sorted_records, plan_rows)
    p_clean = 1.0 if is_converged else 0.0
    imp_clean = float(predicted_impact) if is_converged else 0.0
    ev_clean = round((imp_clean * p_clean) - cost_effort, 2)

    if has_unresolved_failure(sorted_records, plan_rows):
        p_bugs, imp_bugs = 0.0, 0.0
        ev_bugs = round(0.0 - cost_effort, 2)
    else:
        p_bugs, imp_bugs, ev_bugs = p_clean, imp_clean, ev_clean

    return DualEVMetrics(
        ev_clean=ev_clean,
        ev_with_bugs=ev_bugs,
        p_success_clean=p_clean,
        p_success_with_bugs=p_bugs,
        impact_clean=imp_clean,
        impact_with_bugs=imp_bugs,
        bug_ev_drag=round(ev_clean - ev_bugs, 2),
    )


def parse_summary_records(content: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in content.splitlines():
        if line.strip():
            try:
                rec = json.loads(line)
                if rec.get("entry_type", "summary") == "summary":
                    records.append(rec)
            except (json.JSONDecodeError, ValueError):
                continue
    return records


def format_dual_ev_table_rows(
    pred_ev: float,
    act: dict[str, Any],
    deltas: dict[str, Any],
    ratings: dict[str, str],
    biases: dict[str, str],
) -> list[str]:
    c_ev = float(act.get("ev_clean", act.get("ev", 0.0)))
    b_ev = float(act.get("ev_with_bugs", act.get("ev", 0.0)))
    drag = float(act.get("bug_ev_drag", round(c_ev - b_ev, 2)))
    err_c = round(abs(float(deltas.get("delta_ev_clean", deltas.get("ev_error", abs(pred_ev - c_ev))))), 2)
    err_b = round(abs(pred_ev - b_ev), 2)
    r_c = ratings.get("ev_clean", ratings.get("ev", "Moderate"))
    r_b = ratings.get("ev_with_bugs", "Moderate")
    b_c = biases.get("ev_clean", biases.get("ev", "Conservative Underestimate"))
    b_b = biases.get("ev_with_bugs", "Optimistic Overestimate")

    return [
        (
            f"| **Expected Value — Clean (without bugs)** | `{pred_ev:.2f}`            | "
            f"`{c_ev:.2f}`           | `{err_c:.2f}`"
            f"                                | {r_c:<17} | {b_c:<26} |"
        ),
        (
            f"| **Expected Value — Observed (with bugs included)** | `{pred_ev:.2f}`            | "
            f"`{b_ev:.2f}`           | `{err_b:.2f}`"
            f"                                | {r_b:<17} | {b_b:<26} |"
        ),
        (
            f"| **Bug EV Drag**           | `0.00`             | "
            f"`{drag:.2f}`           | `{drag:.2f}`"
            f"                                | {'-':<17} | {'Drag Penalty':<26} |"
        ),
    ]


def format_dual_ev_derivation_lines(
    pred: dict[str, Any],
    act: dict[str, Any],
    deltas: dict[str, Any],
    lambda_val: float,
    mu_val: float,
) -> list[str]:
    p_c = float(act.get("p_success_clean", act.get("p_success", 0.0)))
    imp_c = float(act.get("impact_clean", act.get("impact", 0.0)))
    p_b = float(act.get("p_success_with_bugs", act.get("p_success", 0.0)))
    imp_b = float(act.get("impact_with_bugs", act.get("impact", 0.0)))
    ev_c = float(act.get("ev_clean", act.get("ev", 0.0)))
    ev_b = float(act.get("ev_with_bugs", act.get("ev", 0.0)))
    drag = float(act.get("bug_ev_drag", round(ev_c - ev_b, 2)))
    lv, ent, cost = float(act.get("learning_value", 0.0)), float(act.get("entropy", 0.0)), float(act.get("cost", 0.0))
    d_c = float(deltas.get("delta_ev_clean", deltas.get("delta_ev", 0.0)))
    sign = "+" if d_c >= 0 else ""

    return [
        (
            f"  $$EV_{{\\text{{pred}}}} = {pred['p_success']:.2f} \\times {pred['impact']:.1f} + "
            f"{mu_val:.1f} \\times {pred['learning_value']:.1f} - {lambda_val:.1f} \\times "
            f"{pred['entropy']:.2f} - {pred['cost']:.2f} = {pred['ev']:.2f}$$"
        ),
        (
            f"  $$EV_{{\\text{{actual}}}} = EV_{{\\text{{clean}}}} = {p_c:.2f} \\times {imp_c:.1f} + "
            f"{mu_val:.1f} \\times {lv:.1f} - {lambda_val:.1f} \\times {ent:.2f} - {cost:.2f} = {ev_c:.2f}$$"
        ),
        (
            f"  $$EV_{{\\text{{with\\_bugs}}}} = {p_b:.2f} \\times {imp_b:.1f} + "
            f"{mu_val:.1f} \\times {lv:.1f} - {lambda_val:.1f} \\times {ent:.2f} - {cost:.2f} = {ev_b:.2f}$$"
        ),
        f"  $$\\text{{Bug EV Drag}} = round(EV_{{\\text{{clean}}}} - EV_{{\\text{{with\\_bugs}}}}, 2) = {drag:.2f}$$",
        f"  $$\\Delta EV = {sign}{d_c:.2f}$$",
    ]
