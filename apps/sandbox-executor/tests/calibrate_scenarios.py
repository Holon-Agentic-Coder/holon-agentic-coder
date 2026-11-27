from __future__ import annotations

import copy
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sandbox_executor.flow import FlowContext, StageResult
from sandbox_executor.flow.calibrate_rerun import RerunSeams
from sandbox_executor.flow.calibrate_stale_warning import STALE_MARKER_FAMILY
from sandbox_executor.flow.github.approval_invalidation import CommentRecord
from sandbox_executor.flow.review.conflict_merge import _merge_target_ledger_only
from sandbox_executor.ref_names import calibration_branch

from tests.repo_templates import copy_template, shared_template

SYNC_EXEC = "I-1-x/P-2-y/E-3-z/_"
SYNC_CALIBRATED = calibration_branch(SYNC_EXEC)
PLAN_ID = "P-2-y"
CAL_SUBJECT = f"chore(calibration): add calibration report for {PLAN_ID}"
CAL_REPORT_REL = f"plans/I-1-x/{PLAN_ID}_calibration.md"
LEDGER_REL = "holon-knowledge/ledger/executions.jsonl"
TARGET_REF = "refs/remotes/origin/main"
SYNC_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def run_sync_git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env={**os.environ, **SYNC_GIT_ENV}
    )
    return res.stdout.strip()


def commit_sync_file(repo: Path, name: str, text: str = "x") -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    run_sync_git(repo, "add", name)
    run_sync_git(repo, "commit", "-qm", f"add {name}")
    return run_sync_git(repo, "rev-parse", "HEAD")


def own_copy(template: SimpleNamespace, target: Path) -> SimpleNamespace:
    copy_template(template.root, target)
    built = vars(template.repos)
    return SimpleNamespace(**{name: target / path.relative_to(template.root) for name, path in built.items()})


def own_path_copy(template_root: Path, target: Path, subdir: str = "repo") -> Path:
    copy_template(template_root, target)
    return target / subdir


# why: a template built from a run a guard refused a call of would hide that call from every later test
def run_guarded_template(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    build: Callable[[Path], Any],
    blocked: Sequence[list[str]],
) -> tuple[Path, Any]:
    def checked_build(root: Path) -> Any:
        value = build(root)
        assert not any(blocked), f"the {name} template made calls a guard blocked: {blocked}"
        return value

    return shared_template(tmp_path_factory, name, checked_build)


def clone_flow_pair(
    template_root: Path, pair: tuple[FlowContext, StageResult], worktree: Path
) -> tuple[FlowContext, StageResult]:
    # why: one copy of both, so the context still holds this very result
    ctx, result = copy.deepcopy(pair)
    if ctx.target_worktree:
        ctx.target_worktree = str(worktree)
    if ctx.repo_dir:
        ctx.repo_dir = str(worktree)
    assert str(template_root) not in repr((ctx, result)), "the cached run recorded a path of the shared template"
    return ctx, result


def build_sync_repos(root: Path) -> SimpleNamespace:
    origin = root / "origin.git"
    run_sync_git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    host = root / "host"
    run_sync_git(root, "clone", "-q", str(origin), str(host))
    run_sync_git(host, "checkout", "-qb", "main")
    commit_sync_file(host, "base.txt")
    run_sync_git(host, "push", "-q", "origin", "main")
    run_sync_git(host, "checkout", "-qb", SYNC_EXEC)
    commit_sync_file(host, "exec.txt")
    run_sync_git(host, "push", "-q", "origin", SYNC_EXEC)
    run_sync_git(host, "checkout", "-qb", "operator-work", "main")
    other = root / "other"
    run_sync_git(root, "clone", "-q", str(origin), str(other))
    run_sync_git(other, "checkout", "-q", SYNC_EXEC)
    return SimpleNamespace(origin=origin, host=host, other=other)


def push_sync_ahead(repos: SimpleNamespace) -> str:
    commit_sync_file(repos.other, "from-main.txt")
    sha = commit_sync_file(repos.other, "ledger-interleave.txt")
    run_sync_git(repos.other, "push", "-q", "origin", SYNC_EXEC)
    return sha


def build_sync_repos_ahead(root: Path) -> SimpleNamespace:
    repos = build_sync_repos(root)
    origin_head = push_sync_ahead(repos)
    return SimpleNamespace(repos=repos, origin_head=origin_head)


