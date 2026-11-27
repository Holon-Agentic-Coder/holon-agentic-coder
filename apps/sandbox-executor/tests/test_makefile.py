from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import subprocess
import typing

import pytest

from tests.make_dry_run import SharedMakeRuns


def _get_repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent.parent.parent


def _run_make(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo_root = _get_repo_root()
    return subprocess.run(
        ["make", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


# why: the environment is built on first use inside a test, so it is the one the autouse guards leave.
@pytest.fixture(scope="module")
def default_make(tmp_path_factory: pytest.TempPathFactory) -> SharedMakeRuns:
    env_dir = tmp_path_factory.mktemp("default-make-env")
    env: dict[str, str] = {}

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        if not env:
            env.update(_setup_mock_env(env_dir, mock_docker=False))
        return _run_make(args, env=env)

    return SharedMakeRuns("default", run)


OS_LINUX = "DETECTED_OS=Linux"
OS_DARWIN = "DETECTED_OS=Darwin"
MAKE_RECIPE_FAILURE_EXIT = 2

# why: asserted as one string; bare substrings let a reviewer move gh and openssl into another target's
# why: description while every test stayed green.
PREREQUISITE_HELP_LIST = (
    "(fatal: uv, gh, openssl which must be OpenSSL not LibreSSL; advisory: GNU Make, npx, "
    "gh auth, Docker CLI/Buildx/daemon)"
)


def test_makefile_help_lists_core_targets(default_make: SharedMakeRuns) -> None:
    # why: otherwise make help inherits PATH, CI, MAKEFLAGS and MAKELEVEL from the caller.
    result = default_make(["help"])
    assert result.returncode == 0, result.stderr
    stdout = result.stdout

    expected_targets = [
        "build-images",
        "build-image",
        "check-prerequisites",
        "check-docker",
        "install-docker",
        "install-homebrew",
        "tools",
        "lint",
        "lint-json",
        "lint-yaml",
        "lint-shell",
        "lint-md",
        "help",
    ]
    for target in expected_targets:
        assert target in stdout, f"Expected target '{target}' to be listed in 'make help' output"

    assert PREREQUISITE_HELP_LIST in stdout, (
        "'make help' prerequisite list drifted from the documented fatal/advisory split "
        f"(expected exactly: {PREREQUISITE_HELP_LIST})"
    )


# why: every other test overrides DETECTED_OS, so replacing the uname call with a constant stayed green
# why: and macOS users would have been given apt install advice.
def test_makefile_detects_the_host_platform_without_an_os_override(default_make: SharedMakeRuns) -> None:
    result = default_make(["-n", "check-prerequisites"])
    assert result.returncode == 0, result.stderr
    host_os = os.uname().sysname
    assert f"OS: {host_os} |" in result.stdout, (
        f"make did not expand DETECTED_OS to the host platform {host_os!r}; a hardcoded "
        f"value would silently misdirect install advice. Header seen: "
        f"{[line for line in result.stdout.splitlines() if 'OS:' in line]}"
    )


# why: PATH lacks docker/gh/openssl, so exit 0 proves the in-shell guard fired; make -n still runs
# why: $(MAKE) lines. Only check-prerequisites, check-docker, install-docker and prerequisites carry the
# why: guard; the other targets are inert under -n anyway.
def test_makefile_dry_run_core_targets(default_make: SharedMakeRuns) -> None:
    guard_regression_targets = [
        "check-prerequisites",
        "check-docker",
        "install-docker",
        "prerequisites",
        "tools",
        "lint",
        "lint-json",
        "lint-yaml",
        "lint-shell",
        "lint-md",
    ]
    inert_under_dry_run_targets = ["help", "build-images", "build-image", "install-homebrew"]
    core_targets = guard_regression_targets + inert_under_dry_run_targets
    for target in core_targets:
        result = default_make(["-n", target, OS_LINUX])
        msg = f"make -n {target} failed with code {result.returncode}. stdout: {result.stdout} stderr: {result.stderr}"
        assert result.returncode == 0, msg


def test_makefile_build_image_alias(default_make: SharedMakeRuns) -> None:
    res_images = default_make(["-n", "build-images", OS_LINUX])
    res_image = default_make(["-n", "build-image", OS_LINUX])
    assert res_images.returncode == 0
    assert res_image.returncode == 0
    assert "./apps/sandbox-executor/build_all_images.sh" in res_image.stdout
    assert res_image.stdout == res_images.stdout


def _write_exe(path: pathlib.Path, script: str) -> None:
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _create_openssl(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "openssl", "#!/bin/sh\necho 'OpenSSL 3.0.0'\n")


def _gh_script(auth_exit: int, auth_output: str = "") -> str:
    auth_echo = f'echo "{auth_output}"; ' if auth_output else ""
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "gh version 2.50.0 (2026-05-01)"; exit 0; fi\n'
        f'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then {auth_echo}exit {auth_exit}; fi\n'
        "exit 0\n"
    )


def _create_auth_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "gh", _gh_script(0, "Logged in to github.com"))


def _create_unauth_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "gh", _gh_script(1))


