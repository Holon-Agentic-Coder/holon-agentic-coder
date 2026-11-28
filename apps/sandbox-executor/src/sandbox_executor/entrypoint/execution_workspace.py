from __future__ import annotations

import os
import re

from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.entrypoint.partial_resume import (
    ResumeStart,
    intent_branch_tip,
    plan_lineage_refusal,
    resume_from_branch_tip,
)
from sandbox_executor.git import GitRepository
from sandbox_executor.ledger.fragments import PENDING_DIR_REL

_PARTIAL_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/E-\d+-[A-Za-z0-9._-]+/_$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_WALK_IGNORED_DIRS = (".git", ".venv", "node_modules")


def workspace_mounted() -> bool:
    return os.getenv("HOLON_TARGET_WORKTREE") == SANDBOX_WORKSPACE_MOUNT


def start_branch_from_host_fetch(repo: GitRepository, repo_dir: str, base_branch: str, new_branch: str) -> None:
    if not workspace_mounted():
        raise RuntimeError("the host worktree is not mounted at the sandbox workspace; this role never clones")
    base_tip = validate_mounted_workspace(repo, repo_dir, base_branch)
    repo.run(["checkout", "-B", new_branch, base_tip])


ENGINE_OWNED_LEDGER_PATHS = (
    "holon-knowledge/ledger/intents.jsonl",
    "holon-knowledge/ledger/plans.jsonl",
    "holon-knowledge/ledger/executions.jsonl",
)
ENGINE_OWNED_LEDGER_DIRS = (PENDING_DIR_REL,)
ENGINE_OWNED_NOTES_GLOB = "executions/E-*.md"
ENGINE_OWNED_PLAN_DIR_PREFIX = "plans/I-"
# why: ledgers and execution notes are engine-owned; interrupted runs leave them dirty and salvage never commits them
_ENGINE_OWNED_EXCLUDES = (
    *(f":(exclude){path}" for path in (*ENGINE_OWNED_LEDGER_PATHS, *ENGINE_OWNED_LEDGER_DIRS)),
    f":(exclude){ENGINE_OWNED_NOTES_GLOB}",
)

# why: proxy sidecar scratch artifacts (wire logs, cache dirs, SQLite databases) never belong in Stage 3 commits
_SCRATCH_DIR_NAMES = frozenset({"mitm_wire_logs", "wire_logs", "wire-logs", "cache", "proxy-cache", "to" + "do"})
_SCRATCH_FILE_RE = re.compile(
    r"(?:^|/)(?:wire_log[^/]*\.(?:json|jsonl|log)|[^/]*\.(?:db|sqlite|sqlite3)(?:-(?:shm|wal|journal))?)$",
    re.IGNORECASE,
)


def is_sidecar_scratch_path(path: str) -> bool:
    clean = path.strip().removeprefix("./").strip("/")
    if not clean:
        return False
    parts = clean.split("/")
    if any(part in _SCRATCH_DIR_NAMES for part in parts):
        return True
    return bool(_SCRATCH_FILE_RE.search(clean))


def split_sidecar_scratch_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    skipped: list[str] = []
    for path in paths:
        (skipped if is_sidecar_scratch_path(path) else kept).append(path)
    return kept, skipped


def format_skipped_scratch_message(skipped: list[str]) -> str:
    joined = ", ".join(dict.fromkeys(skipped))
    return f"Skipping sidecar scratch path(s) from Stage 3 commit: {joined}"


def find_worktree_sidecar_scratch(repo_dir: str) -> list[str]:
    found: list[str] = []
    if not repo_dir or not os.path.isdir(repo_dir):
        return found
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in _WALK_IGNORED_DIRS]
        rel_root = os.path.relpath(root, repo_dir)
        rel_prefix = "" if rel_root == "." else f"{rel_root.replace(os.sep, '/')}/"
        for fname in sorted(files):
            rel_path = f"{rel_prefix}{fname}"
            if is_sidecar_scratch_path(rel_path):
                found.append(rel_path)
    return sorted(found)


