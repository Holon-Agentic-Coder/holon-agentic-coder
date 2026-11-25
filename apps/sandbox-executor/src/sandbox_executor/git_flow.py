from __future__ import annotations

import logging
import os
import re
import stat
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sandbox_executor.git import branch_exists, resolve_ref, run_git
from sandbox_executor.redaction import redact_agent_secrets

logger = logging.getLogger(__name__)

SANDBOX_CONTAINER_UID = 1000
SANDBOX_CONTAINER_GID = 1000

_ACL_USER = 0x02
_ACL_MASK = 0x10
_ACL_WRITE = 0x02

_POSIX_ACL_XATTR = "system.posix_acl_access"
_POSIX_ACL_HEADER_BYTES = 4
_POSIX_ACL_ENTRY_BYTES = 8
_POSIX_ACL_ENTRY_FORMAT = "<HHI"

_DEFAULT_BRANCH = "main"
_BRANCH_REF_PREFIXES = ("refs/heads/", "refs/remotes/origin/", "origin/")
_START_POINT_FALLBACKS = ("origin/main", "main", "HEAD", "master")

_HOST_READ_ADMIN_DIRS: tuple[tuple[str, ...], ...] = (
    ("hooks",),
    ("modules",),
    ("worktrees",),
    ("objects", "info"),
    ("info",),
    ("refs", "replace"),
)
_PACKED_REFS_HEADER = "# pack-refs with: peeled fully-peeled sorted \n"
_PRIMARY_WORKTREE_STATE_ENTRIES: tuple[str, ...] = (
    "HEAD",
    "index",
    "FETCH_HEAD",
    "MERGE_HEAD",
    "MERGE_MSG",
    "MERGE_MODE",
    "MERGE_AUTOSTASH",
    "ORIG_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "SQUASH_MSG",
    "AUTO_MERGE",
    "MERGE_RR",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "BISECT_LOG",
    "BISECT_START",
    "BISECT_EXPECTED_REV",
    "BISECT_ANCESTORS_OK",
    "BISECT_NAMES",
    "BISECT_TERMS",
    "BISECT_FIRST_PARENT",
    "BISECT_HEAD",
)

__all__ = [
    "checked_out_branch",
    "default_branch_worktree_problem",
    "derive_worktree_layout",
    "ensure_flow_worktree_off_default",
    "ensure_pr_worktree",
    "ensure_worktree",
    "ephemeral_reviewer_worktree",
    "fetch_origin_branch",
    "find_worktree_for_branch",
    "is_bare_repository",
    "is_default_branch_worktree",
    "is_working_tree",
    "normalise_base_branch",
    "provision_reviewer_worktree",
    "prune_reviewer_worktree",
    "prune_worktrees",
    "repository_default_branch",
    "resolve_review_worktree",
    "sandbox_worktree_mounts",
    "target_worktree_problem",
]

TARGET_WORKTREE_PROBLEM_TEMPLATE: str = (
    "{subject} '{path}' {reason}. holon flow needs --target-worktree to be an attached, non-bare git worktree "
    "because Stages 4 and 5 run host git operations in it. Create one with 'git worktree add <path> <branch>' "
    "from your repository, or pass a bare clone as --target-worktree to auto-provision one{hint}."
)


def is_bare_repository(target_worktree: str) -> bool:
    if not target_worktree or not os.path.exists(target_worktree):
        return False

    res = run_git(["rev-parse", "--is-bare-repository"], cwd=target_worktree, check=False)
    return res.returncode == 0 and res.stdout.strip().lower() == "true"


def is_working_tree(target_worktree: str) -> bool:
    if not target_worktree or not os.path.isdir(target_worktree):
        return False

    res = run_git(["rev-parse", "--is-inside-work-tree"], cwd=target_worktree, check=False)
    if res.returncode != 0 or res.stdout.strip().lower() != "true":
        return False

    return not is_bare_repository(target_worktree)


