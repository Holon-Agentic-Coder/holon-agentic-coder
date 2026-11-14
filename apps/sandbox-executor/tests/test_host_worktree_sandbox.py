"""Hermetic tests for role containers working in place on a bind-mounted host worktree.

Real git repositories under ``tmp_path`` with a local bare remote stand in for the host; no Docker daemon or network is
needed. A child Python process plays the container wherever a process boundary matters (an abrupt exit). The real
container round trip is covered by ``test_review_workspace_integration.py``. One test per goal:

(a) the worktree is validated and bind-mounted with its git directories, and git works on those paths;
(b) the entrypoint starts on the mounted worktree with no clone, fetch, reset or clean;
(c) files the container writes appear on the host worktree at once;
(d) an abrupt exit (code 137) leaves uncommitted edits, untracked files and logs intact on the host;
(e) intermediate subagent artifacts live under ``<worktree>/tmp/review/iter-<n>/``, which git ignores;
(f) the recorded docker commands create no named volume and launch no root chown init container;
(g) resumed runs and Stage 4 reviews reuse ``flow-<slug>`` and never create ``pr<N>-<slug>`` beside it;
(h) the shared worktree stays the host's: no planted config.worktree, no duplicated or swept-up ledger rows.

The Stage 3 executor is covered the same way: the host provisions or reuses flow-<slug> and fetches the plan branch, and
the entrypoint branches from the plan tip in the mounted worktree, refusing a dirty one, with no clone or fetch.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import git_flow
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.entrypoint.execution_workspace import validate_mounted_workspace

MOUNT = "/sandbox/workspace"

#: Plays the review-loop container: prepares the mounted worktree, writes an edit, an untracked file and a log, then
#: dies with the OOM-kill exit code before any cleanup could run.
_CONTAINER_SNIPPET = """
import os
from sandbox_executor.entrypoint.review_loop import _prepare_workspace
ws = os.environ["HOLON_TARGET_WORKTREE"]
print("HEAD", _prepare_workspace(ws, "flow-demo").head_sha, flush=True)
with open(os.path.join(ws, "README.md"), "a") as f:
    f.write("uncommitted edit\\n")
with open(os.path.join(ws, "notes.txt"), "w") as f:
    f.write("untracked scratch\\n")
os.makedirs(os.path.join(ws, "tmp", "review", "iter-1"), exist_ok=True)
with open(os.path.join(ws, "tmp", "review", "iter-1", "pytest.log"), "w") as f:
    f.write("1 passed\\n")
os._exit(137)
"""


def _git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A primary checkout on main with a local bare origin; git never reads the host's own config.

    The host user plays the container user, so the write preflight holds whatever uid runs the tests.
    """
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_UID", os.getuid())
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_GID", os.getgid())
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Holon Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@holon.com")
    remote = tmp_path / "remote.git"
    main = tmp_path / "repo"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "init", "-q", "-b", "main", str(main))
    (main / "README.md").write_text("seed\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "seed")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")
    return main.resolve()


@pytest.fixture
def flow_worktree(repo: Path) -> Path:
    """The run's flow-demo worktree: a linked worktree beside the primary checkout, on branch flow-demo."""
    path = repo.parent / "flow-demo"
    _git(repo, "worktree", "add", "-q", "-b", "flow-demo", str(path))
    return path.resolve()


