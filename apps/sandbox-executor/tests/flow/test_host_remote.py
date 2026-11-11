"""The host-side remote module: fetches and pushes go through the worktree's remote and fail with a named error."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sandbox_executor.flow import host_remote
from sandbox_executor.flow.host_remote import (
    HostRemoteError,
    fetch_base,
    publish_new_child_branches,
    push_branch,
    push_commit,
    remote_error,
)

from tests.flow.helpers import _configure_identity, _git

TOKENISED_URL = "https://ci-user:s3cretTOKENvalue99@127.0.0.1:9/acme/widgets.git"


def _commit_on(host: Path, branch: str, name: str) -> str:
    _configure_identity(host)
    _git(host, "checkout", "-q", "-B", branch, "main")
    (host / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", name)
    return _git(host, "rev-parse", "HEAD")


def test_fetch_base_brings_the_branch_into_the_remote_tracking_ref(flow_repos, tmp_path):
    host, origin = flow_repos
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    tip = _commit_on(other, "base-branch", "x")
    _git(other, "push", "-q", "origin", "base-branch")

    fetch_base(str(host), "base-branch")

    assert _git(host, "rev-parse", "refs/remotes/origin/base-branch") == tip


def test_fetch_failure_names_the_operation_branch_and_remote(flow_repos):
    host, _origin = flow_repos

    with pytest.raises(HostRemoteError) as raised:
        fetch_base(str(host), "no-such-branch")

    error = raised.value
    assert (error.operation, error.branch, error.remote) == ("fetch", "no-such-branch", "origin")
    assert "fetch of 'no-such-branch' through remote 'origin' failed" in str(error)


def test_push_branch_publishes_the_local_branch_and_refuses_a_non_fast_forward(flow_repos, tmp_path):
    host, origin = flow_repos
    tip = _commit_on(host, "work/_", "first")

    push_branch(str(host), "work/_")

    assert _git(origin, "rev-parse", "refs/heads/work/_") == tip
    # A diverged local branch is refused, never forced.
    _git(host, "reset", "-q", "--hard", "main")
    _commit_on(host, "work/_", "diverged")
    with pytest.raises(HostRemoteError) as raised:
        push_branch(str(host), "work/_")
    assert (raised.value.operation, raised.value.branch, raised.value.remote) == ("push", "work/_", "origin")
    assert _git(origin, "rev-parse", "refs/heads/work/_") == tip


def test_push_commit_publishes_an_exact_commit_under_the_branch_name(flow_repos):
    host, origin = flow_repos
    tip = _commit_on(host, "local-name", "exact")

    push_commit(str(host), tip, "remote-name")

    assert _git(origin, "rev-parse", "refs/heads/remote-name") == tip


def test_a_failed_push_through_a_tokenised_remote_leaks_no_credential(flow_repos, monkeypatch):
    host, _origin = flow_repos
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    _commit_on(host, "work/_", "first")
    _git(host, "remote", "set-url", "origin", TOKENISED_URL)

    with pytest.raises(HostRemoteError) as raised:
        push_branch(str(host), "work/_")

    assert "s3cretTOKENvalue99" not in str(raised.value)
    assert "s3cretTOKENvalue99" not in raised.value.detail
    assert "through remote 'origin'" in str(raised.value)


def test_remote_error_returns_the_message_or_none(flow_repos):
    host, _origin = flow_repos

    assert remote_error(fetch_base, str(host), "main") is None
    message = remote_error(fetch_base, str(host), "no-such-branch")
    assert message is not None and "'no-such-branch'" in message and "'origin'" in message


def test_publish_new_child_branches_pushes_only_the_branches_the_role_added(flow_repos):
    host, origin = flow_repos
    existing = "I-1-x/P-100-claude-agent-claude-opus-5-5/_"
    added = "I-1-x/P-200-claude-agent-claude-opus-5-5/_"
    _commit_on(host, existing, "old")
    _commit_on(host, added, "new")

    pushed = publish_new_child_branches(str(host), "I-1-x", "P", before={existing})

    assert pushed == [added]
    assert _git(origin, "rev-parse", "--verify", "--quiet", f"refs/heads/{added}")
    assert _git(origin, "branch", "--list", existing) == ""


def test_the_module_imports_nothing_from_the_entrypoints_or_the_cli():
    tree = ast.parse(Path(host_remote.__file__).read_text(encoding="utf-8"))
    imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [name for name in imported if name.startswith(("sandbox_executor.entrypoint", "sandbox_executor.cli"))]


@pytest.mark.parametrize(
    "source",
    [
        "--receive-pack=touch /tmp/pwned",
        "--force",
        "+refs/heads/main",
        "+" + "a" * 40,
        "HEAD",
        "main",
        "abc123",
        "refs/tags/v1",
        "refs/heads/../main",
        "refs/heads/-x",
        "",
        "a" * 40 + "\n",
    ],
)
def test_push_commit_refuses_a_hostile_source_before_running_git(flow_repos, source):
    host, origin = flow_repos
    before = _git(origin, "for-each-ref")

    with pytest.raises(HostRemoteError) as raised:
        push_commit(str(host), source, "remote-name")

    assert "refusing to push" in str(raised.value)
    assert _git(origin, "for-each-ref") == before


@pytest.mark.parametrize("branch", ["--receive-pack=x", "-f", "a..b", "", "a b", "x//y", "x.lock", "+main"])
def test_push_commit_refuses_a_hostile_branch_name(flow_repos, branch):
    host, _origin = flow_repos
    tip = _git(host, "rev-parse", "HEAD")

    with pytest.raises(HostRemoteError, match="refusing to push"):
        push_commit(str(host), tip, branch)


@pytest.mark.parametrize("remote", ["--receive-pack=x", "-o", "", "a b"])
def test_push_commit_refuses_a_hostile_remote_name(flow_repos, remote):
    host, _origin = flow_repos
    tip = _git(host, "rev-parse", "HEAD")

    with pytest.raises(HostRemoteError, match="refusing to push"):
        push_commit(str(host), tip, "remote-name", remote)


def test_push_commit_never_force_pushes_a_non_descendant_commit(flow_repos):
    host, origin = flow_repos
    tip = _commit_on(host, "work/_", "first")
    push_branch(str(host), "work/_")
    _git(host, "reset", "-q", "--hard", "main")
    diverged = _commit_on(host, "other", "diverged")

    with pytest.raises(HostRemoteError) as raised:
        push_commit(str(host), diverged, "work/_")

    assert raised.value.operation == "push"
    assert _git(origin, "rev-parse", "refs/heads/work/_") == tip