def prune_worktrees(target_worktree: str) -> None:
    if not target_worktree or not os.path.exists(target_worktree):
        return
    res = run_git(["worktree", "prune"], cwd=target_worktree, check=False)
    if res.returncode == 0:
        logger.debug("Pruned stale worktrees in %s", target_worktree)
    else:
        logger.warning("git worktree prune failed in %s: %s", target_worktree, res.stderr.strip())


def derive_worktree_layout(base_target_worktree: str, slug: str) -> tuple[str, str]:
    clean_slug = _clean_slug(slug) or "flow"
    branch_name = f"flow-{clean_slug}"

    resolved_base = Path(base_target_worktree).resolve()
    if not os.path.exists(base_target_worktree) and resolved_base.name == branch_name:
        worktree_path = resolved_base
    else:
        parent_dir = resolved_base.parent
        worktree_path = parent_dir / branch_name

    return os.path.normpath(str(worktree_path)), branch_name


def _resolve_base_git_dir(target_worktree: str) -> str:
    if os.path.exists(target_worktree) and (
        is_bare_repository(target_worktree) or (Path(target_worktree) / ".git").exists()
    ):
        return os.path.abspath(target_worktree)

    curr = Path(target_worktree).resolve()
    while curr != curr.parent:
        if is_bare_repository(str(curr)) or (curr / ".git").exists():
            return str(curr)
        curr = curr.parent

    res = run_git(["rev-parse", "--git-dir"], cwd=".", check=False)
    if res.returncode == 0:
        top_res = run_git(["rev-parse", "--show-toplevel"], cwd=".", check=False)
        if top_res.returncode == 0 and top_res.stdout.strip():
            return os.path.abspath(top_res.stdout.strip())
        return os.path.abspath(res.stdout.strip())

    raise RuntimeError(f"Unable to resolve a valid Git repository from directory: '{target_worktree}'")


def _resolve_start_point(base_git_dir: str, *preferred: str) -> str:
    candidates = [*preferred, *_START_POINT_FALLBACKS]
    for candidate in candidates:
        if candidate and resolve_ref(base_git_dir, candidate) is not None:
            return candidate
    return "HEAD"


def ensure_worktree(target_worktree: str, slug: str, target_branch: str = "origin/main") -> str:
    clean_slug = slug.strip()
    if not clean_slug:
        raise ValueError("Intent slug must be non-empty to ensure worktree.")

    if os.path.exists(target_worktree) and is_working_tree(target_worktree):
        return os.path.abspath(target_worktree)

    base_git_dir = _resolve_base_git_dir(target_worktree)
    prune_worktrees(base_git_dir)

    worktree_path, branch_name = derive_worktree_layout(target_worktree, clean_slug)
    if os.path.exists(worktree_path) and is_working_tree(worktree_path):
        return os.path.abspath(worktree_path)

    return _attach_or_create_worktree(base_git_dir, worktree_path, branch_name, (target_branch,))


def _attach_or_create_worktree(
    base_git_dir: str,
    worktree_path: str,
    branch_name: str,
    preferred_start_points: tuple[str, ...],
) -> str:
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    if not branch_exists(base_git_dir, branch_name, check_remote=True):
        start_point = _resolve_start_point(base_git_dir, *preferred_start_points)
        run_git(
            ["worktree", "add", "--no-track", "-b", branch_name, worktree_path, start_point],
            cwd=base_git_dir,
            check=True,
        )
        logger.info(
            "Created worktree at '%s' on new branch '%s' from '%s'",
            worktree_path,
            branch_name,
            start_point,
        )
    else:
        run_git(["worktree", "add", worktree_path, branch_name], cwd=base_git_dir, check=True)
        logger.info("Attached worktree at '%s' to existing branch '%s'", worktree_path, branch_name)

    return os.path.abspath(worktree_path)


def _target_worktree_reason(target_worktree: str) -> str | None:
    if not target_worktree or not os.path.exists(target_worktree):
        return "does not exist (it may have been deleted)"
    if not os.path.isdir(target_worktree):
        return "is not a directory"
    if is_bare_repository(target_worktree):
        return "is a bare repository with no working tree"
    if not is_working_tree(target_worktree):
        return "is not inside a git working tree"
    return None