def _mount_pairs(args: list[str]) -> list[str]:
    assert args[::2] == ["-v"] * (len(args) // 2), args
    return args[1::2]


def _host_writable(args: list[str], host_path: str) -> bool:
    """Whether the container can write host_path through the mount plan, judged as docker layers bind mounts.

    Docker applies the mount with the deeper destination over the shallower one, so for every container path a mount
    maps host_path to, the deepest-destination mount covering that container path decides read-only or read-write.
    """
    table = [(pair.split(":")[0], pair.split(":")[1], pair.endswith(":ro")) for pair in _mount_pairs(args)]

    def effective_read_only(container_path: str) -> bool:
        covering = [m for m in table if container_path == m[1] or container_path.startswith(m[1] + "/")]
        return max(covering, key=lambda m: len(m[1]))[2]

    return any(
        (host_path == source or host_path.startswith(source + "/"))
        and not effective_read_only(destination + host_path[len(source) :])
        for source, destination, _ in table
    )


# --- (a) validation, bind mounts and git directory resolution -------------------------------------------------------


def test_a_linked_worktree_mounts_its_git_dirs_at_host_paths_and_overlays_git_code_paths_read_only(
    repo: Path, flow_worktree: Path
) -> None:
    common = repo / ".git"
    git_dir = common / "worktrees" / "flow-demo"

    mounts = _mount_pairs(git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT))

    assert mounts == [
        f"{flow_worktree}:{MOUNT}",
        # The .git pointer names these host paths, so they are mounted at the identical path in the container.
        f"{common}:{common}",
        # The own admin dir is re-mounted read-write over the read-only worktrees/ overlay (deeper mount wins).
        f"{git_dir}:{git_dir}",
        # Nothing the container writes may later run code on the host.
        f"{flow_worktree / '.git'}:{MOUNT}/.git:ro",
        f"{git_dir / 'commondir'}:{git_dir / 'commondir'}:ro",
        f"{git_dir / 'gitdir'}:{git_dir / 'gitdir'}:ro",
        f"{common / 'config'}:{common / 'config'}:ro",
        f"{common / 'config.worktree'}:{common / 'config.worktree'}:ro",
        f"{common / 'hooks'}:{common / 'hooks'}:ro",
        f"{common / 'modules'}:{common / 'modules'}:ro",
        f"{common / 'worktrees'}:{common / 'worktrees'}:ro",
        # The whole directories, not single files: a new host-read file (info/grafts, a commit-graph) cannot be
        # planted beside the known ones.
        f"{common / 'objects' / 'info'}:{common / 'objects' / 'info'}:ro",
        f"{common / 'info'}:{common / 'info'}:ro",
        # refs/ stays writable for loose refs; replace refs, packed-refs and shallow are host-trusted and pinned
        # (shallow as a created directory, since an empty shallow FILE would itself mark the repository shallow).
        f"{common / 'refs' / 'replace'}:{common / 'refs' / 'replace'}:ro",
        f"{common / 'packed-refs'}:{common / 'packed-refs'}:ro",
        f"{common / 'shallow'}:{common / 'shallow'}:ro",
        # rr-cache/ was absent, so a zero-length rr-cache FILE stand-in was created and overlaid: inert for git,
        # unlike a created directory, whose mere presence would turn host rerere on.
        f"{common / 'rr-cache'}:{common / 'rr-cache'}:ro",
        # The common HEAD and index are the PRIMARY checkout's; the linked worktree resolves its own through its
        # admin dir. FETCH_HEAD, the transients (MERGE_HEAD/MERGE_MSG/MERGE_MODE/ORIG_HEAD/CHERRY_PICK_HEAD/
        # MERGE_AUTOSTASH/REVERT_HEAD/SQUASH_MSG/AUTO_MERGE/MERGE_RR), the rebase state (REBASE_HEAD,
        # rebase-merge/, rebase-apply/), the sequencer/ directory and the BISECT_* files do not exist here, so no
        # overlay is generated for them.
        f"{common / 'HEAD'}:{common / 'HEAD'}:ro",
        f"{common / 'index'}:{common / 'index'}:ro",
        f"{git_dir / 'config.worktree'}:{git_dir / 'config.worktree'}:ro",
    ]
    # The rr-cache stand-in is a zero-length FILE: inert for host git, unlike a directory, and unreplaceable as a
    # read-only mountpoint in-container.
    assert (common / "rr-cache").is_file()
    assert (common / "rr-cache").read_text(encoding="utf-8") == ""
    # The mounted pointer resolves through the mounted paths, without any network: git works there.
    assert (flow_worktree / ".git").read_text(encoding="utf-8").strip() == f"gitdir: {git_dir}"
    assert _git(flow_worktree, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")
    assert _git(flow_worktree, "status", "--porcelain") == ""


def test_a_primary_checkout_needs_no_extra_git_mount_but_still_protects_config_and_hooks(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")

    args = git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

    assert _mount_pairs(args) == [
        f"{repo}:{MOUNT}",
        f"{repo / '.git' / 'config'}:{MOUNT}/.git/config:ro",
        f"{repo / '.git' / 'config.worktree'}:{MOUNT}/.git/config.worktree:ro",
        f"{repo / '.git' / 'hooks'}:{MOUNT}/.git/hooks:ro",
        f"{repo / '.git' / 'modules'}:{MOUNT}/.git/modules:ro",
        f"{repo / '.git' / 'worktrees'}:{MOUNT}/.git/worktrees:ro",
        f"{repo / '.git' / 'objects' / 'info'}:{MOUNT}/.git/objects/info:ro",
        f"{repo / '.git' / 'info'}:{MOUNT}/.git/info:ro",
        f"{repo / '.git' / 'refs' / 'replace'}:{MOUNT}/.git/refs/replace:ro",
        f"{repo / '.git' / 'packed-refs'}:{MOUNT}/.git/packed-refs:ro",
        f"{repo / '.git' / 'shallow'}:{MOUNT}/.git/shallow:ro",
        f"{repo / '.git' / 'rr-cache'}:{MOUNT}/.git/rr-cache:ro",
    ]
    # The primary checkout's HEAD, index and FETCH_HEAD are its own: they must stay writable for in-container
    # commits and fetches, and its own mid-operation state (rebase, sequencer) is never overlaid either --
    # the per-worktree overlays are gated to the linked case.
    assert _host_writable(args, str(repo / ".git" / "HEAD"))
    assert _host_writable(args, str(repo / ".git" / "index"))
    assert _host_writable(args, str(repo / ".git" / "FETCH_HEAD"))
    assert _host_writable(args, str(repo / ".git" / "rebase-merge" / "git-rebase-todo"))
    assert _host_writable(args, str(repo / ".git" / "sequencer" / "todo"))
    # An absent worktrees/, config.worktree, objects/info/, info/, refs/replace/, packed-refs or shallow is created
    # before overlaying, so the container cannot create it -- nor any host-read file inside the read-only
    # directories, existing or not. shallow is created as a DIRECTORY: an empty shallow file would itself mark the
    # repository shallow, while git treats a shallow directory as no shallow file at all. rr-cache is the inverse:
    # an empty rr-cache DIRECTORY would itself turn host rerere on, while a zero-length rr-cache FILE is inert, so
    # the absent shared rerere cache is pinned as a created read-only file in both layouts.
    assert (repo / ".git" / "worktrees").is_dir()
    assert (repo / ".git" / "config.worktree").read_text(encoding="utf-8") == ""
    assert (repo / ".git" / "objects" / "info").is_dir()
    assert (repo / ".git" / "info").is_dir()
    assert (repo / ".git" / "refs" / "replace").is_dir()
    # Created with the standard header line only: inert like an absent packed-refs, but free of the
    # emptyPackedRefsFile warning host 'git fsck' reports for a zero-byte file.
    packed_refs = (repo / ".git" / "packed-refs").read_text(encoding="utf-8")
    assert packed_refs == "# pack-refs with: peeled fully-peeled sorted \n"
    assert (repo / ".git" / "shallow").is_dir()
    assert (repo / ".git" / "rr-cache").is_file()
    assert (repo / ".git" / "rr-cache").read_text(encoding="utf-8") == ""
    for planted in (
        repo / ".git" / "info" / "grafts",
        repo / ".git" / "objects" / "info" / "commit-graph",
        repo / ".git" / "objects" / "info" / "commit-graphs" / "commit-graph-chain",
        repo / ".git" / "refs" / "replace" / "0123456789abcdef0123456789abcdef01234567",
        repo / ".git" / "packed-refs",
        repo / ".git" / "shallow",
        repo / ".git" / "rr-cache",
        repo / ".git" / "rr-cache" / "0123456789abcdef0123456789abcdef01234567" / "postimage",
    ):
        assert not _host_writable(args, str(planted)), planted
    # Host git still behaves as a normal full clone with the created artifacts in place.
    assert _git(repo, "rev-parse", "--is-shallow-repository") == "false"
    _git(repo, "fetch", "-q", "origin")
    assert _git(repo, "log", "--oneline", "-1")


@pytest.mark.parametrize("enabled", ["true", "yes", "1", "on"])
def test_rr_cache_is_a_read_only_empty_directory_when_host_rerere_is_enabled(repo: Path, enabled: str) -> None:
    # A stand-in FILE would make a conflicting host merge die ('could not create directory') and break 'git gc'.
    # Truthy spellings are normalised by 'git config --type=bool'.
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "config", "rerere.enabled", enabled)

    args = git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

    assert (repo / ".git" / "rr-cache").is_dir()
    assert list((repo / ".git" / "rr-cache").iterdir()) == []
    assert f"{repo / '.git' / 'rr-cache'}:{MOUNT}/.git/rr-cache:ro" in _mount_pairs(args)
    assert not _host_writable(args, str(repo / ".git" / "rr-cache" / "0123456789abcdef" / "postimage"))


def test_a_stand_in_rr_cache_file_from_an_earlier_mount_is_replaced_by_a_directory_once_rerere_is_enabled(
    repo: Path,
) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".git" / "rr-cache").write_text("", encoding="utf-8")
    _git(repo, "config", "rerere.enabled", "true")

    git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

    assert (repo / ".git" / "rr-cache").is_dir()


