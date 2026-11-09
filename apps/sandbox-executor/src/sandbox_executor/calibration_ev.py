"""Dual EV evaluation and reporting helpers for Stage 5 calibration.

Computes Clean / Intrinsic EV, Observed EV with intermediate failures,
and Bug EV Drag to distinguish pipeline/environment failures from agent delivery.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class DualEVMetrics:
    """Computed dual actual EV metrics and bug drag."""

    ev_clean: float = 0.0
    ev_with_bugs: float = 0.0
    p_success_clean: float = 0.0
    p_success_with_bugs: float = 0.0
    impact_clean: float = 0.0
    impact_with_bugs: float = 0.0
    bug_ev_drag: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert metrics to dictionary."""
        return asdict(self)


def is_matching_record(record: dict[str, Any], execution_id: str, plan_branch: str) -> bool:
    """Check if summary record matches execution_id or plan_branch."""
    if record.get("entry_type", "summary") != "summary":
        return False
    rec_exec_id = str(record.get("execution_id", ""))
    rec_pb = str(record.get("plan_branch", "")).rstrip("/_")
    norm_pb = plan_branch.rstrip("/_") if plan_branch else ""
    return bool((execution_id and rec_exec_id == execution_id) or (norm_pb and rec_pb == norm_pb))


def evaluate_dual_ev_outcomes(
    records: list[dict[str, Any]],
    execution_id: str,
    plan_branch: str,
    predicted_impact: float,
    cost_effort: float,
) -> DualEVMetrics:
    """Evaluate dual actual EV outcomes and bug drag across matching summary records."""
    matching = [r for r in records if is_matching_record(r, execution_id, plan_branch)]
    if not matching:
        matching = [r for r in records if r.get("entry_type", "summary") == "summary"]
    if not matching:
        p_c = 1.0
        imp_c = float(predicted_impact)
        c_ev = round((imp_c * p_c) - cost_effort, 2)
        return DualEVMetrics(
            ev_clean=c_ev,
            ev_with_bugs=c_ev,
            p_success_clean=p_c,
            p_success_with_bugs=p_c,
            impact_clean=imp_c,
            impact_with_bugs=imp_c,
            bug_ev_drag=0.0,
        )

    def sort_key(rec: dict[str, Any]) -> tuple[int, str]:
        return (int(rec.get("ledger_revision", 1)), str(rec.get("created_at", "")))

    sorted_records = sorted(matching, key=sort_key)
    final_record = sorted_records[-1]

    final_status = str(final_record.get("status", "success"))
    default_exit = 0 if final_status in ("success", "halted_for_human") else 1
    final_exit = int(final_record.get("exit_code", default_exit))

    is_converged = final_status in ("success", "halted_for_human") and final_exit == 0
    p_clean = 1.0 if is_converged else 0.0
    imp_clean = float(predicted_impact) if is_converged else 0.0
    ev_clean = round((imp_clean * p_clean) - cost_effort, 2)

    has_failure = any(
        str(r.get("status", "success")) == "failed"
        or int(r.get("exit_code", 0 if str(r.get("status", "success")) in ("success", "halted_for_human") else 1)) != 0
        for r in sorted_records
    )

    if has_failure:
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
    """Parse JSONL lines and extract summary records."""
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
    """Format markdown table rows for Clean EV, Observed EV, and Bug EV Drag."""
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
    """Format mathematical derivation lines for dual EV and bug drag."""
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