def target_worktree_problem(
    target_worktree: str,
    subject: str = "Target worktree",
    hint: str = "",
) -> str | None:
    reason = _target_worktree_reason(target_worktree)
    if reason is None:
        return None
    return TARGET_WORKTREE_PROBLEM_TEMPLATE.format(
        subject=subject,
        path=os.path.abspath(target_worktree) if target_worktree else "",
        reason=reason,
        hint=hint,
    )


def checked_out_branch(worktree: str) -> str | None:
    if not worktree or not os.path.isdir(worktree):
        return None
    res = run_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=worktree, check=False)
    branch = res.stdout.strip()
    return branch if res.returncode == 0 and branch else None


def normalise_base_branch(target_branch: str | None) -> str:
    name = (target_branch or "").strip()
    for prefix in _BRANCH_REF_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return name or _DEFAULT_BRANCH


def repository_default_branch(worktree: str) -> str:
    if not worktree or not os.path.isdir(worktree):
        return _DEFAULT_BRANCH
    res = run_git(["symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD"], cwd=worktree, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return _DEFAULT_BRANCH
    return normalise_base_branch(res.stdout.strip())


def is_default_branch_worktree(worktree: str, target_branch: str | None) -> bool:
    return is_working_tree(worktree) and checked_out_branch(worktree) == normalise_base_branch(target_branch)


def _worktree_toplevel(worktree: str) -> str:
    res = run_git(["rev-parse", "--show-toplevel"], cwd=worktree, check=False)
    top = res.stdout.strip()
    return os.path.normpath(top) if res.returncode == 0 and top else os.path.abspath(worktree)


def flow_worktree_layout(default_worktree: str, slug: str) -> tuple[str, str]:
    return derive_worktree_layout(_worktree_toplevel(default_worktree), slug)


def ensure_flow_worktree_off_default(default_worktree: str, slug: str, target_branch: str | None) -> str:
    if not slug.strip():
        raise ValueError("Intent slug must be non-empty to provision a flow worktree.")
    base = normalise_base_branch(target_branch)
    toplevel = _worktree_toplevel(default_worktree)
    prune_worktrees(toplevel)
    worktree_path, branch_name = derive_worktree_layout(toplevel, slug)
    if os.path.exists(worktree_path):
        if not is_working_tree(worktree_path):
            raise RuntimeError(
                f"Flow worktree path '{worktree_path}' exists but is not a git working tree; move it aside or "
                f"attach it with 'git worktree add {worktree_path} {branch_name}'."
            )
        if checked_out_branch(worktree_path) == base:
            raise RuntimeError(
                f"Flow worktree '{worktree_path}' has the base branch '{base}' checked out; check out "
                f"'{branch_name}' in it or remove it with 'git worktree remove {worktree_path}'."
            )
        logger.info("Reused flow worktree at '%s'", worktree_path)
        return os.path.abspath(worktree_path)
    if resolve_ref(toplevel, f"origin/{base}") is None:
        logger.warning("'origin/%s' does not resolve in '%s'; the flow branch starts from a fallback.", base, toplevel)
    return _attach_or_create_worktree(toplevel, worktree_path, branch_name, (f"origin/{base}", base))


def default_branch_worktree_problem(
    worktree: str,
    base_branch: str | None,
    flow_worktree: str | None = None,
    command: str = "holon flow",
    subject: str = "Target worktree",
) -> str | None:
    if not is_default_branch_worktree(worktree, base_branch):
        return None
    base = normalise_base_branch(base_branch)
    if flow_worktree:
        remedy = f"Use the flow worktree instead: pass --target-worktree {flow_worktree}"
    else:
        remedy = (
            f"Run it in the run's flow-<slug> worktree or another dedicated worktree instead: create one with "
            f"'git worktree add <path> <branch>' (or 'git worktree add <path> -b <branch> origin/{base}' for a new "
            f"branch) and pass --target-worktree <path>"
        )
    return (
        f"{subject} '{os.path.abspath(worktree)}' has the default branch '{base}' checked out. {command} checks out "
        f"branches, commits and writes ledger or calibration records in the worktree it runs in, and the "
        f"default-branch worktree must stay identical to origin/{base}. {remedy}."
    )


def find_worktree_for_branch(worktree: str, branch: str) -> str | None:
    if not worktree or not os.path.isdir(worktree) or not branch.strip():
        return None
    res = run_git(["worktree", "list", "--porcelain"], cwd=worktree, check=False)
    if res.returncode != 0:
        return None
    path: str | None = None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch.strip()}" and path and os.path.isdir(path):
            return os.path.normpath(path)
    return None