def test_rr_cache_is_an_inert_read_only_file_when_host_rerere_is_disabled_or_unset(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    for setting in (None, "false"):
        if setting is not None:
            _git(repo, "config", "rerere.enabled", setting)

        args = git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

        assert f"{repo / '.git' / 'rr-cache'}:{MOUNT}/.git/rr-cache:ro" in _mount_pairs(args)
        assert (repo / ".git" / "rr-cache").is_file()
        assert (repo / ".git" / "rr-cache").read_text(encoding="utf-8") == ""
        (repo / ".git" / "rr-cache").unlink()


def test_a_non_empty_rr_cache_file_is_refused_when_host_rerere_is_enabled(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "config", "rerere.enabled", "true")
    (repo / ".git" / "rr-cache").write_text("stray\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"non-empty regular file but rerere is enabled"):
        git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

    # The refusal leaves the stray file exactly as found: nothing is removed or replaced before raising.
    assert (repo / ".git" / "rr-cache").is_file()
    assert (repo / ".git" / "rr-cache").read_text(encoding="utf-8") == "stray\n"


def test_a_sibling_worktree_admin_dirs_are_unwritable_while_own_admin_and_object_database_stay_writable(
    repo: Path, flow_worktree: Path
) -> None:
    """A compromised container must not reach any sibling worktree's administrative files -- its commondir, gitdir,
    HEAD or a plantable config.worktree -- or create a new worktrees/ entry, because host git reads those pointer and
    config files on its next command in that worktree. The mounted worktree's own git writes must survive."""
    sibling = repo.parent / "sibling"
    _git(repo, "worktree", "add", "-q", "-b", "sibling", str(sibling))
    common = repo / ".git"
    own = common / "worktrees" / "flow-demo"
    sib = common / "worktrees" / "sibling"
    # The primary checkout has fetched and sits mid-merge (with cherry-pick/revert, rebase, sequencer and bisect
    # state planted alongside), so its per-worktree state at the common root exists and must be overlaid-if-present.
    # A container-writable rebase-merge/git-rebase-todo would be arbitrary host code execution: host
    # 'git rebase --continue' runs its 'exec' lines.
    head = _git(repo, "rev-parse", "HEAD")
    for transient in ("FETCH_HEAD", "MERGE_HEAD", "ORIG_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD"):
        (common / transient).write_text(head + "\n", encoding="utf-8")
    (common / "AUTO_MERGE").write_text(head + "\n", encoding="utf-8")
    (common / "MERGE_MSG").write_text("merge msg\n", encoding="utf-8")
    (common / "MERGE_MODE").write_text("", encoding="utf-8")
    (common / "MERGE_AUTOSTASH").write_text(head + "\n", encoding="utf-8")
    (common / "MERGE_RR").write_text("0123456789abcdef0123456789abcdef01234567\tREADME.md\n", encoding="utf-8")
    (common / "SQUASH_MSG").write_text("squash msg\n", encoding="utf-8")
    (common / "BISECT_LOG").write_text("git bisect start\n", encoding="utf-8")
    (common / "BISECT_START").write_text("main\n", encoding="utf-8")
    (common / "BISECT_EXPECTED_REV").write_text(head + "\n", encoding="utf-8")
    (common / "BISECT_ANCESTORS_OK").write_text("", encoding="utf-8")
    (common / "BISECT_NAMES").write_text("\n", encoding="utf-8")
    (common / "BISECT_TERMS").write_text("bad\ngood\n", encoding="utf-8")
    (common / "BISECT_HEAD").write_text(head + "\n", encoding="utf-8")
    (common / "BISECT_FIRST_PARENT").write_text("", encoding="utf-8")
    # A pre-existing rerere cache: a container-rewritten postimage would be applied silently to the maintainer's
    # next conflicting host merge, so the existing directory is overlaid read-only as a whole.
    (common / "rr-cache" / "0123456789abcdef0123456789abcdef01234567").mkdir(parents=True)
    (common / "rr-cache" / "0123456789abcdef0123456789abcdef01234567" / "postimage").write_text(
        "resolved\n", encoding="utf-8"
    )
    (common / "rebase-merge").mkdir()
    (common / "rebase-merge" / "git-rebase-todo").write_text("exec true\n", encoding="utf-8")
    (common / "rebase-apply").mkdir()
    (common / "rebase-apply" / "next").write_text("1\n", encoding="utf-8")
    (common / "sequencer").mkdir()
    (common / "sequencer" / "todo").write_text(f"pick {head} subject\n", encoding="utf-8")

    args = git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)

    # The sibling's admin dir and the worktrees/ dir itself (so no new entry can be planted) are read-only.
    for path in (
        sib,
        sib / "commondir",
        sib / "gitdir",
        sib / "HEAD",
        sib / "index",
        sib / "config.worktree",
        sib / "planted-new-file",
        common / "worktrees",
        common / "worktrees" / "planted-new-worktree",
    ):
        assert not _host_writable(args, str(path)), path
    # Everything that makes host git run code stays read-only, including the own pointer files.
    for path in (
        common / "config",
        common / "config.worktree",
        common / "hooks" / "pre-commit",
        common / "modules",
        own / "commondir",
        own / "gitdir",
        own / "config.worktree",
        common / "objects" / "info" / "alternates",
        common / "objects" / "info" / "commit-graph",
        common / "objects" / "info" / "commit-graphs" / "commit-graph-chain",
        common / "info" / "exclude",
        common / "info" / "attributes",
        common / "info" / "grafts",
        # Host-trusted ancestry and ref-substitution surfaces: replace refs (loose and packed), a plantable
        # shallow, the shared rerere cache (its existing entries included), and the PRIMARY checkout's
        # per-worktree state (HEAD, index, FETCH_HEAD and the transients, MERGE_RR among them -- a sibling's
        # MERGE_RR lives in its worktrees/<name>/ admin dir and is covered by the worktrees/ overlay).
        common / "refs" / "replace",
        common / "refs" / "replace" / "0123456789abcdef0123456789abcdef01234567",
        common / "packed-refs",
        common / "shallow",
        common / "rr-cache",
        common / "rr-cache" / "0123456789abcdef0123456789abcdef01234567" / "postimage",
        common / "HEAD",
        common / "index",
        common / "FETCH_HEAD",
        common / "MERGE_HEAD",
        common / "MERGE_MSG",
        common / "MERGE_MODE",
        common / "ORIG_HEAD",
        common / "CHERRY_PICK_HEAD",
        common / "REVERT_HEAD",
        common / "REBASE_HEAD",
        common / "AUTO_MERGE",
        common / "MERGE_AUTOSTASH",
        common / "MERGE_RR",
        common / "SQUASH_MSG",
        common / "BISECT_LOG",
        common / "BISECT_START",
        common / "BISECT_EXPECTED_REV",
        common / "BISECT_ANCESTORS_OK",
        common / "BISECT_NAMES",
        common / "BISECT_TERMS",
        common / "BISECT_HEAD",
        common / "BISECT_FIRST_PARENT",
        common / "rebase-merge",
        common / "rebase-merge" / "git-rebase-todo",
        common / "rebase-apply",
        common / "rebase-apply" / "next",
        common / "sequencer",
        common / "sequencer" / "todo",
        flow_worktree / ".git",
    ):
        assert not _host_writable(args, str(path)), path
    # The mounted worktree's own git still works: its admin dir, the shared object database and loose refs are
    # writable (fetch/commit/push write loose refs; only ref deletion and pack-refs rewrite packed-refs).
    for path in (
        own / "HEAD",
        own / "index",
        own / "FETCH_HEAD",
        common / "objects",
        common / "refs",
        common / "refs" / "heads" / "flow-demo",
        flow_worktree / "README.md",
    ):
        assert _host_writable(args, str(path)), path


def test_a_primary_checkout_with_linked_worktrees_keeps_their_admin_dirs_unwritable(repo: Path) -> None:
    """When the mounted worktree is the primary checkout, its linked worktrees' admin dirs under .git/worktrees/ are
    reachable through the read-write workspace mount; the worktrees/ overlay must make them read-only while the
    checkout's own .git state (HEAD, index, objects, refs) stays writable."""
    linked = repo.parent / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "linked", str(linked))
    _git(repo, "checkout", "-q", "-b", "feature")
    admin = repo / ".git" / "worktrees" / "linked"

    args = git_flow.sandbox_worktree_mounts(str(repo), MOUNT)

    for path in (admin, admin / "commondir", admin / "gitdir", admin / "HEAD", repo / ".git" / "worktrees"):
        assert not _host_writable(args, str(path)), path
    for path in (
        repo / ".git" / "info" / "exclude",
        repo / ".git" / "info" / "attributes",
        repo / ".git" / "info" / "grafts",
        repo / ".git" / "objects" / "info" / "commit-graph",
        repo / ".git" / "refs" / "replace",
        repo / ".git" / "packed-refs",
        repo / ".git" / "shallow",
    ):
        assert not _host_writable(args, str(path)), path
    # The primary's own per-worktree state stays writable: the overlays above apply to it only when it is the
    # sibling of the mounted worktree, not when it is the mounted worktree itself.
    for path in (repo / ".git" / "HEAD", repo / ".git" / "index", repo / ".git" / "objects", repo / ".git" / "refs"):
        assert _host_writable(args, str(path)), path


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("none", "needs an explicit target worktree"),
        ("missing", "does not exist"),
        ("plain-dir", "is not inside a git working tree"),
        ("bare", "is a bare repository"),
        ("default-branch", "has the default branch 'main' checked out"),
    ],
)
def test_a_only_an_attached_non_default_branch_worktree_is_ever_mounted(
    repo: Path, tmp_path: Path, case: str, message: str
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    target = {
        "none": None,
        "missing": str(tmp_path / "gone"),
        "plain-dir": str(plain),
        "bare": str(tmp_path / "remote.git"),
        "default-branch": str(repo),
    }[case]

    with pytest.raises(RuntimeError, match=message):
        git_flow.sandbox_worktree_mounts(target, MOUNT)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the uid preflight applies to Linux bind mounts")
def test_a_a_worktree_the_container_uid_cannot_write_is_refused_before_any_container_starts(
    flow_worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A CI runner whose user is uid 1001 owns a 0755 worktree that the image's uid cannot write.
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_UID", os.getuid() + 1)
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_GID", os.getgid() + 1)
    common = flow_worktree.parent / "repo" / ".git"
    git_dirs = (flow_worktree, common, common / "worktrees" / "flow-demo")
    for path in git_dirs:
        path.chmod(0o755)

    with pytest.raises(RuntimeError, match=rf"runs as uid {os.getuid() + 1}.*cannot write.*{flow_worktree}"):
        git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)

    # Group write for the container's gid is enough.
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_GID", os.getgid())
    for path in git_dirs:
        path.chmod(0o775)
    assert git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the uid preflight applies to Linux bind mounts")
def test_a_a_named_user_acl_grants_the_container_uid_write_access(
    flow_worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 'setfacl -m u:<uid>:rwX' on a 0755 worktree owned by another uid, as the refusal message recommends.
    uid = os.getuid() + 1
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_UID", uid)
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_GID", os.getgid() + 1)
    common = flow_worktree.parent / "repo" / ".git"
    for path in (flow_worktree, common, common / "worktrees" / "flow-demo"):
        path.chmod(0o755)

    def acl(user_perm: int, mask_perm: int) -> bytes:
        none = 0xFFFFFFFF
        entries = [(0x01, 7, none), (0x02, user_perm, uid), (0x04, 5, none), (0x10, mask_perm, none), (0x20, 5, none)]
        return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)

    monkeypatch.setattr(git_flow.os, "getxattr", lambda path, name: acl(7, 7), raising=False)
    assert git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)

    # The ACL mask still limits a named-user entry, and a read-only entry is refused.
    for user_perm, mask_perm in ((7, 5), (5, 7)):
        blob = acl(user_perm, mask_perm)
        monkeypatch.setattr(git_flow.os, "getxattr", lambda path, name, blob=blob: blob, raising=False)
        with pytest.raises(RuntimeError, match=rf"runs as uid {uid}.*cannot write"):
            git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)


