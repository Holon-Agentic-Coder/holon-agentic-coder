"""Stage 5 touch-set accuracy: score the plan's ``## Touch Set`` against the execution diff and render it.

``calibration.py`` calls these two functions; the parsing and matching live in ``flow/touch_set.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

TOUCH_SET_REPORT_ITEM_CAP = 20
_NO_TOUCH_SET: dict[str, Any] = {"precision": None, "recall": None, "unpredicted_paths": [], "unused_entries": []}


def touch_set_metrics(plan_meta: dict[str, Any], changed_paths: list[str], target_worktree: str) -> dict[str, Any]:
    """Score the plan's touch set against the execution's changed paths; ``None`` values when there is none.

    The touch set comes from the ``plans.jsonl`` row, or from the plan file's ``## Touch Set`` section when the row was
    not found and ``parse_predicted_metrics`` fell back to the markdown (its record then has no ``plan_id``). Advisory:
    a malformed row or unreadable plan file never fails calibration.
    """
    # Imported here: calibration imports this module and the flow package imports calibration, so a top-level import
    # would cycle through flow/__init__.py.
    from sandbox_executor.flow.touch_set import TouchSet, parse_touch_set_section, score_touch_set

    try:
        if "plan_id" in plan_meta or "touch_set" in plan_meta or not plan_meta.get("plan_file"):
            touch_set = TouchSet.from_row(plan_meta.get("touch_set"))
        else:
            with open(os.path.join(target_worktree, plan_meta["plan_file"]), encoding="utf-8") as f:
                touch_set = parse_touch_set_section(f.read())
        if touch_set is None:
            return dict(_NO_TOUCH_SET)
        return score_touch_set(touch_set, list(changed_paths)).to_dict()
    except (OSError, TypeError, ValueError) as e:
        logger.warning("Could not score the plan's touch set: %s", e)
        return dict(_NO_TOUCH_SET)


def _capped_code_bullets(items: list[str]) -> list[str]:
    lines = [f"- `{item}`" for item in items[:TOUCH_SET_REPORT_ITEM_CAP]]
    if len(items) > TOUCH_SET_REPORT_ITEM_CAP:
        lines.append(f"- and {len(items) - TOUCH_SET_REPORT_ITEM_CAP} more")
    return lines or ["- none"]


def touch_set_report_lines(report_data: dict[str, Any]) -> list[str]:
    """Render the touch-set accuracy section: precision and recall (or ``n/a``), unpredicted paths, unused entries."""
    score = report_data.get("touch_set") or _NO_TOUCH_SET

    def percent(value: float | None) -> str:
        return "n/a" if value is None else f"`{value * 100:.1f}%`"

    lines = [
        "## Touch Set Accuracy",
        "",
        f"- **Precision (declared entries the diff touched):** {percent(score.get('precision'))}",
        f"- **Recall (changed files the touch set covered):** {percent(score.get('recall'))}",
    ]
    if score.get("precision") is None and score.get("recall") is None and not score.get("unpredicted_paths"):
        return [*lines, "", "---", ""]
    return [
        *lines,
        "",
        "Changed files outside the touch set:",
        "",
        *_capped_code_bullets(list(score.get("unpredicted_paths") or [])),
        "",
        "Declared entries the diff did not touch:",
        "",
        *_capped_code_bullets(list(score.get("unused_entries") or [])),
        "",
        "---",
        "",
    ]