def _clean_slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]+", "-", text.strip()).strip("-")


def ensure_pr_worktree(worktree: str, pr_number: int, head_branch: str) -> str:
    toplevel = _worktree_toplevel(worktree)
    prune_worktrees(toplevel)
    existing = find_worktree_for_branch(toplevel, head_branch)
    if existing:
        logger.info("Reused worktree '%s' for PR #%s (%s)", existing, pr_number, head_branch)
        return existing
    path = os.path.join(os.path.dirname(toplevel), f"pr{pr_number}-{_clean_slug(head_branch) or 'head'}")
    if os.path.exists(path):
        raise RuntimeError(
            f"PR worktree path '{path}' exists but does not have '{head_branch}' checked out; check the branch out "
            f"in it or remove it with 'git worktree remove {path}'."
        )
    run_git(
        ["fetch", "--no-tags", "origin", f"+refs/heads/{head_branch}:refs/remotes/origin/{head_branch}"],
        cwd=toplevel,
        check=False,
        timeout=300,
    )
    if not branch_exists(toplevel, head_branch, check_remote=True):
        raise RuntimeError(
            f"PR #{pr_number} head branch '{head_branch}' exists neither locally nor on origin (a fork PR?); check it "
            "out in a worktree and pass --target-worktree <path>."
        )
    return _attach_or_create_worktree(toplevel, path, head_branch, (f"origin/{head_branch}",))


def resolve_review_worktree(worktree: str, head_branch: str, pr_number: int | None = None) -> str:
    if checked_out_branch(worktree) == head_branch:
        return _worktree_toplevel(worktree)
    existing = find_worktree_for_branch(worktree, head_branch)
    if existing:
        return existing
    if pr_number is not None:
        return ensure_pr_worktree(worktree, pr_number, head_branch)
    raise RuntimeError(
        f"No worktree has branch '{head_branch}' checked out; the review-loop container reviews a host worktree in "
        f"place. Create one with 'git worktree add <path> {head_branch}' and pass --target-worktree <path>."
    )