# --- (b) no clone, fetch, reset or clean in the container ------------------------------------------------------------


def test_b_entrypoint_prepares_the_mounted_worktree_without_any_destructive_git_command(flow_worktree: Path) -> None:
    from sandbox_executor.entrypoint import review_loop

    (flow_worktree / "README.md").write_text("dirty edit\n", encoding="utf-8")
    (flow_worktree / "untracked.txt").write_text("keep me\n", encoding="utf-8")
    calls: list[list[str]] = []
    real_run_cmd = review_loop.run_cmd

    def spy(args, *a, **k):
        calls.append(list(args))
        return real_run_cmd(args, *a, **k)

    with patch.object(review_loop, "run_cmd", side_effect=spy):
        target = review_loop._prepare_workspace(str(flow_worktree), "flow-demo")

    assert target.head_sha == _git(flow_worktree, "rev-parse", "HEAD")
    verbs = {c[1] for c in calls if c[0] == "git"}
    assert verbs.isdisjoint({"clone", "fetch", "reset", "clean", "checkout", "pull"}), calls
    assert (flow_worktree / "README.md").read_text(encoding="utf-8") == "dirty edit\n"
    assert (flow_worktree / "untracked.txt").read_text(encoding="utf-8") == "keep me\n"


# --- (c) + (d) host visibility and crash survival --------------------------------------------------------------------


