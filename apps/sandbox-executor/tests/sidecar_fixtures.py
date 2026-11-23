"""Shared fakes, fixtures and proxy-URL tables for the launcher's sidecar, coherence and proxy-env tests."""

import os
import secrets
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sandbox_executor import cli
from sandbox_executor.launcher import proxy_image, proxy_paths, root_ca
from sandbox_executor.launcher.root_ca import generate_root_ca

from tests.leak_assertions import assert_no_fragment


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeDocker:
    """Records docker invocations and replays canned results for the token-reduction flow."""

    def __init__(self, spawn=None, port_stdout="127.0.0.1:32768\n", network_stderr=""):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []
        self.spawn = spawn if spawn is not None else _completed(stdout="containerid")
        self.port_stdout = port_stdout
        self.network_stderr = network_stderr

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        self.envs.append(kwargs.get("env"))
        head = cmd[:3] if len(cmd) >= 3 else list(cmd)
        if head == ["docker", "network", "create"]:
            return _completed(returncode=1 if self.network_stderr else 0, stderr=self.network_stderr)
        if head == ["docker", "network", "rm"]:
            return _completed()
        if head[:2] == ["docker", "run"]:
            return self.spawn
        if head[:2] == ["docker", "port"]:
            return _completed(stdout=self.port_stdout)
        return _completed()

    def joined(self) -> str:
        return "\n".join(" ".join(call) for call in self.calls)


def clean_sidecar_state() -> Iterator[None]:
    """Clear the module-level sidecar state before and after a test (drive it from a local autouse fixture)."""
    cli._sidecar_state.container_name = None
    cli._sidecar_state.network_name = None
    cli._sidecar_state.network_created = False
    yield
    cli._sidecar_state.container_name = None
    cli._sidecar_state.network_name = None
    cli._sidecar_state.network_created = False


def isolate_host_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every host-side write (CA dir, proxy cache, wire logs, cache) inside tmp_path and hand out a real CA."""
    monkeypatch.setattr(os.path, "expanduser", lambda path: str(tmp_path / "home" / path.lstrip("~/")))
    # The default wire-log and cache directories sit under the per-user state directory (~/.holon/).
    monkeypatch.setattr(proxy_paths, "_find_git_root", lambda *a, **k: str(tmp_path / "repo"))
    ca_dir = tmp_path / "certs"
    # No local holon-coherence image: the sidecar resolves to the GHCR image and never runs `docker image inspect`.
    monkeypatch.setattr(proxy_image, "local_coherence_image", lambda: None)
    monkeypatch.setattr(root_ca, "generate_root_ca", lambda *a, **k: generate_root_ca(cert_dir=str(ca_dir)))
    return tmp_path


_RAW_DELIMITER_PROXY_FORMS = [
    f"{prefix}alice:{{a}}{delim}{{b}}@127.0.0.1:3128"
    for prefix in ("", "//", "http://", "socks5://")
    for delim in ("/", "?", "#")
] + ["alice:{a}//{b}@127.0.0.1:3128"]


def _digit_head(digits: int) -> str:
    """A runtime all-digit password head above 65535, so `alice:<head>` reads as a bad `host:port`."""
    return str(secrets.randbelow(9 * 10 ** (digits - 1)) + 10 ** (digits - 1))


_DIGIT_HEAD_PROXY_FORMS = [
    f"{prefix}alice:{{head}}{delim}{{rest}}@127.0.0.1:3128"
    for prefix in ("", "//", "http://", "socks5://")
    for delim in ("/", "?", "#")
] + ["alice:{head}//{rest}@127.0.0.1:3128"]


def _assert_value_withheld(text: str, head: str, value: str) -> None:
    assert head not in text
    assert_no_fragment(text, value)
