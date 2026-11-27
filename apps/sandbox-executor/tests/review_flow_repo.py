"""A bare ``origin`` and a flow worktree checked out on the execution branch, for the ledger-commit tests.

A plain helper module (not a test module), so the review-loop, conflict-gate and records tests import it without
importing another test module. Everything lives under ``tmp_path``; nothing reaches the network or this checkout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sandbox_executor.ledger.fragments import pending_rel_dir

from tests.flow.helpers import _git
from tests.flow_repo_templates import template_name
from tests.repo_templates import copy_template, shared_template
from tests.review_loop_stubs import EXEC_TARGET

LEDGER = "holon-knowledge/ledger/executions.jsonl"
LEDGER_PREFIX = "holon-knowledge/ledger/"


def build_flow_worktree(tmp_path: Path, branch: str = EXEC_TARGET) -> tuple[Path, Path]:
    """A bare ``origin`` and a clone on ``branch``, one pushed code commit ahead of ``main``; the ledger is tracked."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / ".gitignore").write_text("holon-knowledge/.index/\n.holon/\n", encoding="utf-8")
    (work / LEDGER).parent.mkdir(parents=True, exist_ok=True)
    (work / LEDGER).write_text(json.dumps({"execution_id": "E-base"}) + "\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", "-b", branch)
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(work, "add", "src/app.py")
    _git(work, "commit", "-q", "-m", "feature")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    _git(work, "fetch", "-q", "origin")
    return origin, work


def flow_worktree_copy(
    tmp_path_factory: pytest.TempPathFactory, target: Path, branch: str = EXEC_TARGET
) -> tuple[Path, Path]:
    template, _ = shared_template(
        tmp_path_factory,
        template_name("flow-worktree", branch),
        lambda root: (build_flow_worktree(root, branch), None)[1],
    )
    copy_template(template, target)
    return target / "origin.git", target / "work"


def append_row(work: Path, row: dict, rel: str = LEDGER) -> None:
    """Append one JSON row to a ledger of the worktree, leaving it uncommitted."""
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def changed_paths(work: Path, rev: str = "HEAD") -> list[str]:
    """The paths ``rev`` changed relative to its first parent."""
    return _git(work, "show", "--name-only", "--format=", rev).splitlines()


def added_rows(work: Path, rev: str = "HEAD", rel: str = LEDGER) -> list[dict]:
    """The ledger rows ``rev`` appended to ``rel`` or to its pending fragments."""
    diff = _git(work, "diff", f"{rev}^", rev, "--", rel, pending_rel_dir(os.path.basename(rel)))
    return [json.loads(line[1:]) for line in diff.splitlines() if line.startswith("+{")]


def ledger_commits(work: Path, rev: str = "HEAD") -> list[str]:
    """The SHAs of the review ledger commits reachable from ``rev``, newest first."""
    out = _git(work, "log", "--format=%H", "--grep=^chore(review): record review ledger rows", rev)
    return out.splitlines()
