from __future__ import annotations

from sandbox_executor.git import run_git
from sandbox_executor.ledger.fragments import pending_rel_dir
from sandbox_executor.plan_paths import is_calibration_report_path

LEDGER_EXECUTIONS_REL = "holon-knowledge/ledger/executions.jsonl"
CALIBRATION_SUBJECT_PREFIX = "chore(calibration): "
CALIBRATION_DIR_PREFIXES = ("holon-knowledge/kb/", "holon-knowledge/wisdom/")
LEDGER_DIR_PREFIX = "holon-knowledge/ledger/"
# why: Stage 5 adds at most a handful of commits per run, bounding the first-parent walk.
MAX_EVALUATED_WALK = 50


def _git_out(repo_dir: str, args: list[str]) -> str | None:
    res = run_git(args, cwd=repo_dir, check=False)
    return res.stdout if res.returncode == 0 and isinstance(res.stdout, str) else None


def is_calibration_path(path: str) -> bool:
    return (
        path == LEDGER_EXECUTIONS_REL
        or path.startswith((*CALIBRATION_DIR_PREFIXES, f"{pending_rel_dir('executions.jsonl')}/"))
        or is_calibration_report_path(path)
    )


def _non_merge_commit_paths(repo_dir: str, sha: str) -> list[str] | None:
    parents = _git_out(repo_dir, ["rev-list", "--parents", "-n", "1", sha, "--"])
    paths = _git_out(repo_dir, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, "--"])
    if parents is None or paths is None or len(parents.split()) > 2:
        return None
    return paths.splitlines()


def is_calibration_commit(repo_dir: str, sha: str) -> bool:
    subject = _git_out(repo_dir, ["log", "-1", "--format=%s", sha, "--"])
    paths = _non_merge_commit_paths(repo_dir, sha)
    if subject is None or paths is None:
        return False
    return subject.startswith(CALIBRATION_SUBJECT_PREFIX) and all(map(is_calibration_path, paths))


def is_ledger_only_commit(repo_dir: str, sha: str) -> bool:
    paths = _non_merge_commit_paths(repo_dir, sha)
    return bool(paths) and all(path.startswith(LEDGER_DIR_PREFIX) for path in paths)


# why: the report covers the head the review approved, so Stage 5's own commits on /calibrated are never evaluated.
def evaluated_code_commit(head_ref: str, repo_dir: str) -> str | None:
    listed = _git_out(repo_dir, ["rev-list", "--first-parent", f"--max-count={MAX_EVALUATED_WALK}", head_ref, "--"])
    shas = listed.split() if listed else []
    if not shas:
        return None
    return next((sha for sha in shas if not is_calibration_commit(repo_dir, sha)), shas[0])
