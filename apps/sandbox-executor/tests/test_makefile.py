"""Hermetic unit tests for repository Makefile targets and prerequisite checks.

This test module verifies Makefile behavior including the help target, dry-run
execution across all core targets, and prerequisite detection permutations for
tools such as the GitHub CLI (gh) and OpenSSL (openssl). All tests are hermetic
and execute using isolated subprocess environments.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import typing


def _get_repo_root() -> pathlib.Path:
    """Return the absolute path to the repository root directory containing Makefile."""
    return pathlib.Path(__file__).resolve().parent.parent.parent.parent


def _run_make(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute make in the repository root directory.

    Args:
        args: List of arguments to pass to make (e.g. ["help"]).
        env: Optional environment dictionary override.
        check: Whether to raise CalledProcessError on non-zero exit code.

    Returns:
        CompletedProcess instance containing returncode, stdout, and stderr.
    """
    repo_root = _get_repo_root()
    return subprocess.run(
        ["make", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def test_makefile_help_lists_core_targets() -> None:
    """Verify that make help documents all core targets and tool prerequisites."""
    result = _run_make(["help"], check=True)
    stdout = result.stdout

    expected_targets = [
        "build-images",
        "check-prerequisites",
        "check-docker",
        "install-docker",
        "install-homebrew",
        "help",
    ]
    for target in expected_targets:
        assert target in stdout, f"Expected target '{target}' to be listed in 'make help' output"

    assert "gh" in stdout, "Expected 'gh' to be mentioned in 'make help' description"
    assert "openssl" in stdout, "Expected 'openssl' to be mentioned in 'make help' description"


def test_makefile_dry_run_core_targets() -> None:
    """Verify that dry-run mode (make -n) exits 0 without side effects across core targets."""
    core_targets = [
        "help",
        "check-prerequisites",
        "check-docker",
        "build-images",
        "install-docker",
        "install-homebrew",
        "prerequisites",
    ]
    for target in core_targets:
        result = _run_make(["-n", target])
        msg = f"make -n {target} failed with code {result.returncode}. stdout: {result.stdout} stderr: {result.stderr}"
        assert result.returncode == 0, msg


def _setup_mock_env(
    tmp_path: pathlib.Path,
    *,
    mock_gh: typing.Callable[[pathlib.Path], None] | None = None,
    mock_openssl: typing.Callable[[pathlib.Path], None] | None = None,
    mock_docker: bool = True,
    os_name: str = "Linux",
) -> dict[str, str]:
    """Create an isolated PATH environment with fake binaries and essential tools.

    Args:
        tmp_path: Pytest temporary directory.
        mock_gh: Optional callable to configure mock gh executable.
        mock_openssl: Optional callable to configure mock openssl executable.
        mock_docker: Whether to provide a mock docker binary satisfying check-docker.
        os_name: Operating system string to set for DETECTED_OS override in make.

    Returns:
        Environment dictionary with isolated PATH.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    uv_bin = bin_dir / "uv"
    uv_bin.write_text("#!/bin/sh\necho 'uv 0.12.0'\n", encoding="utf-8")
    uv_bin.chmod(0o755)

    npx_bin = bin_dir / "npx"
    npx_bin.write_text("#!/bin/sh\necho '11.0.0'\n", encoding="utf-8")
    npx_bin.chmod(0o755)

    if mock_docker:
        docker_bin = bin_dir / "docker"
        docker_script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "Docker version 27.0.0"; exit 0; fi\n'
            'if [ "$1" = "buildx" ] && [ "$2" = "version" ]; then echo "github.com/docker/buildx v0.15.0"; exit 0; fi\n'
            'if [ "$1" = "info" ]; then echo "Server Version: 27.0.0"; exit 0; fi\n'
            "exit 0\n"
        )
        docker_bin.write_text(docker_script, encoding="utf-8")
        docker_bin.chmod(0o755)

    if mock_gh is not None:
        mock_gh(bin_dir)

    if mock_openssl is not None:
        mock_openssl(bin_dir)

    clean_bin = tmp_path / "system_bin"
    clean_bin.mkdir(parents=True, exist_ok=True)
    essential_tools = [
        "sh",
        "bash",
        "make",
        "grep",
        "head",
        "uname",
        "printf",
        "echo",
        "cat",
        "rm",
        "mkdir",
    ]
    for tool in essential_tools:
        target = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if target and os.path.exists(target):
            with contextlib.suppress(OSError):
                (clean_bin / tool).symlink_to(target)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{clean_bin}"
    env["DETECTED_OS"] = os_name
    return env


def test_check_prerequisites_fails_when_gh_missing(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites fails with exit code 1 when gh is missing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    def _create_openssl(b: pathlib.Path) -> None:
        openssl_file = b / "openssl"
        openssl_file.write_text("#!/bin/sh\necho 'OpenSSL 3.0.0'\n", encoding="utf-8")
        openssl_file.chmod(0o755)

    env = _setup_mock_env(
        tmp_path,
        mock_gh=None,
        mock_openssl=_create_openssl,
    )

    result = _run_make(["check-prerequisites"], env=env)
    assert result.returncode != 0
    assert "GitHub CLI (gh) not found" in result.stdout
    assert "sudo apt install gh" in result.stdout


def test_check_prerequisites_warns_when_gh_unauthenticated(tmp_path: pathlib.Path) -> None:
    """Verify advisory warning when gh is present but unauthenticated."""

    def _create_unauth_gh(b: pathlib.Path) -> None:
        gh_file = b / "gh"
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.50.0 (2026-05-01)"; exit 0; fi\n'
            'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then exit 1; fi\n'
            "exit 0\n"
        )
        gh_file.write_text(script, encoding="utf-8")
        gh_file.chmod(0o755)

    def _create_openssl(b: pathlib.Path) -> None:
        openssl_file = b / "openssl"
        openssl_file.write_text("#!/bin/sh\necho 'OpenSSL 3.0.0'\n", encoding="utf-8")
        openssl_file.chmod(0o755)

    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_unauth_gh,
        mock_openssl=_create_openssl,
        mock_docker=True,
    )

    result = _run_make(["check-prerequisites"], env=env)
    assert result.returncode == 0
    assert "gh is not authenticated" in result.stdout
    assert "gh auth login" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def test_check_prerequisites_passes_when_gh_authenticated(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites passes with no warnings when gh is authenticated."""

    def _create_auth_gh(b: pathlib.Path) -> None:
        gh_file = b / "gh"
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.50.0 (2026-05-01)"; exit 0; fi\n'
            'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then echo "Logged in to github.com"; exit 0; fi\n'
            "exit 0\n"
        )
        gh_file.write_text(script, encoding="utf-8")
        gh_file.chmod(0o755)

    def _create_openssl(b: pathlib.Path) -> None:
        openssl_file = b / "openssl"
        openssl_file.write_text("#!/bin/sh\necho 'OpenSSL 3.0.0'\n", encoding="utf-8")
        openssl_file.chmod(0o755)

    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_openssl,
        mock_docker=True,
    )

    result = _run_make(["check-prerequisites"], env=env)
    assert result.returncode == 0
    assert "gh is not authenticated" not in result.stdout
    assert "All prerequisites are satisfied!" in result.stdout


def test_check_prerequisites_fails_when_openssl_missing(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites fails with exit code 1 when openssl is missing."""

    def _create_auth_gh(b: pathlib.Path) -> None:
        gh_file = b / "gh"
        script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "gh version 2.50.0 (2026-05-01)"; exit 0; fi\n'
            'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then exit 0; fi\n'
            "exit 0\n"
        )
        gh_file.write_text(script, encoding="utf-8")
        gh_file.chmod(0o755)

    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_auth_gh,
        mock_openssl=None,
        mock_docker=True,
    )

    result = _run_make(["check-prerequisites"], env=env)
    assert result.returncode != 0
    assert "openssl not found" in result.stdout
    assert "ca_generator.py" in result.stdout
    assert "sudo apt install openssl" in result.stdout


def test_check_prerequisites_darwin_install_instructions(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites prints brew install instructions on Darwin."""
    env = _setup_mock_env(
        tmp_path,
        mock_gh=None,
        mock_openssl=None,
        mock_docker=True,
        os_name="Darwin",
    )

    result = _run_make(["check-prerequisites", "DETECTED_OS=Darwin"], env=env)
    assert result.returncode != 0
    assert "brew install gh" in result.stdout
    assert "brew install openssl" in result.stdout
