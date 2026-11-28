from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sandbox_executor.flow.context import FlowContext
from sandbox_executor.flow.github.ci import _extract_failed_ci_check_names
from sandbox_executor.flow.github.ci_failure_detail import STEP_LOOKUP_TIMEOUT_SECONDS, short_sha
from sandbox_executor.flow.github.ci_head_checks import HeadChecksProbe, gh_head_checks_probe, is_failed
from sandbox_executor.flow.github.ci_reverify import approved_head_of
from sandbox_executor.flow.review.approval_gates import diff_gate_of
from sandbox_executor.git import run_git

MAIN_BASELINE_FAILING = "main-baseline-failing"
GUARDED_CHECK_TOKENS = ("hygiene", "unit")
_REPO_PATH_RE = re.compile(r"(?:^|[\s'\"(\[])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9_]+)(?=$|[:\s'\"\)\],])")


@dataclass(frozen=True)
class BaselineFailure:
    target_sha: str
    failing_checks: tuple[str, ...]
    failing_paths: tuple[str, ...]
    untouched_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class BaselineGuardSeams:
    probe_head_checks: HeadChecksProbe | None = None
    target_sha_reader: Callable[[str, str], str | None] | None = None
    pr_diff_paths: Callable[[str, str, str], set[str]] | None = None
    failing_paths_lookup: Callable[[Mapping[str, Any]], Sequence[str]] | None = None
    proxy_env: Mapping[str, str] | None = None


def _normalize_path(raw: str) -> str:
    return raw.strip().replace("\\", "/").lstrip("./")


def read_origin_target_sha(repo_dir: str, target_branch: str) -> str | None:
    ref = f"origin/{target_branch}^{{commit}}"
    res = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=repo_dir, check=False)
    return res.stdout.strip() or None if res.returncode == 0 else None


def read_pr_diff_paths(repo_dir: str, target_branch: str, head_ref: str) -> set[str]:
    origin_ref = f"origin/{target_branch}"
    merge_base = run_git(["merge-base", origin_ref, head_ref], cwd=repo_dir, check=False)
    base = merge_base.stdout.strip() if merge_base.returncode == 0 and merge_base.stdout.strip() else origin_ref
    diff = run_git(["diff", "--name-only", f"{base}...{head_ref}"], cwd=repo_dir, check=False)
    if diff.returncode != 0:
        return set()
    return {path for line in diff.stdout.splitlines() if (path := _normalize_path(line))}


def _is_guarded_check(check: Mapping[str, Any], check_names: set[str] | None) -> bool:
    if not is_failed(check):
        return False
    lowered = str(check.get("name") or "").strip().lower()
    if not any(token in lowered for token in GUARDED_CHECK_TOKENS):
        return False
    return check_names is None or lowered in check_names


def _run_gh(argv: list[str], repo_dir: str, proxy_env: Mapping[str, str] | None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(proxy_env or {}), "DOCKER_CLI_HINTS": "false"}
    return subprocess.run(
        argv,
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_LOOKUP_TIMEOUT_SECONDS,
        env=env,
    )


def _annotation_paths(repo_dir: str, check_run_id: int, proxy_env: Mapping[str, str] | None) -> tuple[str, ...]:
    argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/check-runs/{check_run_id}/annotations", "--jq", ".[].path"]
    proc = _run_gh(argv, repo_dir, proxy_env)
    if proc.returncode != 0:
        return ()
    paths = {path for line in proc.stdout.splitlines() if (path := _normalize_path(line)) and path != ".github"}
    return tuple(sorted(paths))


def _failed_log_paths(repo_dir: str, run_id: str, proxy_env: Mapping[str, str] | None) -> tuple[str, ...]:
    proc = _run_gh(["gh", "run", "view", run_id, "--log-failed"], repo_dir, proxy_env)
    if proc.returncode != 0:
        return ()
    found: set[str] = set()
    for match in _REPO_PATH_RE.finditer(proc.stdout):
        path = _normalize_path(match.group(1))
        if path and os.path.exists(os.path.join(repo_dir, path)):
            found.add(path)
    return tuple(sorted(found))


def gh_failing_paths_lookup(
    repo_dir: str, proxy_env: Mapping[str, str] | None = None
) -> Callable[[Mapping[str, Any]], tuple[str, ...]]:
    def lookup(check: Mapping[str, Any]) -> tuple[str, ...]:
        try:
            check_run_id = int(check.get("check_run_id") or 0)
            if check_run_id > 0 and (paths := _annotation_paths(repo_dir, check_run_id, proxy_env)):
                return paths
            run_id = str(check.get("run_id") or "").strip()
            if run_id:
                return _failed_log_paths(repo_dir, run_id, proxy_env)
        # why: fail safe so a transient GitHub API or log lookup failure never suppresses a genuine PR defect
        except Exception:
            return ()
        return ()

    return lookup


