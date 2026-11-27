import json
import os
import subprocess
import sys
from typing import Any

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.attribution_check import attribution_allows_publish
from sandbox_executor.entrypoint.executor.commands import _entrypoint_git_runner, _get_repo
from sandbox_executor.entrypoint.executor.repair import _configure_executor_identity
from sandbox_executor.entrypoint.sync_conflict import (
    SyncConflictMessage,
    record_sync_failure,
    superseding_failure_row,
)
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import LedgerRewriteError, atomic_ledger_append
from sandbox_executor.ledger.fragments import ledger_commit_paths
from sandbox_executor.redaction import redact_agent_secrets

_LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"
_LEDGER_FILE_SUFFIX = ".jsonl"
_CONFLICT_STATUS_PREFIXES: tuple[str, ...] = ("UU ", "AA ", "UD ", "DU ", "AU ", "UA ", "DD ")
_PUBLISHED_TARGET_PREFIX_SIDE = "theirs"
SELF_EXPLAINING_CHECKER = "scripts/check_self_explaining.py"
SELF_EXPLAINING_VIOLATION_EXIT = 1
SELF_EXPLAINING_TIMEOUT_SECONDS = 600
SELF_EXPLAINING_SUMMARY_LIMIT = 20
SELF_EXPLAINING_FAILURE_REASON = "self_explaining_violation"


def _is_ledger_path(path: str) -> bool:
    return path.startswith(_LEDGER_DIR_PREFIX) and path.endswith(_LEDGER_FILE_SUFFIX)


def sync_and_reconcile_pre_push(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]:
    repo = _get_repo(repo_dir)
    target_ref = f"origin/{target_branch}"
    ref_check = repo.rev_parse(target_ref, verify=True)
    if ref_check.returncode != 0:
        return True, f"Remote branch {target_ref} not found"

    remote_tip = repo.rev_parse(target_ref).stdout.strip()

    mb_cmd = repo.merge_base("HEAD", target_ref)
    if mb_cmd.returncode != 0:
        print(f"Warning: Unable to compute merge-base with {target_ref}; skipping pre-push sync.", file=sys.stderr)
        return True, f"Unable to compute merge-base with {target_ref}"

    merge_base = mb_cmd.stdout.strip()
    if merge_base == remote_tip:
        return True, f"Branch is already up to date with {target_ref}"

    _configure_executor_identity(repo_dir)

    merge_res = repo.merge(target_ref, no_commit=True, no_ff=True)
    if merge_res.returncode == 0:
        staged = repo.diff(cached=True, quiet=True)
        if staged.returncode != 0:
            repo.commit(f"chore(sync): sync target branch {target_branch} before push", check=False)
        return True, f"Cleanly merged {target_ref}"

    return _reconcile_conflicted_merge(repo, repo_dir, target_ref)


def _conflicted_files(repo: GitRepository) -> list[str]:
    unmerged_res = repo.diff("--name-only", "--diff-filter=U")
    conflicted_files = [f.strip() for f in unmerged_res.stdout.splitlines() if f.strip()]
    if not conflicted_files:
        status_res = repo.run(["status", "--porcelain"], check=False)
        for line in status_res.stdout.splitlines():
            if any(line.startswith(prefix) for prefix in _CONFLICT_STATUS_PREFIXES):
                conflicted_files.append(line[3:].strip())
    return conflicted_files


def _reconcile_conflicted_merge(repo: GitRepository, repo_dir: str, target_ref: str) -> tuple[bool, str]:
    from sandbox_executor.ledger.reconcile import reconcile_ledger_file

    conflicted_files = _conflicted_files(repo)
    non_ledger_files = [path for path in conflicted_files if not _is_ledger_path(path)]
    if non_ledger_files:
        repo.merge_abort()
        err_msg = (
            f"Non-ledger merge conflicts detected with {target_ref} in files: "
            f"{non_ledger_files}. Aborting auto-merge and refusing push."
        )
        print(f"Error: {err_msg}", file=sys.stderr)
        return False, SyncConflictMessage(err_msg, non_ledger_files)

    ledger_conflicts = [path for path in conflicted_files if _is_ledger_path(path)]
    try:
        for ledger_path in ledger_conflicts:
            full_path = os.path.join(repo_dir, ledger_path)
            if not reconcile_ledger_file(full_path, prefix_side=_PUBLISHED_TARGET_PREFIX_SIDE):
                repo.merge_abort()
                return False, f"Failed to reconcile ledger file {ledger_path}"
            repo.add(ledger_path, check=True)

        rem_unmerged = repo.diff("--name-only", "--diff-filter=U")
        if rem_unmerged.stdout.strip():
            rem_paths = rem_unmerged.stdout.strip()
            repo.merge_abort()
            return False, f"Unmerged paths remain after ledger reconciliation: {rem_paths}"

        repo.commit(f"chore(ledger): reconcile concurrent ledger updates with {target_ref}", check=True)
        return True, f"Successfully reconciled ledger conflicts and committed merge with {target_ref}"
    except LedgerRewriteError:
        repo.merge_abort()
        raise
    except Exception as exc:
        repo.merge_abort()
        return False, f"Exception during ledger reconciliation merge: {redact_agent_secrets(str(exc))}"