def commit_calibration(
    repo: Path, rel: str = CAL_REPORT_REL, subject: str = CAL_SUBJECT, text: str = "report\n"
) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    run_sync_git(repo, "add", rel)
    run_sync_git(repo, "commit", "-qm", subject)
    return run_sync_git(repo, "rev-parse", "HEAD")


def ledger_row(repo: Path, label: str, subject: str = "chore(review): record review rows") -> str:
    path = repo / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"execution_id": label}) + "\n")
    run_sync_git(repo, "add", LEDGER_REL)
    run_sync_git(repo, "commit", "-qm", subject)
    return run_sync_git(repo, "rev-parse", "HEAD")


def advance_main(repos: SimpleNamespace, name: str, text: str = "y\n") -> None:
    other = repos.other
    run_sync_git(other, "checkout", "-q", "main")
    run_sync_git(other, "pull", "-q", "--ff-only", "origin", "main")
    commit_sync_file(other, name, text)
    ledger_row(other, f"E-main-{name}", subject=f"chore: record {name} on main")
    run_sync_git(other, "push", "-q", "origin", "main")
    run_sync_git(other, "checkout", "-q", SYNC_EXEC)
    run_sync_git(other, "fetch", "-q", "origin")


def merge_main(repos: SimpleNamespace, ledger_only: bool = False) -> str:
    outcome = _merge_target_ledger_only(str(repos.other), TARGET_REF, "Merge main into the execution branch")
    assert (outcome.merged, outcome.ledger_only) == (True, ledger_only), outcome
    return run_sync_git(repos.other, "rev-parse", "HEAD")


# why: reproduces the history of PR #258: a report on E, then a ledger-only merge of main and bookkeeping commits
def pr258_history(repos: SimpleNamespace) -> SimpleNamespace:
    other = repos.other
    evaluated = commit_sync_file(other, "feature.py", "x = 1\n")
    commit_calibration(other, text=f"# Calibration Report for {PLAN_ID}\n\n- **Evaluated Commit SHA:** {evaluated}\n")
    advance_main(repos, "main-only.txt")
    # why: both sides append to the ledger, so the merge conflicts there and nowhere else
    ledger_row(other, "E-pr")
    merge = merge_main(repos, ledger_only=True)
    ledger_row(other, "E-review")
    head = commit_calibration(other, rel="holon-knowledge/kb/k.json", subject="chore(calibration): record knowledge")
    run_sync_git(other, "push", "-q", "origin", SYNC_EXEC)
    return SimpleNamespace(evaluated=evaluated, merge=merge, head=head)


def build_clean_merge_repos(root: Path) -> SimpleNamespace:
    repos = build_sync_repos(root)
    evaluated = commit_sync_file(repos.other, "feature.py", "x = 1\n")
    advance_main(repos, "main-only.txt")
    merge_main(repos)
    return SimpleNamespace(repos=repos, evaluated=evaluated)


class FakeThread:
    def __init__(self, *bodies: str) -> None:
        self.records = [CommentRecord(f"IC_{n}", body) for n, body in enumerate(bodies)]
        self.posted: list[str] = []
        self.edits: list[tuple[str, str]] = []

    def comments(self, pr_number: str) -> list[CommentRecord]:
        return list(self.records)

    def poster(self, pr_number: str, body: str) -> bool:
        self.posted.append(body)
        self.records.append(CommentRecord(f"IC_{len(self.records)}", body))
        return True

    def editor(self, comment_id: str, body: str) -> bool:
        index = next((i for i, record in enumerate(self.records) if comment_id and record.id == comment_id), None)
        if index is None:
            return False
        self.edits.append((comment_id, body))
        self.records[index] = CommentRecord(comment_id, body)
        return True

    def seams(self) -> RerunSeams:
        return RerunSeams(comments=self.comments, poster=self.poster, editor=self.editor)

    def stale_bodies(self) -> list[str]:
        return [record.body for record in self.records if STALE_MARKER_FAMILY in record.body]


def build_predicate_repo(root: Path) -> Path:
    repo = root / "predicate"
    repo.mkdir()
    run_sync_git(repo, "init", "-q", "-b", "main")
    commit_sync_file(repo, "base.txt")
    return repo


