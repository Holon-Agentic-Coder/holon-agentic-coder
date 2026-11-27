from __future__ import annotations

import inspect
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args, get_type_hints

import sandbox_executor.flow as flow_pkg
from sandbox_executor.flow.github import (
    ci,
    ci_final_head_seams,
    ci_head_reruns,
    ci_timing_rerun,
    merge,
    pr,
    review_post,
)
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.adapter_runner import GhResult
from sandbox_executor.flow.github.forge import GitForge
from sandbox_executor.flow.review import loop as review_loop

from tests.flow.github.fake_github import FakeGitHub


class StandaloneForge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def proxy_env(self) -> Mapping[str, str] | None:
        return {"HTTP_PROXY": "http://127.0.0.1:8888"}

    @property
    def available(self) -> bool:
        return True

    def pr_view_number(self, branch: str) -> GhResult:
        self.calls.append(("pr_view_number", (branch,)))
        return GhResult(True, 0, json.dumps({"number": 42, "title": "PR"}), "")

    def open_or_update(self, branch: str, base: str, title: str, body: str) -> tuple[str, int | None, GhResult]:
        self.calls.append(("open_or_update", (branch, base, title, body)))
        return "edit", 42, GhResult(True, 0, "https://github.com/o/r/pull/42\n", "")

    def ci_checks(self, target: str, timeout: float) -> GhResult:
        self.calls.append(("ci_checks", (target, timeout)))
        payload = [{"bucket": "pass", "name": "tests", "state": "SUCCESS", "event": "pull_request", "workflow": "CI"}]
        return GhResult(True, 0, json.dumps(payload), "")

    def mergeability(self, branch: str) -> GhResult:
        self.calls.append(("mergeability", (branch,)))
        payload = {"number": 42, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}
        return GhResult(True, 0, json.dumps(payload), "")

    def post_review(self, target: str, flag: str, body: str) -> GhResult:
        self.calls.append(("post_review", (target, flag, body)))
        return GhResult(True, 0, "reviewed", "")

    def post_comment(self, target: str, body: str) -> GhResult:
        self.calls.append(("post_comment", (target, body)))
        return GhResult(True, 0, "commented", "")

    def rerun_failed(self, run_id: str, timeout: float = 60.0) -> bool:
        self.calls.append(("rerun_failed", (run_id, timeout)))
        return True

    def rerun_run(self, run_id: str, timeout: float = 30.0) -> bool:
        self.calls.append(("rerun_run", (run_id, timeout)))
        return True

    def gh(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(("gh", (list(args), cwd)))
        if args[:2] == ["repo", "view"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps({"defaultBranchRef": {"name": "main"}}), "")
        if args[:2] == ["pr", "view"]:
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps({"number": 42, "state": "OPEN"}), "")
        return subprocess.CompletedProcess(["gh", *args], 0, "", "")

    def pr_head_sha(self, target: str, timeout: float = 60.0) -> str | None:
        self.calls.append(("pr_head_sha", (target, timeout)))
        return "deadbeef"


class IncompleteForge:
    available = True


def test_runtime_checkable_conformance() -> None:
    assert isinstance(GitHubAdapter("."), GitForge)
    assert isinstance(FakeGitHub(), GitForge)
    assert isinstance(StandaloneForge(), GitForge)
    assert not isinstance(IncompleteForge(), GitForge)


def test_git_forge_exports_on_flow_package() -> None:
    assert flow_pkg.GitForge is GitForge


def test_git_forge_public_methods_match_github_adapter() -> None:
    protocol_methods = {name for name, value in vars(GitForge).items() if not name.startswith("_") and callable(value)}
    adapter_methods = {
        name for name, value in vars(GitHubAdapter).items() if not name.startswith("_") and callable(value)
    }
    assert protocol_methods == adapter_methods
    for name in protocol_methods:
        proto_params = list(inspect.signature(getattr(GitForge, name)).parameters)
        adapter_params = list(inspect.signature(getattr(GitHubAdapter, name)).parameters)
        assert proto_params == adapter_params, name


def test_standalone_forge_works_with_pr_and_ci_consumers(tmp_path: Path) -> None:
    forge = StandaloneForge()
    repo_dir = str(tmp_path)

    mergeable = pr.check_pr_mergeability(repo_dir, "feat-branch", github=forge)
    assert mergeable["mergeable"] == "MERGEABLE"

    pr_res = pr.create_or_update_pr(repo_dir, "feat-branch", "main", "Title", "Body", github=forge)
    assert pr_res["success"] is True
    assert pr_res["pr_number"] == 42

    ci_res = ci.probe_ci_checks(repo_dir, "feat-branch", github=forge)
    assert ci_res["all_passed"] is True

    rerunner = ci_head_reruns.gh_run_rerunner(repo_dir, github=forge)
    assert rerunner("run-1") is True

    assert ci_timing_rerun._dispatch_rerun(forge, "run-2") is True

    post_res = review_post.post_pr_review_consensus(
        repo_dir, "42", {"approved": True, "score": 1.0, "iterations": 1}, github=forge
    )
    assert post_res["gh_available"] is True
    assert post_res["official_review"]["success"] is True

    runner = merge.gh_runner(None, github=forge)
    assert runner == forge.gh
    assert merge.lookup_default_branch(repo_dir, github=forge) == "main"
    viewed = merge.view_pr(repo_dir, "42", github=forge)
    assert viewed == {"number": 42, "state": "OPEN"}

    seams = ci_final_head_seams.FinalHeadSeams(github=forge)
    assert seams.github is forge


def _annotation_contains_git_forge(annotation: Any) -> bool:
    if annotation is GitForge:
        return True
    args = get_args(annotation)
    return GitForge in args and GitHubAdapter not in args


def test_all_eight_consumers_annotate_github_with_git_forge() -> None:
    targets: list[tuple[str, Any]] = [
        ("pr.check_pr_mergeability", pr.check_pr_mergeability),
        ("pr._gh_mergeability", pr._gh_mergeability),
        ("pr.create_or_update_pr", pr.create_or_update_pr),
        ("ci.probe_ci_checks", ci.probe_ci_checks),
        ("ci_final_head_seams.FinalHeadSeams", ci_final_head_seams.FinalHeadSeams),
        ("ci_final_head_seams._FinalHeadCheck", ci_final_head_seams._FinalHeadCheck),
        ("ci_head_reruns.gh_run_rerunner", ci_head_reruns.gh_run_rerunner),
        ("ci_timing_rerun._dispatch_rerun", ci_timing_rerun._dispatch_rerun),
        ("review_post.post_pr_review_consensus", review_post.post_pr_review_consensus),
        ("review_post._GhCall", review_post._GhCall),
        ("merge.gh_runner", merge.gh_runner),
        ("merge.lookup_default_branch", merge.lookup_default_branch),
        ("merge.view_pr", merge.view_pr),
        ("review_loop.run_review_loop", review_loop.run_review_loop),
        ("review_loop._run_engine_review_loop", review_loop._run_engine_review_loop),
    ]
    for label, obj in targets:
        hints = get_type_hints(obj)
        assert "github" in hints, label
        assert _annotation_contains_git_forge(hints["github"]), f"{label}: {hints['github']!r}"