def _reviewer_worktree_path(worktree: str, index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError(f"reviewer index must be a positive integer, got {index!r}")
    return f"{_worktree_toplevel(worktree)}-rev{index}"


def prune_reviewer_worktree(worktree: str, index: int) -> None:
    path = _reviewer_worktree_path(worktree, index)
    if os.path.exists(path):
        res = run_git(["worktree", "remove", "--force", path], cwd=worktree, check=False)
        if res.returncode != 0:
            logger.warning("git worktree remove failed for '%s': %s", path, res.stderr.strip())
    prune_worktrees(worktree)


def provision_reviewer_worktree(worktree: str, index: int) -> str:
    path = _reviewer_worktree_path(worktree, index)
    prune_reviewer_worktree(worktree, index)
    run_git(["worktree", "add", "--detach", path, "HEAD"], cwd=worktree, check=True)
    return os.path.abspath(path)


@contextmanager
def ephemeral_reviewer_worktree(worktree: str, index: int) -> Iterator[str]:
    path = provision_reviewer_worktree(worktree, index)
    try:
        yield path
    finally:
        prune_reviewer_worktree(worktree, index)


def fetch_origin_branch(worktree: str, branch: str) -> None:
    res = run_git(
        ["fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=worktree,
        check=False,
    )
    if res.returncode != 0:
        detail = redact_agent_secrets((res.stderr or "").strip()) or f"exit code {res.returncode}"
        raise RuntimeError(f"could not fetch branch '{branch}' from origin into '{worktree}': {detail}")


def _read_pointer(path: str, prefix: str = "") -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content.startswith(prefix):
        return None
    target = content[len(prefix) :].strip()
    return os.path.normpath(os.path.join(os.path.dirname(path), target)) if target else None


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _acl_user_write(path: str, uid: int) -> bool | None:
    try:
        acl = os.getxattr(path, _POSIX_ACL_XATTR)
    except (AttributeError, OSError):
        return None
    entry_stop = len(acl) - _POSIX_ACL_ENTRY_BYTES + 1
    entries = [
        struct.unpack_from(_POSIX_ACL_ENTRY_FORMAT, acl, offset)
        for offset in range(_POSIX_ACL_HEADER_BYTES, entry_stop, _POSIX_ACL_ENTRY_BYTES)
    ]
    user_perms = [perm for tag, perm, entry_id in entries if tag == _ACL_USER and entry_id == uid]
    if not user_perms:
        return None
    mask_perms = [perm for tag, perm, _ in entries if tag == _ACL_MASK]
    return bool(user_perms[0] & _ACL_WRITE) and all(perm & _ACL_WRITE for perm in mask_perms)


def _container_cannot_write(path: str) -> bool:
    st = os.stat(path)
    if st.st_uid == SANDBOX_CONTAINER_UID:
        return not st.st_mode & stat.S_IWUSR
    acl_write = _acl_user_write(path, SANDBOX_CONTAINER_UID)
    if acl_write is not None:
        return not acl_write
    if st.st_gid == SANDBOX_CONTAINER_GID:
        return not st.st_mode & stat.S_IWGRP
    return not st.st_mode & stat.S_IWOTH


def _container_write_problem(role: str, *paths: str) -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    unwritable = [path for path in dict.fromkeys(paths) if _container_cannot_write(path)]
    if not unwritable:
        return None
    return (
        f"the {role} container runs as uid {SANDBOX_CONTAINER_UID} and a Linux bind mount keeps host ownership, "
        f"so it cannot write {', '.join(repr(p) for p in unwritable)}. Run holon as uid {SANDBOX_CONTAINER_UID}, "
        f"or grant it write access (for example 'setfacl -R -m u:{SANDBOX_CONTAINER_UID}:rwX,"
        f"d:u:{SANDBOX_CONTAINER_UID}:rwX <path>')."
    )


def _validated_sandbox_toplevel(target_worktree: str | None, role: str) -> str:
    if not target_worktree:
        raise RuntimeError(
            f"the {role} container works on a host worktree in place and needs an explicit target worktree; pass "
            "--target-worktree <path> (never the current directory implicitly)."
        )
    problem = target_worktree_problem(target_worktree, subject="Sandbox worktree")
    if problem:
        raise RuntimeError(problem)
    toplevel = _worktree_toplevel(target_worktree)
    problem = default_branch_worktree_problem(
        toplevel, repository_default_branch(toplevel), command=f"The {role} container", subject="Sandbox worktree"
    )
    if problem:
        raise RuntimeError(problem)
    worktree_config = run_git(
        ["config", "--type=bool", "--get", "extensions.worktreeConfig"], cwd=toplevel, check=False
    )
    if (worktree_config.stdout or "").strip() == "true":
        raise RuntimeError(
            f"Sandbox worktree '{toplevel}' belongs to a repository with extensions.worktreeConfig enabled; a "
            "container could plant a config.worktree that later runs code on the host. Disable it with 'git config "
            "--unset extensions.worktreeConfig' or review in a fresh clone."
        )
    return toplevel


def sandbox_worktree_mounts(target_worktree: str | None, mountpoint: str, role: str = "review-loop") -> list[str]:
    toplevel = _validated_sandbox_toplevel(target_worktree, role)
    dot_git = os.path.join(toplevel, ".git")
    args = ["-v", f"{toplevel}:{mountpoint}"]
    if os.path.isdir(dot_git):
        git_dir = common_dir = dot_git
        read_only: list[str] = []
    else:
        git_dir = _read_pointer(dot_git, "gitdir:") or ""
        if not os.path.isdir(git_dir):
            raise RuntimeError(f"cannot resolve the git directory of worktree '{toplevel}' from its .git file")
        common_dir = _read_pointer(os.path.join(git_dir, "commondir")) or git_dir
        args += ["-v", f"{common_dir}:{common_dir}"]
        if git_dir != common_dir:
            args += ["-v", f"{git_dir}:{git_dir}"]
        read_only = [dot_git, os.path.join(git_dir, "commondir"), os.path.join(git_dir, "gitdir")]
    problem = _container_write_problem(role, toplevel, git_dir, common_dir)
    if problem:
        raise RuntimeError(problem)
    read_only += _common_dir_read_only_overlays(common_dir, git_dir)
    read_only.append(os.path.join(git_dir, "config.worktree"))

    for host_path in dict.fromkeys(read_only):
        if not os.path.exists(host_path):
            continue
        rel = os.path.relpath(host_path, toplevel)
        container_path = os.path.join(mountpoint, rel) if _within(host_path, toplevel) else host_path
        args += ["-v", f"{host_path}:{container_path}:ro"]
    return args


def _is_rerere_enabled(common_dir: str) -> bool:
    rerere_res = run_git(["--git-dir", common_dir, "config", "--type=bool", "--get", "rerere.enabled"], check=False)
    return rerere_res.returncode == 0 and rerere_res.stdout.strip() == "true"


def _ensure_inert_shallow_sentinel(common_dir: str) -> str:
    shallow = os.path.join(common_dir, "shallow")
    if not os.path.isfile(shallow):
        os.makedirs(shallow, exist_ok=True)
    return shallow


def _ensure_rerere_cache_sentinel(common_dir: str) -> str:
    rr_cache = os.path.join(common_dir, "rr-cache")
    if _is_rerere_enabled(common_dir):
        if os.path.isfile(rr_cache) and os.path.getsize(rr_cache) == 0:
            os.remove(rr_cache)
        if os.path.exists(rr_cache) and not os.path.isdir(rr_cache):
            raise RuntimeError(
                f"{rr_cache} is a non-empty regular file but rerere is enabled: host git needs a directory there; "
                "remove the stray file and retry"
            )
        os.makedirs(rr_cache, exist_ok=True)
    elif not os.path.isdir(rr_cache):
        open(rr_cache, "a", encoding="utf-8").close()
    return rr_cache


def _ensure_host_read_config_files(common_dir: str, git_dir: str) -> None:
    for path in dict.fromkeys(
        (
            os.path.join(common_dir, "config"),
            os.path.join(common_dir, "config.worktree"),
            os.path.join(git_dir, "config.worktree"),
        )
    ):
        open(path, "a", encoding="utf-8").close()


def _ensure_inert_packed_refs(common_dir: str) -> str:
    packed_refs = os.path.join(common_dir, "packed-refs")
    if not os.path.exists(packed_refs):
        with open(packed_refs, "w", encoding="utf-8") as handle:
            handle.write(_PACKED_REFS_HEADER)
    return packed_refs


def _common_dir_read_only_overlays(common_dir: str, git_dir: str) -> list[str]:
    for parts in _HOST_READ_ADMIN_DIRS:
        os.makedirs(os.path.join(common_dir, *parts), exist_ok=True)
    shallow = _ensure_inert_shallow_sentinel(common_dir)
    rr_cache = _ensure_rerere_cache_sentinel(common_dir)
    _ensure_host_read_config_files(common_dir, git_dir)
    packed_refs = _ensure_inert_packed_refs(common_dir)
    overlays = [
        os.path.join(common_dir, "config"),
        os.path.join(common_dir, "config.worktree"),
        *(os.path.join(common_dir, *parts) for parts in _HOST_READ_ADMIN_DIRS),
        packed_refs,
        shallow,
        rr_cache,
    ]
    if git_dir != common_dir:
        overlays += [os.path.join(common_dir, name) for name in _PRIMARY_WORKTREE_STATE_ENTRIES]
    return overlays