def test_c_d_container_writes_are_on_the_host_at_once_and_survive_an_abrupt_exit(flow_worktree: Path) -> None:
    head = _git(flow_worktree, "rev-parse", "HEAD")
    env = {
        **os.environ,
        "HOLON_TARGET_WORKTREE": str(flow_worktree),
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }

    result = subprocess.run(
        [sys.executable, "-c", _CONTAINER_SNIPPET], env=env, capture_output=True, text=True, check=False, timeout=120
    )

    assert result.returncode == 137, result.stderr
    assert f"HEAD {head}" in result.stdout
    assert (flow_worktree / "README.md").read_text(encoding="utf-8") == "seed\nuncommitted edit\n"
    assert (flow_worktree / "notes.txt").read_text(encoding="utf-8") == "untracked scratch\n"
    assert (flow_worktree / "tmp" / "review" / "iter-1" / "pytest.log").read_text(encoding="utf-8") == "1 passed\n"
    assert _git(flow_worktree, "rev-parse", "HEAD") == head
    assert _git(flow_worktree, "diff", "--name-only") == "README.md"
    untracked = _git(flow_worktree, "ls-files", "--others").splitlines()
    assert sorted(untracked) == ["notes.txt", "tmp/review/iter-1/pytest.log"]


# --- (e) intermediate artifacts under tmp/review/ --------------------------------------------------------------------


def test_e_reviewer_artifacts_live_under_the_git_ignored_tmp_review_root() -> None:
    from sandbox_executor.review_ensemble import REVIEW_ROOT_REL, reviewer_report_rel

    assert REVIEW_ROOT_REL == "tmp/review"
    assert reviewer_report_rel(4, 2) == "tmp/review/iter-4/reviewer-2/report.md"
    gitignore = Path(__file__).resolve().parents[3] / ".gitignore"
    assert "tmp/" in gitignore.read_text(encoding="utf-8").splitlines()


# --- (f) recorded docker commands: no volume, no root chown ----------------------------------------------------------


def test_f_review_loop_dispatch_runs_one_docker_run_with_no_volume_and_no_root_init(flow_worktree: Path) -> None:
    from sandbox_executor.cli import run_docker_container

    docker_calls: list[list[str]] = []
    real_run = subprocess.run
    real_which = shutil.which

    def record(cmd, *args, **kwargs):
        if cmd and cmd[0] == "docker":
            docker_calls.append(list(cmd))
            return MagicMock(returncode=0)
        return real_run(cmd, *args, **kwargs)

    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME") or key.startswith("GIT_")}
    with (
        patch("sandbox_executor.cli.get_agent_session_mounts", return_value=[]),
        patch("subprocess.run", side_effect=record),
        patch("shutil.which", side_effect=lambda name: "/usr/bin/docker" if name == "docker" else real_which(name)),
        patch.dict(os.environ, env, clear=True),
    ):
        code = run_docker_container(
            "review-loop",
            "holon/agent-claude",
            ["flow-demo", "claude-agent", "opus"],
            agent_id="claude",
            target_worktree=str(flow_worktree),
        )

    assert code == 0
    (argv,) = docker_calls
    assert argv[:2] == ["docker", "run"]
    assert "volume" not in argv
    assert "chown" not in argv
    assert "root" not in argv
    assert f"{flow_worktree}:{SANDBOX_WORKSPACE_MOUNT}" in argv
    # Every mount source is an absolute host path: no named volume is mounted either.
    sources = [argv[i + 1].split(":", 1)[0] for i, token in enumerate(argv) if token == "-v"]
    assert sources and all(os.path.isabs(source) for source in sources), sources


# --- (g) flow-<slug> reuse; no duplicate pr<N>-<slug> -----------------------------------------------------------------


def _worktrees(repo: Path) -> list[str]:
    return [line[len("worktree ") :] for line in _git(repo, "worktree", "list", "--porcelain").splitlines() if line]


def test_g_a_resumed_run_reuses_the_existing_flow_worktree(repo: Path, flow_worktree: Path) -> None:
    before = _worktrees(repo)

    reused = git_flow.ensure_flow_worktree_off_default(str(repo), "demo", "main")

    assert reused == str(flow_worktree)
    assert _worktrees(repo) == before


def test_g_a_pr_review_reuses_the_flow_worktree_and_never_creates_a_pr_worktree(
    repo: Path, flow_worktree: Path
) -> None:
    _git(flow_worktree, "push", "-q", "origin", "flow-demo")
    before = _worktrees(repo)

    assert git_flow.ensure_pr_worktree(str(repo), 42, "flow-demo") == str(flow_worktree)
    assert git_flow.resolve_review_worktree(str(repo), "flow-demo", 42) == str(flow_worktree)
    assert git_flow.resolve_review_worktree(str(flow_worktree), "flow-demo", 42) == str(flow_worktree)

    assert _worktrees(repo) == before
    assert not list(repo.parent.glob("pr42-*"))


def test_g_a_pr_without_any_worktree_gets_a_pr_worktree_of_its_head_branch(repo: Path) -> None:
    _git(repo, "push", "-q", "origin", "main:refs/heads/feature/z")

    path = git_flow.ensure_pr_worktree(str(repo), 7, "feature/z")

    assert path == str(repo.parent / "pr7-feature-z")
    assert _git(path, "symbolic-ref", "--short", "HEAD") == "feature/z"
    # Asked again, it is reused rather than provisioned twice.
    assert git_flow.ensure_pr_worktree(str(repo), 7, "feature/z") == path


def test_g_a_branch_target_without_a_worktree_is_refused_not_provisioned(repo: Path) -> None:
    before = _worktrees(repo)

    with pytest.raises(RuntimeError, match="No worktree has branch 'feature/q' checked out"):
        git_flow.resolve_review_worktree(str(repo), "feature/q")

    assert _worktrees(repo) == before


