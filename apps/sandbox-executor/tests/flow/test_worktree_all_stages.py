"""Every stage works in the host worktree and no container touches the remote: the host fetches and pushes.

Hermetic: real git repositories under ``tmp_path`` with a local bare origin, fake role dispatchers and a recorded
``docker run`` argv. No Docker daemon, ``gh`` or network is used.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import git_flow
from sandbox_executor.agent_runner import SANDBOX_UV_PROJECT_ENVIRONMENT, SANDBOX_WORKSPACE_MOUNT
from sandbox_executor.cli import run_docker_container
from sandbox_executor.flow import PipelineEngine, StageStatus
from sandbox_executor.flow.review.resolver import _push_resolver_head
from sandbox_executor.flow.stages.intent import run_intent_stage
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import FakeRoleAgents, _configure_identity, _dispatch_context, _git
from tests.test_host_worktree_sandbox import _host_writable, _mount_pairs

ROLES = ["intent-creator", "planner", "executor", "review-loop"]
SSH_ORIGIN = "git@example.com:org/repo.git"
REMOTE_VERBS = {"clone", "fetch", "pull", "push", "ls-remote", "remote"}
#: Methods of ``GitRepository`` that speak to a remote.
REMOTE_METHODS = {"fetch", "push", "pull", "clone"}
ENTRYPOINT_DIR = Path(git_flow.__file__).parent / "entrypoint"
APP_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def sandbox_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A primary checkout on main plus its flow worktree and a sibling worktree, as a mounted run has them."""
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_UID", os.getuid())
    monkeypatch.setattr(git_flow, "SANDBOX_CONTAINER_GID", os.getgid())
    main = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True, capture_output=True)
    _configure_identity(main)
    (main / "README.md").write_text("seed\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "seed")
    flow = tmp_path / "flow-demo"
    _git(main, "worktree", "add", "-q", "-b", "flow-demo", str(flow))
    _git(main, "worktree", "add", "-q", "-b", "sibling", str(tmp_path / "sibling"))
    return main.resolve(), flow.resolve()


def _docker_run(role: str, worktree: Path, tmp_path: Path, env: dict[str, str] | None = None) -> tuple[list[str], dict]:
    """Run ``run_docker_container`` for ``role`` with docker recorded; return the argv and the env-file variables."""
    argvs: list[list[str]] = []
    secrets: dict[str, str] = {}
    intent_file = tmp_path / "intent.json"
    intent_file.write_text("{}", encoding="utf-8")

    real_run = subprocess.run

    def record(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        if cmd[:1] != ["docker"]:
            return real_run(cmd, *args, **kwargs)
        argvs.append(list(cmd))
        return MagicMock(returncode=0)

    def record_env(values: dict[str, str]) -> str:
        secrets.update(values)
        return str(tmp_path / "env-file")

    with (
        patch("sandbox_executor.cli.get_agent_session_mounts", return_value=[]),
        patch("sandbox_executor.cli.write_env_file", side_effect=record_env),
        patch("subprocess.run", side_effect=record),
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch.dict(os.environ, {"HOME": str(tmp_path), "PATH": os.environ["PATH"], **(env or {})}, clear=True),
    ):
        code = run_docker_container(
            role,
            "holon/agent-claude",
            ["branch", "agent", "model"],
            agent_id="claude",
            intent_file=str(intent_file) if role == "intent-creator" else None,
            target_worktree=str(worktree),
        )
    assert code == 0
    docker = [argv for argv in argvs if argv[:2] == ["docker", "run"]]
    assert len(docker) == 1
    return docker[0], secrets


# --- the intent and plan containers are started in the mounted worktree -------------------------------------------


@pytest.mark.parametrize("role", ["intent-creator", "planner"])
def test_the_intent_and_plan_containers_mount_the_worktree_exactly_as_the_executor_does(role, sandbox_repo, tmp_path):
    _main, flow = sandbox_repo
    executor_mounts = git_flow.sandbox_worktree_mounts(str(flow), SANDBOX_WORKSPACE_MOUNT, "executor")

    argv, _ = _docker_run(role, flow, tmp_path)

    assert git_flow.sandbox_worktree_mounts(str(flow), SANDBOX_WORKSPACE_MOUNT, role) == executor_mounts
    for pair in _mount_pairs(executor_mounts):
        assert pair in argv
    assert f"HOLON_TARGET_WORKTREE={SANDBOX_WORKSPACE_MOUNT}" in argv
    assert f"UV_PROJECT_ENVIRONMENT={SANDBOX_UV_PROJECT_ENVIRONMENT}" in argv
    assert "clone" not in argv


@pytest.mark.parametrize("role", ROLES)
def test_no_container_gets_an_ssh_agent_a_credential_helper_or_a_repository_url(role, sandbox_repo, tmp_path):
    _main, flow = sandbox_repo

    argv, secrets = _docker_run(role, flow, tmp_path, env={"SSH_AUTH_SOCK": "/tmp/ssh-agent.sock"})

    joined = " ".join(argv) + " " + " ".join(secrets)
    assert "SSH_AUTH_SOCK" not in joined and "ssh-agent" not in joined and "ssh-auth.sock" not in joined
    assert "GIT_CONFIG" not in joined
    assert "REPO_URL" not in joined and "GH_REPO" not in joined


# --- the sibling-worktree protections hold for the intent and plan mounts -----------------------------------------


@pytest.mark.parametrize("role", ["intent-creator", "planner"])
def test_the_sibling_worktree_protections_hold_for_the_intent_and_plan_mounts(role, sandbox_repo):
    main, flow = sandbox_repo
    args = git_flow.sandbox_worktree_mounts(str(flow), SANDBOX_WORKSPACE_MOUNT, role)
    common = main / ".git"

    for sibling_file in ("HEAD", "gitdir", "commondir", "config.worktree"):
        assert not _host_writable(args, str(common / "worktrees" / "sibling" / sibling_file))
    assert not _host_writable(args, str(common / "worktrees" / "new-entry" / "HEAD"))
    assert not _host_writable(args, str(common / "config"))
    assert not _host_writable(args, str(common / "hooks" / "pre-commit"))
    # The mounted worktree's own administrative directory stays writable, so its commits work.
    assert _host_writable(args, str(common / "worktrees" / "flow-demo" / "HEAD"))
    assert _host_writable(args, str(flow / "README.md"))
    # No sibling worktree is reachable at all.
    sibling = str(main.parent / "sibling")
    assert not any(pair.split(":")[0] == sibling or pair.startswith(sibling + os.sep) for pair in _mount_pairs(args))
    assert not _host_writable(args, sibling + "/README.md")


# --- no entrypoint issues a remote git operation ------------------------------------------------------------------


def _git_verbs(tree: ast.AST) -> list[tuple[int, str]]:
    """Every git verb an entrypoint names: ``["git", verb, ...]`` literals and ``repo.run([verb, ...])`` calls."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts and all(isinstance(e, ast.Constant) for e in node.elts[:1]):
            first = node.elts[0].value if isinstance(node.elts[0], ast.Constant) else None
            if first == "git" and len(node.elts) > 1 and isinstance(node.elts[1], ast.Constant):
                found.append((node.lineno, str(node.elts[1].value)))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and node.args
            and isinstance(node.args[0], ast.List)
            and node.args[0].elts
            and isinstance(node.args[0].elts[0], ast.Constant)
        ):
            found.append((node.lineno, str(node.args[0].elts[0].value)))
    return found


@pytest.mark.parametrize("path", sorted(ENTRYPOINT_DIR.glob("*.py")), ids=lambda p: p.name)
def test_no_entrypoint_names_a_remote_git_verb_or_method(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))

    remote_verbs = [(line, verb) for line, verb in _git_verbs(tree) if verb in REMOTE_VERBS]
    remote_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REMOTE_METHODS
    ]

    assert remote_verbs == [], f"{path.name} issues remote git verbs: {remote_verbs}"
    assert remote_calls == [], f"{path.name} calls a remote GitRepository method at lines {remote_calls}"


def test_the_role_dispatcher_script_runs_no_git():
    script = (APP_DIR / "entrypoint" / "role_dispatcher.sh").read_text(encoding="utf-8")

    assert not re.search(r"\bgit\s+(clone|fetch|pull|push|ls-remote|remote)\b", script)


def test_the_intent_creator_issues_no_remote_git_command_when_it_runs(sandbox_repo, monkeypatch):
    from sandbox_executor.entrypoint import execution_workspace, intent_creator

    _main, flow = sandbox_repo
    _git(flow, "update-ref", "refs/remotes/origin/main", "main")
    issued: list[list[str]] = []
    real_run = intent_creator.run_cmd

    def recording(args: list[str], **kwargs: Any) -> Any:
        issued.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(execution_workspace, "SANDBOX_WORKSPACE_MOUNT", str(flow))
    monkeypatch.setenv("HOLON_TARGET_WORKTREE", str(flow))
    monkeypatch.setattr(intent_creator, "run_cmd", recording)
    intent_json = Path("/tmp/intent.json")
    if intent_json.exists():
        pytest.skip("/tmp/intent.json belongs to someone else")
    intent_json.write_text('{"slug": "demo", "goal": "g", "description": "d", "branch": "I-1-demo"}', encoding="utf-8")
    try:
        intent_creator.main()
    finally:
        intent_json.unlink(missing_ok=True)

    assert issued, "the entrypoint ran no git command at all"
    assert not [cmd for cmd in issued if cmd[:1] == ["git"] and set(cmd[1:3]) & REMOTE_VERBS]
    assert _git(flow, "rev-parse", "--abbrev-ref", "HEAD") == "I-1-demo/_"


# --- an SSH origin: the host fetches and pushes through the worktree's remote, so every stage completes -----------


@pytest.fixture
def ssh_origin_host(flow_repos):
    """The flow host repository whose ``origin`` is an SSH URL that git rewrites to the local bare repository."""
    host, origin = flow_repos
    _git(host, "remote", "set-url", "origin", SSH_ORIGIN)
    _git(host, "config", f"url.{origin}.insteadOf", SSH_ORIGIN)
    assert _git(host, "config", "--get", "remote.origin.url") == SSH_ORIGIN
    return host, origin


def test_every_stage_completes_when_the_worktrees_origin_is_an_ssh_url(ssh_origin_host, tmp_path):
    host, origin = ssh_origin_host
    agents = FakeRoleAgents(tmp_path, origin)
    ctx = _dispatch_context(host, agents)

    results = PipelineEngine(ctx).run()

    for stage in ("intent", "plan", "execute"):
        assert results[stage].status == StageStatus.SUCCESS, results[stage].error
    assert agents.roles() == ["intent-creator", "planner", "executor"]
    # The host pushed what each container committed, through origin.
    for branch in (ctx.intent_branch, ctx.plan_branch, ctx.execution_branch):
        assert _git(origin, "rev-parse", f"refs/heads/{branch}") == _git(host, "rev-parse", f"refs/heads/{branch}")
    # The fake containers contacted no remote: their calls carry no credentials and see the SSH URL untouched.
    assert _git(host, "config", "--get", "remote.origin.url") == SSH_ORIGIN


def test_the_review_resolver_head_is_pushed_by_the_host_through_an_ssh_origin(ssh_origin_host, tmp_path):
    host, origin = ssh_origin_host
    agents = FakeRoleAgents(tmp_path, origin)
    ctx = _dispatch_context(host, agents)
    assert PipelineEngine(ctx).run()["execute"].status == StageStatus.SUCCESS
    branch = ctx.execution_branch
    old_sha = _git(host, "rev-parse", f"refs/heads/{branch}")
    # The resolver container commits a fix in the mounted worktree; the host pushes that commit.
    (host / "src" / "app.py").write_text("print('fixed')\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-qm", "fix(review): resolve findings")
    new_sha = _git(host, "rev-parse", "HEAD")
    target = ReviewTarget(raw=branch, head_branch=branch, head_sha=old_sha)

    pushed, error = _push_resolver_head(ctx, target, new_sha, None)

    assert (pushed, error) == (True, None)
    assert _git(origin, "rev-parse", f"refs/heads/{branch}") == new_sha


# --- a stale HOLON_REPO_URL changes nothing -----------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_a_stale_repository_url_in_the_environment_changes_nothing(role, sandbox_repo, tmp_path):
    _main, flow = sandbox_repo
    stale = {"HOLON_REPO_URL": "https://ci-user:stale-secret-value@github.com/old/repo.git"}

    clean_argv, clean_secrets = _docker_run(role, flow, tmp_path)
    stale_argv, stale_secrets = _docker_run(role, flow, tmp_path, env=stale)

    assert stale_argv == clean_argv
    assert stale_secrets == clean_secrets
    assert "stale-secret-value" not in " ".join(stale_argv) + " ".join(stale_secrets.values())


def test_a_stale_repository_url_leaves_the_intent_stage_outcome_unchanged(flow_repos, tmp_path, monkeypatch):
    host, origin = flow_repos
    clean = run_intent_stage(_dispatch_context(host, FakeRoleAgents(tmp_path / "a", origin)))
    assert clean.status == StageStatus.SUCCESS
    host2, origin2 = host, origin
    _git(host2, "checkout", "-q", "main")
    monkeypatch.setenv("HOLON_REPO_URL", "git@github.com:stale/repo.git")
    intent = {"slug": "dispatch-test-2", "goal": "Change the application", "labels": ["flow"]}

    stale = run_intent_stage(_dispatch_context(host2, FakeRoleAgents(tmp_path / "b", origin2), intent_data=intent))

    assert stale.status == StageStatus.SUCCESS, stale.error
    assert stale.payload["dispatched"] is True


# --- a failed host fetch or push is named -------------------------------------------------------------------------


def test_a_failed_host_push_fails_the_intent_stage_with_the_named_error(flow_repos, tmp_path):
    host, origin = flow_repos
    secret = "s3cretTOKENvalue99"
    _git(host, "config", "remote.origin.pushurl", f"https://ci-user:{secret}@127.0.0.1:9/acme/widgets.git")
    agents = FakeRoleAgents(tmp_path, origin)
    ctx = _dispatch_context(host, agents)

    with patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "0"}):
        result = run_intent_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert f"host push of '{ctx.intent_branch}' through remote 'origin' failed" in (result.error or "")
    assert secret not in (result.error or "")
    assert "Permission denied" not in (result.error or "")


def test_a_failed_host_fetch_fails_the_intent_stage_with_the_named_error(flow_repos, tmp_path):
    host, origin = flow_repos
    ctx = _dispatch_context(
        host,
        FakeRoleAgents(tmp_path, origin),
        intent_data={"slug": "fetch-fail", "goal": "g", "target_branch": "no-such-base"},
    )

    result = run_intent_stage(ctx)

    assert result.status == StageStatus.FAILED
    assert "host fetch of 'no-such-base' through remote 'origin' failed" in (result.error or "")
