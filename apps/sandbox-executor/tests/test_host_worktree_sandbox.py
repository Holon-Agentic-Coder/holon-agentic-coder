"""Hermetic tests for role containers working in place on a bind-mounted host worktree.

Real git repositories under ``tmp_path`` with a local bare remote stand in for the host; no Docker daemon or network is
needed. A child Python process plays the container wherever a process boundary matters (an abrupt exit). The real
container round trip is covered by ``test_review_workspace_integration.py``. One test per goal:

(a) the worktree is validated and bind-mounted with its git directories, and git works on those paths;
(b) the entrypoint starts on the mounted worktree with no clone, fetch, reset or clean;
(c) files the container writes appear on the host worktree at once;
(d) an abrupt exit (code 137) leaves uncommitted edits, untracked files and logs intact on the host;
(e) intermediate subagent artifacts live under ``<worktree>/.subagent/review/iter-<n>/``, which git ignores;
(f) the recorded docker commands create no named volume and launch no root chown init container;
(g) resumed runs and Stage 4 reviews reuse ``flow-<slug>`` and never create ``pr<N>-<slug>`` beside it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import git_flow
from sandbox_executor.agent_runner import SANDBOX_WORKSPACE_MOUNT

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
os.makedirs(os.path.join(ws, ".subagent", "review", "iter-1"), exist_ok=True)
with open(os.path.join(ws, ".subagent", "review", "iter-1", "pytest.log"), "w") as f:
    f.write("1 passed\\n")
os._exit(137)
"""


def _git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A primary checkout on main with a local bare origin; git never reads the host's own config."""
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
        # Nothing the container writes may later run code on the host.
        f"{flow_worktree / '.git'}:{MOUNT}/.git:ro",
        f"{git_dir / 'commondir'}:{git_dir / 'commondir'}:ro",
        f"{git_dir / 'gitdir'}:{git_dir / 'gitdir'}:ro",
        f"{common / 'config'}:{common / 'config'}:ro",
        f"{common / 'hooks'}:{common / 'hooks'}:ro",
    ]
    # The mounted pointer resolves through the mounted paths, without any network: git works there.
    assert (flow_worktree / ".git").read_text(encoding="utf-8").strip() == f"gitdir: {git_dir}"
    assert _git(flow_worktree, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")
    assert _git(flow_worktree, "status", "--porcelain") == ""


def test_a_primary_checkout_needs_no_extra_git_mount_but_still_protects_config_and_hooks(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")

    mounts = _mount_pairs(git_flow.sandbox_worktree_mounts(str(repo), MOUNT))

    assert mounts == [
        f"{repo}:{MOUNT}",
        f"{repo / '.git' / 'config'}:{MOUNT}/.git/config:ro",
        f"{repo / '.git' / 'hooks'}:{MOUNT}/.git/hooks:ro",
    ]


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
    assert (flow_worktree / ".subagent" / "review" / "iter-1" / "pytest.log").read_text(
        encoding="utf-8"
    ) == "1 passed\n"
    assert _git(flow_worktree, "rev-parse", "HEAD") == head
    assert _git(flow_worktree, "diff", "--name-only") == "README.md"
    untracked = _git(flow_worktree, "ls-files", "--others").splitlines()
    assert sorted(untracked) == [".subagent/review/iter-1/pytest.log", "notes.txt"]


# --- (e) intermediate artifacts under .subagent/ ---------------------------------------------------------------------


def test_e_reviewer_artifacts_live_under_the_git_ignored_subagent_review_root() -> None:
    from sandbox_executor.review_ensemble import REVIEW_ROOT_REL, reviewer_dir_rel, reviewer_report_rel

    assert REVIEW_ROOT_REL == ".subagent/review"
    assert reviewer_dir_rel(4, 2) == ".subagent/review/iter-4/reviewer-2"
    assert reviewer_report_rel(4, 2) == ".subagent/review/iter-4/reviewer-2/report.md"
    gitignore = Path(__file__).resolve().parents[3] / ".gitignore"
    assert ".subagent" in gitignore.read_text(encoding="utf-8").splitlines()


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
        patch("sandbox_executor.cli.resolve_target_repo", return_value=("https://github.com/acme/widgets.git", None)),
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