def unstage_sidecar_scratch(repo: GitRepository) -> list[str]:
    candidates = find_worktree_sidecar_scratch(repo.repo_dir)
    if not candidates:
        return []
    repo.reset(None, "--", *candidates)
    return candidates


def _is_untracked_engine_plan_entry(line: str) -> bool:
    if not line.startswith("?? "):
        return False
    path = line[3:]
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path.startswith(ENGINE_OWNED_PLAN_DIR_PREFIX) and "/" in path[len(ENGINE_OWNED_PLAN_DIR_PREFIX) :]


def _foreign_status_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line and not _is_untracked_engine_plan_entry(line)]


def validate_mounted_workspace(repo: GitRepository, repo_dir: str, plan_branch: str) -> str:
    repo.run(["config", "--global", "--add", "safe.directory", repo_dir], check=False)
    if repo.rev_parse("HEAD^{commit}", verify=True).returncode != 0:
        raise RuntimeError(f"mounted workspace at '{repo_dir}' has no readable HEAD commit")
    status = repo.run(["status", "--porcelain", "--", ".", *_ENGINE_OWNED_EXCLUDES], check=False)
    if status.returncode != 0 or _foreign_status_lines(status.stdout or ""):
        raise RuntimeError(
            f"mounted workspace at '{repo_dir}' has uncommitted changes, which are kept on the host; commit, stash or "
            "discard them in the host worktree before executing"
        )
    for ref in (f"refs/remotes/origin/{plan_branch}", f"refs/heads/{plan_branch}"):
        tip = repo.rev_parse(f"{ref}^{{commit}}", verify=True)
        if tip.returncode == 0 and (tip.stdout or "").strip():
            return tip.stdout.strip()
    raise RuntimeError(
        f"branch '{plan_branch}' does not resolve in the mounted workspace; the host fetches it before dispatch"
    )


def _resume_refusal(partial_branch: str, plan_branch_prefix: str) -> str | None:
    shaped = _PARTIAL_BRANCH_RE.match(partial_branch) and ".." not in partial_branch
    if not shaped or not partial_branch.startswith(f"{plan_branch_prefix}/E-"):
        return f"'{partial_branch}' is not an execution branch of this plan"
    return None


def resolve_partial_head(
    repo: GitRepository, partial_branch: str, plan_tip: str | None, plan_branch_prefix: str
) -> ResumeStart:
    if not plan_tip:
        return ResumeStart(None, "the plan tip is unknown")
    refusal = _resume_refusal(partial_branch, plan_branch_prefix)
    if refusal:
        return ResumeStart(None, refusal)
    from sandbox_executor.flow.partial_head import failed_partial_head

    exec_id = partial_branch.removesuffix("/_").rsplit("/", 1)[-1]
    branch_ref = f"refs/remotes/origin/{partial_branch}"
    sha = failed_partial_head(repo.repo_dir, branch_ref, exec_id)
    if not sha or not _SHA_RE.match(sha):
        return ResumeStart(None, "no partial_head")
    if repo.run(["cat-file", "-e", f"{sha}^{{commit}}"], check=False).returncode != 0:
        return ResumeStart(None, "the partial head is not on the fetched branch")
    intent_tip = intent_branch_tip(repo, plan_branch_prefix)
    lineage_refusal = plan_lineage_refusal(repo, plan_tip, sha, intent_tip)
    if lineage_refusal:
        return ResumeStart(None, lineage_refusal)
    tip = repo.rev_parse(f"{branch_ref}^{{commit}}", verify=True)
    branch_tip = (tip.stdout or "").strip() if tip.returncode == 0 else None
    return resume_from_branch_tip(repo, branch_tip, sha, (plan_tip, intent_tip), _ENGINE_OWNED_EXCLUDES)


def continuation_note(partial_branch: str, partial_head: str) -> str:
    return (
        f"Continuation: the working tree already holds partial work from the failed execution {partial_branch} "
        f"(commit {partial_head}); its execution notes stay on that branch. Review the existing commits and the "
        "plan's completed steps, then continue from where that run stopped rather than redoing them."
    )