def test_g_ephemeral_reviewer_worktrees_are_removed_and_pruned_even_on_error(flow_worktree: Path) -> None:
    rev = Path(f"{flow_worktree}-rev2")

    with (
        pytest.raises(RuntimeError, match="reviewer crashed"),
        git_flow.ephemeral_reviewer_worktree(str(flow_worktree), 2) as path,
    ):
        assert path == str(rev)
        assert _git(path, "rev-parse", "HEAD") == _git(flow_worktree, "rev-parse", "HEAD")
        raise RuntimeError("reviewer crashed")

    assert not rev.exists()
    assert str(rev) not in _worktrees(flow_worktree)


def test_g_a_survivor_reviewer_worktree_from_a_crash_is_replaced(flow_worktree: Path) -> None:
    survivor = git_flow.provision_reviewer_worktree(str(flow_worktree), 1)
    (Path(survivor) / "leftover.txt").write_text("from a crashed turn\n", encoding="utf-8")

    fresh = git_flow.provision_reviewer_worktree(str(flow_worktree), 1)

    assert fresh == survivor
    assert not (Path(fresh) / "leftover.txt").exists()
    git_flow.prune_reviewer_worktree(str(flow_worktree), 1)
    assert not Path(fresh).exists()
    with pytest.raises(ValueError, match="positive integer"):
        git_flow.provision_reviewer_worktree(str(flow_worktree), 0)


# --- (h) the shared worktree stays the host's: no planted config, no duplicated or swept-up ledger rows ---------------


def test_h_a_repository_with_worktree_config_enabled_is_never_mounted(repo: Path, flow_worktree: Path) -> None:
    # git would read every worktree's config.worktree, and the container can create one in the writable git dir.
    _git(repo, "config", "extensions.worktreeConfig", "true")

    with pytest.raises(RuntimeError, match=r"extensions\.worktreeConfig enabled"):
        git_flow.sandbox_worktree_mounts(str(flow_worktree), MOUNT)


def test_h_the_resolver_commits_its_fix_but_never_the_host_loops_uncommitted_ledger_rows(flow_worktree: Path) -> None:
    from sandbox_executor.entrypoint import review_loop
    from sandbox_executor.review_report import FindingDisposition

    ledger = flow_worktree / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"action_id": "ACT-1"}\n', encoding="utf-8")
    _git(flow_worktree, "add", "-A")
    _git(flow_worktree, "commit", "-qm", "seed ledger")
    with ledger.open("a", encoding="utf-8") as f:
        f.write('{"action_id": "ACT-HOST"}\n')  # written by the host loop, not yet committed
    (flow_worktree / "fix.py").write_text("FIXED = True\n", encoding="utf-8")
    applied = FindingDisposition("R1-F1", "important", "fix.py", 1, "s", disposition="applied")

    with patch.object(review_loop, "_verify_before_push", return_value=(True, True, None)):
        result = review_loop._commit_resolution(
            str(flow_worktree), "flow-demo", 1, (applied,), review_loop.CommitOptions()
        )

    assert result[4] is None, result
    assert _git(flow_worktree, "show", "--name-only", "--format=", "HEAD") == "fix.py"
    assert _git(flow_worktree, "status", "--porcelain") == "M holon-knowledge/ledger/executions.jsonl"


@pytest.mark.parametrize("dispatched", [True, False])
def test_h_a_host_dispatched_pass_appends_its_ledger_rows_to_container_scratch(
    flow_worktree: Path, tmp_path: Path, dispatched: bool
) -> None:
    from sandbox_executor.entrypoint import review_loop
    from sandbox_executor.review_target import ReviewTarget

    argv = ["review_loop", "flow-demo", f"--target-worktree={flow_worktree}", "--skip-post", "--dry-run"]
    if dispatched:
        argv.append(f"--result-file={tmp_path / 'result.json'}")
    prepared = ReviewTarget(raw="flow-demo", head_branch="flow-demo", head_sha="a" * 40)
    with (
        patch.object(sys, "argv", argv),
        patch.object(review_loop, "_prepare_workspace", return_value=prepared),
        patch.object(review_loop, "check_and_resolve_stage_conflicts"),
        patch.object(review_loop, "run_review_loop", return_value={"consensus": {}}) as loop,
        pytest.raises(SystemExit),
    ):
        review_loop.main()

    # The host loop owns the worktree's ledger and records a dispatched pass from its result file.
    assert loop.call_args.kwargs["ledger_file"] == ("/tmp/holon-review-executions.jsonl" if dispatched else None)


# --- (f) the review-loop docker argv: only the worktree mounts are added, and nothing runs without them -------------


def _remove_argv_pair(argv: list[str], pair: list[str]) -> list[str]:
    """Return ``argv`` with the first adjacent occurrence of ``pair`` removed; fail if it is absent."""
    for idx in range(len(argv) - 1):
        if argv[idx : idx + 2] == pair:
            return argv[:idx] + argv[idx + 2 :]
    raise AssertionError(f"expected pair {pair} in docker argv: {argv}")


_FAKE_WORKTREE_MOUNTS = ["-v", "/work/flow-demo:/sandbox", "-v", "/work/repo/.git/config:/work/repo/.git/config:ro"]


def _docker_argvs(
    role: str, target_worktree: str | None, mounts: list[str] | Exception, intent_file: str | None = None
) -> tuple[int, list[list[str]]]:
    """Run run_docker_container for role with the worktree mount builder faked; return (exit code, docker argvs)."""
    from sandbox_executor.cli import run_docker_container

    captured: list[list[str]] = []

    def record_run(cmd, *args, **kwargs):
        captured.append(list(cmd))
        return MagicMock(returncode=0)

    mount_patch = (
        patch("sandbox_executor.git_flow.sandbox_worktree_mounts", side_effect=mounts)
        if isinstance(mounts, Exception)
        else patch("sandbox_executor.git_flow.sandbox_worktree_mounts", return_value=list(mounts))
    )
    with (
        patch("sandbox_executor.cli.get_agent_session_mounts", return_value=["-v", "/mock:/mock"]),
        patch("subprocess.run", side_effect=record_run),
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": "/home/dev/own-checkout"}, clear=True),
        mount_patch,
    ):
        code = run_docker_container(
            role,
            "holon/agent-claude",
            ["branch", "fake-agent", "fake-model"],
            agent_id="claude",
            target_worktree=target_worktree,
            intent_file=intent_file,
        )
    return code, captured


ALL_WORKTREE_ROLES = ["intent-creator", "planner", "review-loop", "executor"]


