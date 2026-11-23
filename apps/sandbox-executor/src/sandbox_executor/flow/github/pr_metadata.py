"""I/O-free semantic PR metadata: a Conventional Commits title and a structured markdown body from intent data."""

from __future__ import annotations

import re
from typing import Any

CONVENTIONAL_PREFIXES: dict[str, str] = {
    "feature": "feat",
    "feat": "feat",
    "fix": "fix",
    "bugfix": "fix",
    "bug": "fix",
    "refactor": "refactor",
    "refactoring": "refactor",
    "documentation": "docs",
    "doc": "docs",
    "docs": "docs",
    "chore": "chore",
    "test": "test",
    "tests": "test",
    "testing": "test",
    "build": "build",
    "ci": "ci",
    "perf": "perf",
}

_SCOPE_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")
_EXISTING_PREFIX = re.compile(r"^(feat|fix|refactor|docs|chore|test|build|ci|perf)(\([^)]+\))?:\s*", re.IGNORECASE)
_NUMBERED_BULLET = re.compile(r"^\d+[\.\)]")
_NUMBERED_BULLET_MARKER = re.compile(r"^\d+[\.\)]\s*")
_BULLET_STARTS = ("-", "*", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")
_MAX_TITLE_LENGTH = 100


def _conventional_prefix(intent_data: dict[str, Any]) -> str:
    raw_cat = str(intent_data.get("category") or "feat").strip().lower()
    return CONVENTIONAL_PREFIXES.get(raw_cat, "feat")


def _scope(intent_data: dict[str, Any]) -> str:
    """Primary label, else the first slug component, else ``core``; sanitised to ``[a-zA-Z0-9_-]``."""
    scope = ""
    labels = intent_data.get("labels")
    if isinstance(labels, list) and labels:
        scope = str(labels[0]).strip().lower()
    elif isinstance(labels, str) and labels.strip():
        scope = labels.split(",")[0].strip().lower()
    if not scope:
        scope = str(intent_data.get("slug") or "").strip().split("-")[0].strip().lower()
    return _SCOPE_UNSAFE.sub("", scope or "core") or "core"


def _raw_title(intent_data: dict[str, Any]) -> str:
    """First line of the title (or description, goal), without an existing conventional commit prefix."""
    raw_title = str(
        intent_data.get("title") or intent_data.get("description") or intent_data.get("goal") or "Update components"
    ).strip()
    raw_title = raw_title.splitlines()[0].strip()
    return _EXISTING_PREFIX.sub("", raw_title).strip()


def _title(prefix: str, scope: str, raw_title: str) -> str:
    full_title = f"{prefix}({scope}): {raw_title}"
    if len(full_title) > _MAX_TITLE_LENGTH:
        full_title = full_title[: _MAX_TITLE_LENGTH - 3].rstrip() + "..."
    return full_title


def _summary_lines(desc: Any, goal: Any) -> list[str]:
    lines = ["## Summary"]
    if desc:
        lines.append(str(desc).strip())
    if goal and goal != desc:
        if desc:
            lines.append("")
        lines.append(f"**Goal:**\n{str(goal).strip()}")
    if not desc and not goal:
        lines.append("No description provided.")
    return [*lines, ""]


def _goal_bullets(goal: Any) -> list[str] | None:
    """The cleaned bullet lines of a list-shaped goal, or None when the goal has no bullet lines."""
    candidates = [
        line.strip().lstrip("-* ").strip()
        for line in str(goal).strip().splitlines()
        if line.strip().startswith(_BULLET_STARTS) or bool(_NUMBERED_BULLET.match(line.strip()))
    ]
    if not candidates:
        return None
    cleaned = (_NUMBERED_BULLET_MARKER.sub("", cand).strip() for cand in candidates)
    return [f"- {clean}" for clean in cleaned if clean]


def _key_change_lines(goal: Any, raw_title: str) -> list[str]:
    bullets = _goal_bullets(goal) if goal else None
    if bullets is None:
        bullets = [f"- Implement {raw_title}"]
    return ["## Key Changes", *bullets, ""]


def _reference_lines(references: Any) -> list[str]:
    reference_list: list[str] = []
    if isinstance(references, list):
        reference_list = [str(r).strip() for r in references if str(r).strip()]
    elif isinstance(references, str):
        reference_list = [r.strip() for r in references.split(",") if r.strip()]
    line = f"References: {', '.join(reference_list)}" if reference_list else "None"
    return ["## References", line, ""]


def _plan_metadata_lines(plan_metadata: dict[str, Any] | None, intent_data: dict[str, Any]) -> list[str]:
    plan_meta = plan_metadata or {}
    plan_id = plan_meta.get("plan_id") or "N/A"
    ev = plan_meta.get("ev")
    if ev is None and isinstance(plan_meta.get("metrics"), dict):
        ev = plan_meta["metrics"].get("ev")
    ev_str = f"{ev:.2f}" if isinstance(ev, (int, float)) else str(ev or "N/A")
    agent = plan_meta.get("agent") or intent_data.get("agent", "N/A")
    model = plan_meta.get("model") or intent_data.get("model", "N/A")
    return [
        "## Plan & Validation Metadata",
        f"- **Plan ID:** `{plan_id}`",
        f"- **Expected Value (EV):** `{ev_str}`",
        f"- **Agent:** `{agent}`",
        f"- **Model:** `{model}`",
        "",
    ]


def generate_semantic_pr_metadata(
    intent_data: dict[str, Any],
    plan_metadata: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Generate a conventional commit PR title and structured markdown description.

    Transforms intent data and optional plan metadata into Conventional Commits
    title format (e.g. 'feat(scope): title') and a structured markdown PR body
    containing Summary, Key Changes, References, and Plan & Validation Metadata.

    Args:
        intent_data: Dictionary containing intent fields (category, slug, title, description, goal, references, labels).
        plan_metadata: Optional dictionary containing plan fields (plan_id, ev, agent, model).

    Returns:
        tuple[str, str]: (pr_title, pr_body)
    """
    raw_title = _raw_title(intent_data)
    title = _title(_conventional_prefix(intent_data), _scope(intent_data), raw_title)
    goal = intent_data.get("goal")
    body_lines = [
        *_summary_lines(intent_data.get("description"), goal),
        *_key_change_lines(goal, raw_title),
        *_reference_lines(intent_data.get("references")),
        *_plan_metadata_lines(plan_metadata, intent_data),
    ]
    return title, "\n".join(body_lines)