def build_rebuild_stale_repos(root: Path, report_path: str, report_subject: str) -> SimpleNamespace:
    repos = build_sync_repos(root)
    host = repos.host
    run_sync_git(host, "checkout", "-qb", SYNC_CALIBRATED, SYNC_EXEC)
    target = host / report_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{report_subject}\n", encoding="utf-8")
    run_sync_git(host, "add", report_path)
    run_sync_git(host, "commit", "-qm", report_subject)
    old = run_sync_git(host, "rev-parse", "HEAD")
    run_sync_git(host, "push", "-q", "origin", SYNC_CALIBRATED)
    run_sync_git(host, "checkout", "-q", "operator-work")
    head = push_sync_ahead(repos)
    return SimpleNamespace(repos=repos, old=old, head=head)


def _commit_files(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_sync_git(repo, "add", "--", *files)
    run_sync_git(repo, "commit", "-qm", message)
    return run_sync_git(repo, "rev-parse", "HEAD")


def build_self_check_flow_repo(root: Path, plan: str, exec_branch: str, summary_row: dict[str, Any]) -> Path:
    repo = root / "repo"
    repo.mkdir()
    run_sync_git(repo, "init", "-q", "-b", "main")
    _commit_files(repo, "base", {"base.txt": "base\n"})
    run_sync_git(repo, "checkout", "-qb", plan)
    _commit_files(repo, "plan", {"plans/I-1-sc/P-2-sc.md": "# Plan\n"})
    run_sync_git(repo, "checkout", "-qb", exec_branch)
    _commit_files(repo, "pr one", {"src/own.py": "one\n"})
    run_sync_git(repo, "checkout", "-q", "main")
    _commit_files(repo, "main moves", {"main/other.py": "other\n"})
    run_sync_git(repo, "checkout", "-q", exec_branch)
    run_sync_git(repo, "merge", "-q", "--no-edit", "main")
    ledger = json.dumps(summary_row) + "\n"
    _commit_files(repo, "pr two", {"src/own.py": "two\n", "holon-knowledge/ledger/executions.jsonl": ledger})
    run_sync_git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    return repo


def build_calibration_commit_repo(root: Path, exec_branch: str) -> Path:
    repo = root / "repo"
    repo.mkdir()
    run_sync_git(repo, "init", "-q", "-b", "main")
    summary = {
        "execution_id": "E-3",
        "plan_branch": "I-1-cal/P-2/_",
        "agent": "test-agent",
        "model": "test-model",
        "status": "success",
        "entry_type": "summary",
        "created_at": "2026-01-01T00:00:00.000Z",
        "duration": 12.0,
        "exit_code": 0,
    }
    _commit_files(repo, "base", {"holon-knowledge/ledger/executions.jsonl": json.dumps(summary) + "\n"})
    run_sync_git(repo, "checkout", "-q", "-b", "I-1-cal/P-2/_")
    run_sync_git(repo, "checkout", "-q", "-b", exec_branch)
    _commit_files(repo, "feature", {"feature.py": "print('hello')\n"})
    (repo / "untracked_scratch.txt").write_text("not for commit\n", encoding="utf-8")
    return repo


def build_no_fallback_repo(root: Path, exec_branch: str, ledger_rel: str, seed_row: str) -> Path:
    repo = root / "repo"
    repo.mkdir()
    run_sync_git(repo, "init", "-q", "-b", "main")
    run_sync_git(repo, "config", "user.name", "Test User")
    run_sync_git(repo, "config", "user.email", "test@example.com")
    _commit_files(repo, "initial", {ledger_rel: seed_row})
    run_sync_git(repo, "checkout", "-q", "-b", exec_branch)
    _commit_files(repo, "execution change", {"src.py": "x = 1\n"})
    return repo


def build_evaluated_sha_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    run_sync_git(repo, "init", "-q", "-b", "main")
    _commit_files(repo, "base", {"base.txt": "base\n"})
    run_sync_git(repo, "checkout", "-qb", "exec")
    return repo


def build_evaluated_sha_calibrated_repo(root: Path, report_rel: str, ledger_rel: str) -> SimpleNamespace:
    repo = build_evaluated_sha_repo(root)
    code = _commit_files(repo, "feat: code", {"src/a.py": "feat: code\n"})
    for message, paths in (
        ("chore(calibration): add calibration report for P-2-x", (report_rel, ledger_rel)),
        ("chore(calibration): record stage 5 reconciliation", (ledger_rel,)),
        ("chore(calibration): record stage 5 promoted knowledge and wisdom", ("holon-knowledge/kb/k.json",)),
    ):
        for rel in paths:
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{message}\n")
        run_sync_git(repo, "add", "--", *paths)
        run_sync_git(repo, "commit", "-qm", message)
    return SimpleNamespace(repo=repo, code=code)