@pytest.fixture
def intent_file(tmp_path: Path) -> str:
    path = tmp_path / "intent.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("role", ALL_WORKTREE_ROLES)
def test_worktree_roles_add_only_the_worktree_mounts_and_pin_the_workspace(role, intent_file):
    """Every role argv is a role-neutral argv plus worktree mounts, the pinned workspace and venv only."""
    from sandbox_executor.agent_runner import SANDBOX_UV_PROJECT_ENVIRONMENT, SANDBOX_WORKSPACE_MOUNT

    _code, (baseline,) = _docker_argvs("unmounted-role", None, [], intent_file)
    code, (review,) = _docker_argvs(role, "/work/flow-demo", _FAKE_WORKTREE_MOUNTS, intent_file)

    assert code == 0
    # Removing the mounts, the pinned workspace and the role restores the neutral argv byte for byte, so nothing
    # else (--rm, --cap-drop=ALL, no-new-privileges, env forwarding) changed.
    reduced = review
    for pair in (_FAKE_WORKTREE_MOUNTS[:2], _FAKE_WORKTREE_MOUNTS[2:]):
        reduced = _remove_argv_pair(reduced, pair)
    reduced = _remove_argv_pair(reduced, ["-e", f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}"])
    reduced = _remove_argv_pair(reduced, ["-e", f"UV_PROJECT_ENVIRONMENT={SANDBOX_UV_PROJECT_ENVIRONMENT}"])
    reduced = _remove_argv_pair(reduced, ["-e", "UV_FROZEN=1"])
    reduced = _remove_argv_pair(reduced, ["-e", f"HOLON_ROLE={role}"])
    if role == "intent-creator":
        reduced = _remove_argv_pair(reduced, ["-v", f"{intent_file}:/tmp/intent.json"])
    baseline = _remove_argv_pair(baseline, ["-e", "HOLON_TARGET_WORKTREE=/home/dev/own-checkout"])
    baseline = _remove_argv_pair(baseline, ["-e", "HOLON_ROLE=unmounted-role"])
    assert reduced == baseline
    assert "--cap-drop=ALL" in review and "--security-opt=no-new-privileges" in review
    # A forwarded host HOLON_TARGET_WORKTREE cannot displace the pinned container workspace path.
    assert [a for a in review if a.startswith("HOLON_TARGET_WORKTREE=")] == [
        f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}"
    ]
    # 'uv run' in the container must not rebuild the host's .venv inside the shared worktree.
    assert f"UV_PROJECT_ENVIRONMENT={SANDBOX_UV_PROJECT_ENVIRONMENT}" in review
    assert not SANDBOX_UV_PROJECT_ENVIRONMENT.startswith(SANDBOX_WORKSPACE_MOUNT)
    # ...and must never rewrite the committed lock.
    assert "UV_FROZEN=1" in review


@pytest.mark.parametrize("role", ["intent-creator", "planner", "review-loop"])
def test_every_role_mounts_the_worktree_exactly_as_the_executor_does(role, intent_file):
    """The worktree mount arguments, pin and venv of each role are identical to the executor's."""
    from sandbox_executor.agent_runner import SANDBOX_UV_PROJECT_ENVIRONMENT, SANDBOX_WORKSPACE_MOUNT

    wanted = {
        f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}",
        f"UV_PROJECT_ENVIRONMENT={SANDBOX_UV_PROJECT_ENVIRONMENT}",
        "UV_FROZEN=1",
        *_FAKE_WORKTREE_MOUNTS[1::2],
    }

    def worktree_args(argv: list[str]) -> list[tuple[str, str]]:
        return [(argv[i], argv[i + 1]) for i in range(len(argv) - 1) if argv[i + 1] in wanted]

    _code, (executor_argv,) = _docker_argvs("executor", "/work/flow-demo", _FAKE_WORKTREE_MOUNTS)
    code, (argv,) = _docker_argvs(role, "/work/flow-demo", _FAKE_WORKTREE_MOUNTS, intent_file)

    assert code == 0
    assert len(worktree_args(argv)) == len(wanted)
    assert worktree_args(argv) == worktree_args(executor_argv)


@pytest.mark.parametrize("role", ALL_WORKTREE_ROLES)
def test_worktree_role_without_a_valid_worktree_fails_before_any_docker_command(role, capsys, intent_file):
    """A missing or refused worktree fails closed: exit 1, an actionable error, and no docker run at all."""
    from sandbox_executor import git_flow

    code, captured = _docker_argvs(role, None, RuntimeError("needs an explicit target worktree"), intent_file)

    assert code == 1
    assert captured == []
    assert "needs an explicit target worktree" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="needs an explicit target worktree"):
        git_flow.sandbox_worktree_mounts(None, "/sandbox")


# --- the Stage 3 executor on the mounted flow worktree ---------------------------------------------------------------

_PLAN = "I-1-demo/P-1/_"