def violations_on_tracked_paths(repo_dir: str, checker_stderr: str) -> list[str]:
    tracked_paths = set(_get_repo(repo_dir).run(["ls-files"], check=False).stdout.splitlines())
    return [
        line for line in checker_stderr.splitlines() if ": added " in line and line.split(":", 1)[0] in tracked_paths
    ]


def run_self_explaining_check(repo_dir: str) -> tuple[bool, str]:
    checker = os.path.join(repo_dir, SELF_EXPLAINING_CHECKER)
    if not os.path.isfile(checker):
        return True, f"{SELF_EXPLAINING_CHECKER} not found; the pre-push self-explaining check was skipped"
    try:
        completed = subprocess.run(
            [sys.executable, checker, "--root", repo_dir],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=SELF_EXPLAINING_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _continue_since_ci_fails_closed(redact_agent_secrets(str(exc)))
    if completed.returncode == 0:
        return True, completed.stdout
    if completed.returncode != SELF_EXPLAINING_VIOLATION_EXIT:
        return _continue_since_ci_fails_closed(
            f"exit code {completed.returncode}: {redact_agent_secrets(completed.stderr)}"
        )
    violations = violations_on_tracked_paths(repo_dir, completed.stderr)
    if not violations:
        notice = "Notice: the self-explaining check flagged only untracked files; continuing."
        print(notice)
        return True, notice
    return False, "\n".join(violations)


def _continue_since_ci_fails_closed(detail: str) -> tuple[bool, str]:
    warning = f"Warning: the pre-push self-explaining check could not run ({detail}); continuing."
    print(warning, file=sys.stderr)
    return True, warning


def record_self_explaining_refusal(
    repo: GitRepository, exec_entry: dict[str, Any], exec_id: str, ledger_dir: str, violations: str
) -> None:
    listed = "; ".join(violations.splitlines()[:SELF_EXPLAINING_SUMMARY_LIMIT])
    summary = (
        f"Pre-push self-explaining check refused the branch: {listed}. Remove the added docstrings and comments, "
        "or turn a needed reason into a '# why:' comment."
    )
    ledger_row = {**superseding_failure_row(exec_entry, summary), "failure_reason": SELF_EXPLAINING_FAILURE_REASON}
    with atomic_ledger_append(os.path.join(ledger_dir, "executions.jsonl")) as ledger_file:
        ledger_file.write(json.dumps(ledger_row) + "\n")
    repo.add(ledger_commit_paths(os.path.dirname(os.path.dirname(ledger_dir)), "executions.jsonl"), check=False)
    repo.commit(f"execute: record self-explaining refusal for {exec_id}", check=False)


class PrePushSteps:
    def self_explaining_gate(self) -> bool:
        if self.exec_entry.get("status") != "success":
            return True
        passed, output = run_self_explaining_check(self.repo_dir)
        if passed:
            return True
        print(f"Error: Self-explaining check failed before push:\n{output}", file=sys.stderr)
        record_self_explaining_refusal(self.repo, self.exec_entry, self.exec_id, self.ledger_dir, output)
        self.exec_status = "failure"
        return False

    def pre_push_sync(self) -> bool:
        intent_data = self.intent_data
        target_base = (
            os.getenv("HOLON_TARGET_BRANCH") or (intent_data.get("target_branch") if intent_data else None) or "main"
        )
        sync_ok, sync_msg = executor_facade.sync_and_reconcile_pre_push(self.repo_dir, target_branch=target_base)
        if sync_ok:
            return True
        return record_sync_failure(self.repo, self.exec_entry, self.exec_id, self.ledger_dir, sync_msg)

    def record_branch(self) -> None:
        can_record = self.repo.rev_parse("HEAD^", verify=True).returncode == 0
        can_record = attribution_allows_publish(self.plan_tip, _entrypoint_git_runner(self.repo_dir)) and can_record
        if can_record:
            can_record = self.self_explaining_gate()
        if can_record:
            can_record = self.pre_push_sync()

        if can_record:
            self.branch_recorded = True
            print(f"Execution branch '{self.exec_branch}' successfully committed locally; the host pushes it.")
        else:
            print("Refusing to publish execution branch: safety or sync verification failed.", file=sys.stderr)
