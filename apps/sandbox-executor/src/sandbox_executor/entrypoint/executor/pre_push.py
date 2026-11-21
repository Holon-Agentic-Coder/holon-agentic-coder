"""Pre-push sync of the execution branch with the fetched target branch, and the record-branch step.

The host fetched the target branch before the container started, so nothing here reads a remote. A merge whose
conflicts are confined to the append-only ledgers is reconciled by pure append; any other conflict aborts the merge.
"""

import os
import sys

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.attribution_check import attribution_allows_publish
from sandbox_executor.entrypoint.executor.commands import _entrypoint_git_runner, _get_repo
from sandbox_executor.entrypoint.executor.repair import _configure_executor_identity
from sandbox_executor.entrypoint.sync_conflict import SyncConflictMessage, record_sync_failure
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger import LedgerRewriteError
from sandbox_executor.redaction import redact_agent_secrets


def sync_and_reconcile_pre_push(repo_dir: str, target_branch: str = "main") -> tuple[bool, str]:
    """Inspect whether the fetched target branch has advanced, merge it, and reconcile ledger conflicts.

    The host fetched the target branch into ``refs/remotes/origin/<target_branch>`` before the container started, so
    this reads no remote. If that ref has diverged from HEAD, attempts an in-flight
    merge. If merge conflicts are strictly confined to holon-knowledge/ledger/*.jsonl,
    resolves each conflicted ledger by pure append (the target branch's bytes stay a prefix and the
    branch's missing lines follow in their original order), stages the resolved files, verifies that
    no unmerged paths remain, and commits the merge. A clean merge leaves the ledgers as git merged them.
    If any non-ledger file has conflicts, aborts the merge via `git merge --abort` and fails loudly.

    Args:
        repo_dir: Working directory of the git repository.
        target_branch: Target base branch name (defaults to "main").

    Returns:
        tuple[bool, str]: (success, message).

    Raises:
        None: Aborts any in-flight merge on failure and returns (False, err_msg).
    """
    repo = _get_repo(repo_dir)
    # 1. The target branch tip the host fetched
    target_ref = f"origin/{target_branch}"
    ref_check = repo.rev_parse(target_ref, verify=True)
    if ref_check.returncode != 0:
        return True, f"Remote branch {target_ref} not found"

    remote_tip = repo.rev_parse(target_ref).stdout.strip()

    # 3. Check divergence with HEAD
    mb_cmd = repo.merge_base("HEAD", target_ref)
    if mb_cmd.returncode != 0:
        print(f"Warning: Unable to compute merge-base with {target_ref}; skipping pre-push sync.", file=sys.stderr)
        return True, f"Unable to compute merge-base with {target_ref}"

    merge_base = mb_cmd.stdout.strip()
    if merge_base == remote_tip:
        return True, f"Branch is already up to date with {target_ref}"

    # 4. Divergence detected: Attempt merge
    _configure_executor_identity(repo_dir)

    merge_res = repo.merge(target_ref, no_commit=True, no_ff=True)
    if merge_res.returncode == 0:
        # A clean merge of append-only ledgers is already correct; rewriting them here would reorder rows.
        staged = repo.diff(cached=True, quiet=True)
        if staged.returncode != 0:
            repo.commit(f"chore(sync): sync target branch {target_branch} before push", check=False)
        return True, f"Cleanly merged {target_ref}"

    return _reconcile_conflicted_merge(repo, repo_dir, target_ref)


def _conflicted_files(repo: GitRepository) -> list[str]:
    """Return the paths left conflicted by an in-flight merge, from the unmerged index or else the porcelain status."""
    unmerged_res = repo.diff("--name-only", "--diff-filter=U")
    conflicted_files = [f.strip() for f in unmerged_res.stdout.splitlines() if f.strip()]
    if not conflicted_files:
        status_res = repo.run(["status", "--porcelain"], check=False)
        for line in status_res.stdout.splitlines():
            if any(line.startswith(c) for c in ("UU ", "AA ", "UD ", "DU ", "AU ", "UA ", "DD ")):
                conflicted_files.append(line[3:].strip())
    return conflicted_files


def _reconcile_conflicted_merge(repo: GitRepository, repo_dir: str, target_ref: str) -> tuple[bool, str]:
    """Resolve an in-flight merge whose conflicts are all append-only ledgers, or abort it and say why."""
    from sandbox_executor.ledger.reconcile import reconcile_ledger_file

    conflicted_files = _conflicted_files(repo)
    non_ledger_files = [
        f for f in conflicted_files if not (f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl"))
    ]
    if non_ledger_files:
        repo.merge_abort()
        err_msg = (
            f"Non-ledger merge conflicts detected with {target_ref} in files: "
            f"{non_ledger_files}. Aborting auto-merge and refusing push."
        )
        print(f"Error: {err_msg}", file=sys.stderr)
        return False, SyncConflictMessage(err_msg, non_ledger_files)

    ledger_conflicts = [f for f in conflicted_files if f.startswith("holon-knowledge/ledger/") and f.endswith(".jsonl")]
    try:
        for ledger_path in ledger_conflicts:
            full_path = os.path.join(repo_dir, ledger_path)
            # The target branch is published history, so its ledger stays a byte prefix of the merge result.
            if not reconcile_ledger_file(full_path, prefix_side="theirs"):
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


class PrePushSteps:
    """The `ExecutionRun` steps that sync the committed execution branch and record it for the host to push.

    Relies on these `ExecutionRun` attributes: ``intent_data``, ``repo_dir``, ``repo``, ``exec_entry``, ``exec_id``,
    ``ledger_dir``, ``plan_tip`` and ``exec_branch``; sets ``branch_recorded``.
    """

    def pre_push_sync(self) -> bool:
        """Merge the fetched target branch and reconcile ledger conflicts; returns whether the branch can be recorded.

        A failed sync records a superseding failure row (see `record_sync_failure`), which decides the result. The host
        fetched the target branch into the worktree before dispatch; no remote is read here.
        """
        intent_data = self.intent_data
        target_base = (
            os.getenv("HOLON_TARGET_BRANCH") or (intent_data.get("target_branch") if intent_data else None) or "main"
        )
        sync_ok, sync_msg = executor_facade.sync_and_reconcile_pre_push(self.repo_dir, target_branch=target_base)
        if sync_ok:
            return True
        return record_sync_failure(self.repo, self.exec_entry, self.exec_id, self.ledger_dir, sync_msg)

    def record_branch(self) -> None:
        """Record the committed execution branch for the host to push, after the safety and sync checks.

        Invariant: a branch with no parent, or one the attribution guard rejects, is never published, whatever the
        execution status. Sets `branch_recorded`; the container itself never pushes.
        """
        can_record = self.repo.rev_parse("HEAD^", verify=True).returncode == 0
        can_record = attribution_allows_publish(self.plan_tip, _entrypoint_git_runner(self.repo_dir)) and can_record
        if can_record:
            can_record = self.pre_push_sync()

        if can_record:
            self.branch_recorded = True
            print(f"Execution branch '{self.exec_branch}' successfully committed locally; the host pushes it.")
        else:
            print("Refusing to publish execution branch: safety or sync verification failed.", file=sys.stderr)
