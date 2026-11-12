"""The suite-wide network guard refuses git network commands against real remotes and inet connections."""

import socket
import subprocess
from pathlib import Path

import pytest

from tests.network_guard import NetworkBlockedError, git_network_violation, is_local_url

NON_LOCAL_URLS = [
    "ssh://git@github.com/o/r.git",
    "https://github.com/o/r.git",
    "git://example.invalid/r.git",
    "git@github.com:o/r.git",
    "example.invalid:r.git",
]


@pytest.mark.parametrize("url", NON_LOCAL_URLS)
def test_remote_urls_that_reach_a_network_are_not_local(url):
    assert not is_local_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "/tmp/origin.git",
        "../origin.git",
        "./origin.git",
        "origin.git",
        "file:///tmp/origin.git",
        "https://127.0.0.1:9/r",
    ],
)
def test_paths_file_urls_and_loopback_urls_are_local(url):
    assert is_local_url(url)


@pytest.mark.parametrize("url", NON_LOCAL_URLS)
@pytest.mark.parametrize("subcommand", ["fetch", "pull", "push", "ls-remote"])
def test_network_subcommands_against_a_non_local_remote_are_violations(subcommand, url):
    assert git_network_violation(["git", subcommand, url]) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "-C", "/some/dir", "fetch", "ssh://git@example.invalid/r.git"],
        ["git", "-c", "http.proxy=x", "push", "--force", "-o", "ci.skip", "https://example.invalid/r.git"],
        ["/usr/bin/git", "--no-pager", "ls-remote", "--heads", "git@example.invalid:r.git"],
    ],
)
def test_global_and_subcommand_options_are_skipped_to_find_the_remote(argv):
    assert git_network_violation(argv) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "status"],
        ["git", "commit", "-m", "fetch ssh://git@example.invalid/r.git"],
        ["git", "-C", "/tmp", "fetch", "/tmp/origin.git"],
        ["git", "fetch", "file:///tmp/origin.git"],
        ["gh", "pr", "view", "1"],
        ["echo", "git", "fetch", "ssh://example.invalid/r.git"],
    ],
)
def test_non_network_and_local_commands_are_ignored(argv):
    assert git_network_violation(argv) is None


def _init_repo(path: Path, *flags: str) -> None:
    subprocess.run(["git", "init", "-q", *flags, str(path)], check=True)


def test_a_fetch_from_a_named_non_local_remote_is_refused_and_recorded(
    tmp_path: Path, network_guard_fixture: list[str]
):
    _init_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "ssh://git@example.invalid/r.git"], cwd=tmp_path, check=True)

    with pytest.raises(NetworkBlockedError, match="non-local remote"):
        subprocess.run(["git", "fetch", "origin"], cwd=tmp_path, check=False, capture_output=True)

    assert len(network_guard_fixture) == 1
    network_guard_fixture.clear()


def test_an_insteadof_rewrite_to_a_real_remote_is_refused(tmp_path: Path, network_guard_fixture: list[str]):
    _init_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "origin", "local-alias"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "url.ssh://git@example.invalid/.insteadOf", "local-alias"], cwd=tmp_path, check=True
    )

    with pytest.raises(NetworkBlockedError):
        subprocess.run(["git", "ls-remote", "origin"], cwd=tmp_path, check=False, capture_output=True)

    network_guard_fixture.clear()


def test_a_fetch_from_a_local_bare_remote_succeeds(tmp_path: Path, network_guard_fixture: list[str]):
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _init_repo(origin, "--bare")
    _init_repo(work)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=work, check=True)

    for argv in (["git", "fetch", "origin"], ["git", "ls-remote", "origin"]):
        assert subprocess.run(argv, cwd=work, check=False, capture_output=True).returncode == 0

    assert network_guard_fixture == []


def test_a_connection_to_a_real_host_is_refused_and_recorded(network_guard_fixture: list[str]):
    with pytest.raises(NetworkBlockedError, match="name lookup"):
        socket.create_connection(("example.invalid", 80), timeout=1)
    with pytest.raises(NetworkBlockedError, match="connect to"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("192.0.2.1", 80))
    with pytest.raises(NetworkBlockedError, match="connect to"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("192.0.2.1", 80))

    assert len(network_guard_fixture) == 3
    network_guard_fixture.clear()


def test_loopback_and_unix_sockets_are_allowed(tmp_path: Path, network_guard_fixture: list[str]):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2) as client:
            accepted, _ = server.accept()
            accepted.close()
            assert client.fileno() >= 0

    path = str(tmp_path / "s")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_server:
        unix_server.bind(path)
        unix_server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_client:
            unix_client.connect(path)

    assert network_guard_fixture == []