def _create_uv(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "uv", "#!/bin/sh\necho 'uv 0.12.0'\n")


def _create_silent_docker(bin_dir: pathlib.Path) -> None:
    docker_script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then exit 0; fi\n'
        'if [ "$1" = "buildx" ] && [ "$2" = "version" ]; then echo "github.com/docker/buildx v0.15.0"; exit 0; fi\n'
        'if [ "$1" = "info" ]; then echo "Server Version: 27.0.0"; exit 0; fi\n'
        "exit 0\n"
    )
    _write_exe(bin_dir / "docker", docker_script)


def _create_silent_buildx(bin_dir: pathlib.Path) -> None:
    docker_script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Docker version 27.0.0"; exit 0; fi\n'
        'if [ "$1" = "buildx" ] && [ "$2" = "version" ]; then exit 0; fi\n'
        'if [ "$1" = "info" ]; then echo "Server Version: 27.0.0"; exit 0; fi\n'
        "exit 0\n"
    )
    _write_exe(bin_dir / "docker", docker_script)


def _create_mock_toolbelt(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "git", "#!/bin/sh\necho 'git version 2.43.0'\n")
    _write_exe(
        bin_dir / "jq",
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "jq-1.7"; exit 0; fi\n'
        'python3 -c "\n'
        "import sys, json\n"
        "files = [a for a in sys.argv[1:] if not a.startswith('-') and a != '.']\n"
        "for f in files:\n"
        "    for line in open(f):\n"
        "        if line.strip():\n"
        "            json.loads(line)\n"
        '" "$@" 2>/dev/null || exit 1\n',
    )
    _write_exe(
        bin_dir / "shellcheck",
        '#!/bin/sh\nif [ "$1" = "--version" ] || [ "$1" = "-V" ]; then echo "version: 0.9.0"; exit 0; fi\n'
        'python3 -c "\n'
        "import sys\n"
        "files = [a for a in sys.argv[1:] if not a.startswith('-')]\n"
        "for f in files:\n"
        "    content = open(f).read()\n"
        "    if 'syntax_error' in content or 'invalid' in content:\n"
        "        sys.exit(1)\n"
        '" "$@" 2>/dev/null || exit 1\n',
    )


# why: DETECTED_OS is assigned with := in the Makefile, so tests pass it on the make command line.
def _setup_mock_env(  # noqa: PLR0913
    tmp_path: pathlib.Path,
    *,
    mock_gh: typing.Callable[[pathlib.Path], None] | None = None,
    mock_openssl: typing.Callable[[pathlib.Path], None] | None = None,
    mock_docker: bool | typing.Callable[[pathlib.Path], None] = True,
    mock_uv: typing.Callable[[pathlib.Path], None] | None = None,
    uv_present: bool = True,
    npx_present: bool = True,
    mock_toolbelt: bool = False,
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    if uv_present:
        (mock_uv or _create_uv)(bin_dir)
    if npx_present:
        _write_exe(bin_dir / "npx", "#!/bin/sh\necho '11.0.0'\n")

    if callable(mock_docker):
        mock_docker(bin_dir)
    elif mock_docker:
        docker_script = (
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "Docker version 27.0.0"; exit 0; fi\n'
            'if [ "$1" = "buildx" ] && [ "$2" = "version" ]; then echo "github.com/docker/buildx v0.15.0"; exit 0; fi\n'
            'if [ "$1" = "info" ]; then echo "Server Version: 27.0.0"; exit 0; fi\n'
            "exit 0\n"
        )
        _write_exe(bin_dir / "docker", docker_script)

    if mock_gh is not None:
        mock_gh(bin_dir)

    if mock_openssl is not None:
        mock_openssl(bin_dir)

    if mock_toolbelt:
        _create_mock_toolbelt(bin_dir)

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
        "tr",
        "echo",
        "cat",
        "rm",
        "mkdir",
        "find",
        "python3",
    ]
    for tool in essential_tools:
        target = shutil.which(tool)
        if target and os.path.exists(target):
            with contextlib.suppress(OSError):
                (clean_bin / tool).symlink_to(target)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{clean_bin}"
    # why: a suite launched from a make recipe inherits MAKEFLAGS (including -n) and MAKELEVEL, turning
    # why: every child make into a dry run.
    env["MAKEFLAGS"] = ""
    env["MAKELEVEL"] = ""
    return env


