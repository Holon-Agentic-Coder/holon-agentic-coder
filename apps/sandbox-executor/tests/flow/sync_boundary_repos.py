"""The fixed repositories of test_sync_boundaries.py, served from read-only templates.

Each scenario is what ``build_flow_repos`` followed by its fixed ``commit_on`` calls gave, built once per test run by
``tests.flow_repo_templates.flow_history_copy`` and copied per test without a git process. Commits a test makes after
a production call stay in the test, because their order matters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.flow_repo_templates import FlowCommit, flow_history_copy
from tests.ledger_conflict_repos import FLOW_BRANCH

LEDGER = "holon-knowledge/ledger/executions.jsonl"
APP = "src/app.py"
BASE_ROW = json.dumps({"execution_id": "E-base", "created_at": "2026-11-01T00:00:00Z"}) + "\n"
BASE = {LEDGER: BASE_ROW, APP: "x = 0\n"}
CODE_BASE = {**BASE, ".gitignore": "tmp/\n"}
TARGET_CODE = FlowCommit("main", {APP: "y = 2\n"}, "target code")
FLOW_CODE = FlowCommit(FLOW_BRANCH, {APP: "x = 1\n"}, "flow code")
CLEAN_TARGET_MOVE = FlowCommit("main", {"docs/clean.md": "clean\n"}, "clean target move")


def ledger_conflict(idx: int) -> tuple[FlowCommit, FlowCommit]:
    """A newer row on ``main`` and an older one on the flow branch, each appended to the base ledger."""
    main_row = json.dumps({"execution_id": f"E-main-{idx}", "created_at": f"2026-11-0{idx}T02:00:00Z"}) + "\n"
    flow_row = json.dumps({"execution_id": f"E-flow-{idx}", "created_at": f"2026-11-0{idx}T01:00:00Z"}) + "\n"
    return (
        FlowCommit("main", {LEDGER: BASE_ROW + main_row}, f"main row {idx}"),
        FlowCommit(FLOW_BRANCH, {LEDGER: BASE_ROW + flow_row}, f"flow row {idx}"),
    )


def sync_conflict_commit(conflict_row: dict[str, object]) -> FlowCommit:
    """The executor's commit of a ``sync_conflict`` summary row over a conflicting code change."""
    files = {APP: "x = 1\n", LEDGER: BASE_ROW + json.dumps(conflict_row) + "\n"}
    return FlowCommit(FLOW_BRANCH, files, "executor sync_conflict commit")


def sync_work(
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
    commits: tuple[FlowCommit, ...] = (),
    base: dict[str, str] = BASE,
) -> Path:
    """The ``work`` clone of a private copy of ``build_flow_repos(tmp_path, base)`` plus ``commit_on`` per commit."""
    return flow_history_copy(tmp_path_factory, tmp_path, base, commits).work