def inspect_main_baseline(
    repo_dir: str,
    target_branch: str,
    head_ref: str,
    *,
    check_names: Sequence[str] | None = None,
    seams: BaselineGuardSeams | None = None,
) -> BaselineFailure | None:
    active = seams or BaselineGuardSeams()
    target_sha = (active.target_sha_reader or read_origin_target_sha)(repo_dir, target_branch)
    if not target_sha:
        return None
    probe = active.probe_head_checks or gh_head_checks_probe(repo_dir, active.proxy_env)
    checks = probe(target_sha)
    if not checks:
        return None
    normalized_names = {name.strip().lower() for name in check_names if name.strip()} if check_names else None
    guarded = [check for check in checks if _is_guarded_check(check, normalized_names)]
    if not guarded:
        return None
    path_lookup = active.failing_paths_lookup or gh_failing_paths_lookup(repo_dir, active.proxy_env)
    failing_paths = tuple(
        sorted({_normalize_path(p) for check in guarded for p in path_lookup(check) if _normalize_path(p)})
    )
    if not failing_paths:
        return None
    pr_paths = {
        _normalize_path(p) for p in (active.pr_diff_paths or read_pr_diff_paths)(repo_dir, target_branch, head_ref)
    }
    untouched = tuple(sorted(path for path in failing_paths if path not in pr_paths))
    if not untouched:
        return None
    failing_checks = tuple(dict.fromkeys(str(check.get("name") or "") for check in guarded if check.get("name")))
    sample = ", ".join(untouched[:5])
    checks_str = ", ".join(failing_checks)
    reason = f"origin/{target_branch} ({short_sha(target_sha)}) failing {checks_str} on untouched path(s): {sample}"
    return BaselineFailure(target_sha, failing_checks, failing_paths, untouched, reason)


def is_pr_already_approved(context: FlowContext) -> bool:
    gate = diff_gate_of(context)
    return bool((gate is not None and gate.approved) or approved_head_of(context))


def baseline_blocked_on_record(
    failure: BaselineFailure, head_sha: str, finding_ids: Sequence[str] = ()
) -> dict[str, Any]:
    return {
        "blocked_on": MAIN_BASELINE_FAILING,
        "code": MAIN_BASELINE_FAILING,
        "head_sha": head_sha,
        "target_sha": failure.target_sha,
        "finding_ids": list(finding_ids),
        "failing_checks": list(failure.failing_checks),
        "failing_paths": list(failure.failing_paths),
        "untouched_paths": list(failure.untouched_paths),
        "reason": f"{MAIN_BASELINE_FAILING}: {failure.reason}",
    }


def guard_target_sync(
    context: FlowContext, target_branch: str, head_ref: str, seams: BaselineGuardSeams | None = None
) -> BaselineFailure | None:
    if context.dry_run or not is_pr_already_approved(context):
        return None
    failure = inspect_main_baseline(context.target_worktree, target_branch, head_ref, seams=seams)
    if failure is None:
        return None
    head_sha = approved_head_of(context) or head_ref
    context.review_state["blocked_on"] = baseline_blocked_on_record(failure, head_sha)
    context.log(f"[Baseline Guard] Refusing to sync origin/{target_branch} into approved PR: {failure.reason}.")
    return failure


def classify_baseline_block(
    context: FlowContext,
    target_branch: str,
    head_ref: str,
    failed_names: Sequence[str],
    seams: BaselineGuardSeams | None = None,
) -> dict[str, Any] | None:
    failure = inspect_main_baseline(
        context.target_worktree, target_branch, head_ref, check_names=failed_names or None, seams=seams
    )
    if failure is None:
        return None
    record = baseline_blocked_on_record(failure, head_ref)
    context.review_state["blocked_on"] = record
    return record


def baseline_block_for_ci_finding(
    context: FlowContext,
    target_branch: str,
    resolved_target: Any,
    ci_status: Mapping[str, Any],
    proxy_env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    existing = context.review_state.get("blocked_on")
    if isinstance(existing, dict) and existing.get("blocked_on") == MAIN_BASELINE_FAILING:
        return existing
    failed_names = _extract_failed_ci_check_names(dict(ci_status), False)
    head_ref = (
        (getattr(resolved_target, "head_sha", None) if resolved_target is not None else None)
        or context.execution_branch
        or "HEAD"
    )
    return classify_baseline_block(
        context, target_branch, head_ref, failed_names, BaselineGuardSeams(proxy_env=proxy_env)
    )


def should_preserve_consensus(record: Mapping[str, Any]) -> bool:
    return record.get("blocked_on") == MAIN_BASELINE_FAILING