def test_check_prerequisites_fails_when_gh_missing(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=None, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == MAKE_RECIPE_FAILURE_EXIT
    assert "GitHub CLI (gh) not found" in result.stdout
    assert "sudo apt install gh" in result.stdout


def test_check_prerequisites_warns_when_gh_unauthenticated(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_unauth_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 0
    assert "gh is not authenticated" in result.stdout
    assert "gh auth login" in result.stdout
    assert "OpenSSL 3.0.0" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def test_check_prerequisites_passes_when_gh_authenticated(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 0
    assert "gh version 2.50.0" in result.stdout
    assert "gh is not authenticated" not in result.stdout
    assert "All prerequisites are satisfied!" in result.stdout


def test_check_prerequisites_fails_when_openssl_missing(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=None)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == MAKE_RECIPE_FAILURE_EXIT
    assert "openssl not found" in result.stdout
    assert "launcher/root_ca.py" in result.stdout
    assert "sudo apt install openssl" in result.stdout


def test_check_prerequisites_darwin_install_instructions(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=None, mock_openssl=None)

    result = _run_make(["check-prerequisites", OS_DARWIN], env=env)
    assert result.returncode == MAKE_RECIPE_FAILURE_EXIT
    assert "brew install gh" in result.stdout
    assert "brew install openssl" in result.stdout


# why: README restates the prerequisite list by hand, so either copy could drift and stay green.
def test_prerequisite_list_is_identical_in_make_help_and_readme(default_make: SharedMakeRuns) -> None:
    help_result = default_make(["help"])
    assert help_result.returncode == 0, help_result.stderr
    help_stdout = help_result.stdout
    assert PREREQUISITE_HELP_LIST in help_stdout
    readme_text = (_get_repo_root() / "README.md").read_text(encoding="utf-8")
    assert PREREQUISITE_HELP_LIST in readme_text, "README's prerequisite list drifted from the list `make help` prints"


# why: only the fatal half was tested; turning Docker probes fatal stayed green and would fail CI on
# why: machines without a daemon.
def test_check_prerequisites_treats_docker_as_advisory_not_fatal(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, mock_docker=False)
    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, f"the advisory Docker check was treated as fatal:\n{result.stdout}"
    assert "optional/advisory check(s) raised warnings" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def _create_silent_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "gh", "#!/bin/sh\nexit 0\n")


def _create_silent_openssl(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "openssl", "#!/bin/sh\nexit 0\n")


# why: command -v succeeds for a broken or shadowed gh, and the probe used to print a green tick with an
# why: empty version and exit 0.
def test_check_prerequisites_fails_when_gh_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_silent_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: gh is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


# why: openssl's consumer mints the proxy root CA.
def test_check_prerequisites_fails_when_openssl_is_present_but_unusable(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_silent_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: openssl is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def _create_libressl(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "openssl", "#!/bin/sh\necho 'LibreSSL 3.3.6'\n")


# why: launcher/root_ca_openssl.py passes -addext, which LibreSSL lacks; the intent asked only for a
# why: presence-and-version report, so the remedy is advisory but must not stay silent.
def test_check_prerequisites_warns_when_the_gate_is_satisfied_by_libressl(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_libressl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "a non-OpenSSL build must warn, not fail"
    assert "LibreSSL 3.3.6" in result.stdout
    assert "Not OpenSSL" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def _create_silent_uv(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "uv", "#!/bin/sh\nexit 0\n")


# why: make help names uv as fatal, and deleting the whole uv block used to leave every test green.
def test_check_prerequisites_fails_when_uv_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, mock_uv=_create_silent_uv)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: uv is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def test_check_prerequisites_fails_when_uv_is_missing(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, uv_present=False)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Missing: uv not found" in result.stdout
    assert "Install uv via" in result.stdout


# why: npx used to be written unconditionally, so turning its advisory branch fatal stayed green.
def test_check_prerequisites_warns_without_failing_when_npx_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_openssl,
        npx_present=False,
    )

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "missing npx was treated as fatal"
    assert "Missing: npx not found" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def test_check_prerequisites_reports_unusable_npx_without_calling_it_found(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    _write_exe(tmp_path / "bin" / "npx", "#!/bin/sh\nexit 0\n")

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "an advisory tool's failure must not fail the run"
    assert "Unusable: npx is on PATH" in result.stdout
    assert "Found: npx v" not in result.stdout


# why: [ -z ] alone calls a whitespace-only version non-empty.
def _create_whitespace_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "gh", "#!/bin/sh\nprintf '   '\n")


def test_check_prerequisites_treats_a_whitespace_version_as_unusable(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_whitespace_gh,
        mock_openssl=_create_openssl,
    )

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: gh is on PATH" in result.stdout


# why: hardcoding DETECTED_OS := Darwin passes the host-matching test on a macOS runner.
def test_detected_os_is_derived_from_uname_not_written_as_a_constant() -> None:
    makefile_lines = (_get_repo_root() / "Makefile").read_text(encoding="utf-8").splitlines()
    detected = [line for line in makefile_lines if line.startswith("DETECTED_OS")]
    assert detected == ["DETECTED_OS := $(shell uname -s)"], (
        f"DETECTED_OS must be derived from `uname -s`, found: {detected}"
    )


def _probe_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("Checking ")]


def test_check_prerequisites_stays_inert_under_every_dry_run_spelling(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    for flags in (["-n"], ["-n", "-s"], ["-Bn"], ["--dry-run"]):
        result = _run_make([*flags, "check-prerequisites"], env=env)
        assert result.returncode == 0, f"{flags} failed: {result.stderr}"
        assert _probe_lines(result.stdout) == [], f"{flags} executed the checks: {result.stdout}"


# why: GNU make keeps -n only in the first MAKEFLAGS word; scanning every word matched the n in an -I
# why: argument and silently skipped every probe, a hole the sibling repository shares.
def test_check_prerequisites_runs_when_an_option_argument_spells_n(tmp_path: pathlib.Path) -> None:
    include_dir = tmp_path / "incdir_n"
    include_dir.mkdir()
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    result = _run_make(["-I", str(include_dir), "check-prerequisites"], env=env)

    probes = _probe_lines(result.stdout)
    assert probes, f"the guard misread an option argument as a dry run:\n{result.stdout}"
    assert any(line.startswith("Checking GitHub CLI") for line in probes)


# why: the older first-word-only form failed here because 'Linux' contains an n.
def test_check_prerequisites_runs_when_a_command_line_assignment_spells_n(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    result = _run_make(["DETECTED_OS=Linux", "check-prerequisites"], env=env)

    probes = _probe_lines(result.stdout)
    assert probes, f"the guard misread an assignment as a dry run:\n{result.stdout}"
    assert any(line.startswith("Checking uv") for line in probes)


# why: both wrong detector shapes are measurable, so the compacted-option-cluster form is pinned.
def test_dry_run_guard_does_not_scan_every_makeflags_word() -> None:
    makefile = (_get_repo_root() / "Makefile").read_text(encoding="utf-8")
    assert "findstring n,$(foreach" not in makefile, "MAKEFLAGS word scan is back"
    assert "DRY_RUN := $(if $(findstring n,$(MF_OPTION_CLUSTER)),1,)" in makefile


def _create_noisy_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(
        bin_dir / "gh",
        "#!/bin/sh\nprintf 'dyld: warning, malformed path\\n'\nprintf 'gh version 2.50.0 (2026-05-01)\\n'\n",
    )


def _create_noisy_openssl(bin_dir: pathlib.Path) -> None:
    _write_exe(
        bin_dir / "openssl",
        "#!/bin/sh\nprintf 'dyld: warning, malformed path\\n'\nprintf 'OpenSSL 3.0.0  1 Jan 2026\\n'\n",
    )


# why: taking the first line showed a dyld warning as the version and warned Not OpenSSL about a
# why: working OpenSSL.
def test_version_probes_pick_the_version_line_not_the_first_line(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_noisy_gh, mock_openssl=_create_noisy_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, result.stdout
    assert "gh version 2.50.0" in result.stdout
    assert "OpenSSL 3.0.0" in result.stdout
    assert "Not OpenSSL" not in result.stdout, "a working OpenSSL was misclassified"
    assert "dyld" not in result.stdout, f"loader noise reported as a version: {result.stdout}"


def _create_failing_gh(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "gh", "#!/bin/sh\nprintf 'gh version 2.50.0 (2026-05-01)\\n'\nexit 3\n")


def _create_failing_openssl(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "openssl", "#!/bin/sh\nprintf 'OpenSSL 3.0.0  1 Jan 2026\\n'\nexit 2\n")


def _create_compatible_not_openssl(bin_dir: pathlib.Path) -> None:
    _write_exe(bin_dir / "openssl", "#!/bin/sh\nprintf 'FooSSL 1.0 (OpenSSL compatible)\\n'\n")


# why: `|| true` discarded the status, so a gh that printed its version and exited 3 passed.
def test_check_prerequisites_fails_when_a_fatal_probe_exits_nonzero(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_failing_gh,
        mock_openssl=_create_failing_openssl,
    )

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: gh is on PATH but 'gh --version' failed (exit 3)" in result.stdout
    assert "Unusable: openssl is on PATH but 'openssl version' failed (exit 2)" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def test_check_prerequisites_warns_when_the_advisory_npx_probe_exits_nonzero(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    _write_exe(tmp_path / "bin" / "npx", "#!/bin/sh\nprintf '11.0.0\\n'\nexit 4\n")

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "an advisory probe failure must not fail the run"
    assert "Unusable: npx is on PATH but 'npx --version' failed (exit 4)" in result.stdout


# why: the Root CA step needs -addext, so classification reads the version line's first token rather
# why: than any mention of OpenSSL.
def test_check_prerequisites_warns_for_a_build_that_only_claims_openssl_compatibility(
    tmp_path: pathlib.Path,
) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_compatible_not_openssl,
    )

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "a non-OpenSSL build warns; it is not fatal"
    assert "Not OpenSSL" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def test_check_docker_fails_when_docker_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_docker=_create_silent_docker,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_openssl,
    )
    result = _run_make(["check-docker", OS_LINUX], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "Unusable: docker is on PATH" in result.stdout
    assert "Found: Docker version" not in result.stdout


def test_check_docker_fails_when_buildx_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_docker=_create_silent_buildx,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_openssl,
    )
    result = _run_make(["check-docker", OS_LINUX, "AUTO_INSTALL=false"], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "Unusable: docker buildx is on PATH" in result.stdout
    assert "Found: github.com/docker/buildx" not in result.stdout


def test_tools_target_reports_paths_and_versions(tmp_path: pathlib.Path) -> None:
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_auth_gh,
        mock_openssl=_create_openssl,
        mock_docker=True,
        mock_toolbelt=True,
    )
    result = _run_make(["tools", OS_LINUX], env=env)
    assert result.returncode == 0, f"make tools failed: stdout={result.stdout} stderr={result.stderr}"
    stdout = result.stdout
    for tool in ["git", "jq", "shellcheck", "uv", "gh", "npx", "openssl"]:
        assert tool in stdout, f"Expected {tool} to be reported by make tools"
    assert "All toolbelt tools are present." in stdout


def test_lint_json_fails_on_malformed_fixture(tmp_path: pathlib.Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "malformed.json").write_text("{ unclosed: json\n", encoding="utf-8")

    env = _setup_mock_env(tmp_path, mock_docker=False, mock_toolbelt=True)
    result = _run_make(["lint-json", f"TARGET_DIR={fixture_dir}", OS_LINUX], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "JSON/JSONL validation failed" in result.stdout


def test_lint_yaml_fails_on_malformed_fixture(tmp_path: pathlib.Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "malformed.yaml").write_text("invalid: : yaml\n", encoding="utf-8")

    env = _setup_mock_env(tmp_path, mock_docker=False, mock_toolbelt=True)
    bin_dir = tmp_path / "bin"
    _write_exe(bin_dir / "npx", "#!/bin/sh\nexit 1\n")

    result = _run_make(["lint-yaml", f"TARGET_DIR={fixture_dir}", OS_LINUX], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "YAML validation failed" in result.stdout


def test_lint_shell_fails_on_malformed_fixture(tmp_path: pathlib.Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "malformed.sh").write_text("#!/bin/sh\nsyntax_error unquoted\n", encoding="utf-8")

    env = _setup_mock_env(tmp_path, mock_docker=False, mock_toolbelt=True)
    result = _run_make(["lint-shell", f"TARGET_DIR={fixture_dir}", OS_LINUX], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "ShellCheck validation failed" in result.stdout


def test_lint_md_fails_on_unformatted_fixture(tmp_path: pathlib.Path) -> None:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "unformatted.md").write_text("# Title\n\n\n\nExtra blank lines\n", encoding="utf-8")

    env = _setup_mock_env(tmp_path, mock_docker=False, mock_toolbelt=True)
    bin_dir = tmp_path / "bin"
    _write_exe(bin_dir / "npx", "#!/bin/sh\nexit 1\n")

    result = _run_make(["lint-md", f"TARGET_DIR={fixture_dir}", OS_LINUX], env=env)
    assert result.returncode != 0, f"Expected non-zero exit code, got {result.returncode}"
    assert "Markdown formatting check failed" in result.stdout