def _push_over_budget_plan(repo: Path, slug: str = "demo") -> str:
    """Push an over-budget plan branch to origin (so the executor records a refusal without running an agent)."""
    _git(repo, "checkout", "-q", "-b", "plan-seed")
    ledger = repo / "holon-knowledge" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "intents.jsonl").write_text(f'{{"branch": "I-1-demo/_", "slug": "{slug}"}}\n', encoding="utf-8")
    (ledger / "plans.jsonl").write_text(
        '{"plan_id": "P-1", "intent_branch": "I-1-demo/_", "entropy": 8.0, "entropy_budget": 5.0}\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "plan")
    _git(repo, "push", "-q", str(repo.parent / "remote.git"), f"HEAD:refs/heads/{_PLAN}")
    # Newer git records a remote-tracking ref even for a push by URL when that URL is origin's; drop it so none exists
    # until the host fetches the plan branch.
    _git(repo, "update-ref", "-d", f"refs/remotes/origin/{_PLAN}")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return tip


def _executor_spy(calls: list[list[str]]):
    from sandbox_executor.entrypoint import executor

    real_run_cmd = executor.run_cmd

    def spy(args, *a, **k):
        calls.append(list(args))
        return real_run_cmd(args, *a, **k)

    return patch.object(executor, "run_cmd", side_effect=spy)


def test_b_executor_starts_from_the_host_fetched_plan_tip_without_cloning_or_fetching(
    repo: Path, flow_worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sandbox_executor.entrypoint import executor

    tip = _push_over_budget_plan(repo)
    git_flow.fetch_origin_branch(str(flow_worktree), _PLAN)
    monkeypatch.setenv("HOLON_TARGET_WORKTREE", SANDBOX_WORKSPACE_MOUNT)
    calls: list[list[str]] = []

    with _executor_spy(calls):
        assert validate_mounted_workspace(executor._get_repo(str(flow_worktree)), str(flow_worktree), _PLAN) == tip

    verbs = {c[1] for c in calls if c[0] == "git"}
    assert verbs.isdisjoint({"clone", "fetch", "reset", "clean", "checkout", "pull"}), calls
    # The mounted repository's config is the host's and read-only: safe.directory goes in the container's own config.
    assert str(flow_worktree) in _git(flow_worktree, "config", "--global", "--get-all", "safe.directory")


def test_b_executor_refuses_a_plan_branch_the_host_has_not_fetched(repo: Path, flow_worktree: Path) -> None:
    from sandbox_executor.entrypoint import executor

    _push_over_budget_plan(repo)

    with pytest.raises(RuntimeError, match="the host fetches it before dispatch"):
        validate_mounted_workspace(executor._get_repo(str(flow_worktree)), str(flow_worktree), _PLAN)


def test_d_executor_refuses_a_dirty_mounted_worktree_and_leaves_its_edits_on_the_host(
    repo: Path, flow_worktree: Path
) -> None:
    from sandbox_executor.entrypoint import executor

    _push_over_budget_plan(repo)
    git_flow.fetch_origin_branch(str(flow_worktree), _PLAN)
    (flow_worktree / "README.md").write_text("edit left by a crashed run\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uncommitted changes, which are kept on the host"):
        validate_mounted_workspace(executor._get_repo(str(flow_worktree)), str(flow_worktree), _PLAN)

    assert (flow_worktree / "README.md").read_text(encoding="utf-8") == "edit left by a crashed run\n"


def _run_executor_on_mounted_worktree(
    repo: Path, flow_worktree: Path, monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]
) -> None:
    """Run the executor's main on the over-budget plan in the mounted worktree and expect the refusal exit."""
    from sandbox_executor.entrypoint import executor

    monkeypatch.setenv("HOLON_TARGET_WORKTREE", SANDBOX_WORKSPACE_MOUNT)
    monkeypatch.setattr(executor, "get_workspace_dir", lambda: str(flow_worktree))
    monkeypatch.setattr(executor, "sync_and_reconcile_pre_push", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(executor, "get_runner", lambda name: MagicMock(**{"get_version.return_value": "1.0.0"}))
    monkeypatch.setattr(sys, "argv", ["executor.py", _PLAN, "antigravity-agent", "gemini-3.5-flash"])

    with _executor_spy(calls), pytest.raises(SystemExit) as exit_:
        executor.main()

    assert exit_.value.code == executor.OVER_BUDGET_EXIT_CODE


def test_c_executor_commits_its_execution_branch_in_the_mounted_worktree_and_leaves_the_push_to_the_host(
    repo: Path, flow_worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tip = _push_over_budget_plan(repo)
    git_flow.fetch_origin_branch(str(flow_worktree), _PLAN)
    calls: list[list[str]] = []

    _run_executor_on_mounted_worktree(repo, flow_worktree, monkeypatch, calls)

    assert not any(c[:2] == ["git", "clone"] or c[:2] == ["git", "clean"] for c in calls), calls
    # The execution branch is checked out in the host worktree and starts at the plan tip.
    branch = _git(flow_worktree, "symbolic-ref", "--short", "HEAD")
    assert branch.startswith("I-1-demo/P-1/E-") and branch.endswith("/_")
    assert _git(flow_worktree, "rev-parse", "HEAD~1") == tip
    assert _git(flow_worktree, "status", "--porcelain") == ""
    # The container never publishes it: the host pushes after the container returns.
    remote = repo.parent / "remote.git"
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=remote).returncode != 0
    # Nothing was written to the host's shared repository config: no upstream, no identity.
    host_config = (repo / ".git" / "config").read_text(encoding="utf-8")
    assert "Holon Executor Agent" not in host_config and f'branch "{branch}"' not in host_config


def test_c_executor_runs_no_remote_git_command_even_when_the_hosts_origin_is_unreachable(
    repo: Path, flow_worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _push_over_budget_plan(repo)
    git_flow.fetch_origin_branch(str(flow_worktree), _PLAN)
    _git(repo, "remote", "set-url", "origin", "git@unreachable.invalid:holon/demo.git")
    calls: list[list[str]] = []

    _run_executor_on_mounted_worktree(repo, flow_worktree, monkeypatch, calls)

    remote_verbs = {"fetch", "push", "pull", "ls-remote", "clone"}
    assert not [c for c in calls if c[0] == "git" and remote_verbs & set(c[1:3])], calls


def test_g_holon_execute_provisions_the_plans_flow_worktree_off_a_default_branch_checkout(repo: Path) -> None:
    from sandbox_executor.cli.commands.execute import execution_worktree

    tip = _push_over_budget_plan(repo)

    worktree = execution_worktree(_PLAN, str(repo))

    assert worktree == str(repo.parent / "flow-demo")
    assert _git(worktree, "symbolic-ref", "--short", "HEAD") == "flow-demo"
    # The plan branch is fetched on the host, so the executor container never fetches to prepare its checkout.
    assert _git(worktree, "rev-parse", f"refs/remotes/origin/{_PLAN}") == tip
    # The default-branch checkout only selected the repository: it stays on main.
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"


def test_g_holon_execute_names_the_flow_worktree_after_the_intent_ledger_slug(repo: Path) -> None:
    from sandbox_executor.cli.commands.execute import execution_worktree

    # An intent given an explicit branch keeps its own slug, which holon flow named the flow worktree after.
    _push_over_budget_plan(repo, slug="custom")

    assert execution_worktree(_PLAN, str(repo)) == str(repo.parent / "flow-custom")


def test_g_holon_execute_reuses_an_existing_flow_worktree(repo: Path, flow_worktree: Path) -> None:
    from sandbox_executor.cli.commands.execute import execution_worktree

    _push_over_budget_plan(repo)
    before = _worktrees(repo)

    assert execution_worktree(_PLAN, str(flow_worktree)) == str(flow_worktree)
    assert _worktrees(repo) == before


def test_d_executor_ignores_a_dirty_engine_owned_ledger(repo: Path, flow_worktree: Path) -> None:
    from sandbox_executor.entrypoint import executor

    tip = _push_over_budget_plan(repo)
    git_flow.fetch_origin_branch(str(flow_worktree), _PLAN)
    ledger = flow_worktree / "holon-knowledge" / "ledger" / "executions.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"dirty": true}\n', encoding="utf-8")

    assert validate_mounted_workspace(executor._get_repo(str(flow_worktree)), str(flow_worktree), _PLAN) == tip
