"""Hermetic unit tests for repository Makefile targets and prerequisite checks.

This test module verifies Makefile behavior including the help target, dry-run
execution across all core targets, and prerequisite detection permutations for
tools such as the GitHub CLI (gh) and OpenSSL (openssl). Each make invocation runs with an
test-owned environment, so nothing here depends on the developer's shell.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
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


OS_LINUX = "DETECTED_OS=Linux"
OS_DARWIN = "DETECTED_OS=Darwin"

# The prerequisite list exactly as `make help` and README state it, asserted as one string.
# Asserting bare substrings instead is vacuous: a reviewer removed `gh` and `openssl` from
# the prerequisite description, planted those two words in an unrelated target's
# description, and every test stayed green while the documented list no longer named either
# tool. The shared constant is asserted against both `make help` and README by
# test_prerequisite_list_is_identical_in_make_help_and_readme, so the two cannot drift apart
# silently.
PREREQUISITE_HELP_LIST = "(fatal: uv, gh, openssl; advisory: GNU Make, npx, gh auth, Docker CLI/Buildx/daemon)"


def test_makefile_help_lists_core_targets(tmp_path: pathlib.Path) -> None:
    """Verify that make help documents all core targets and the fatal/advisory split."""
    # Run with the test-owned environment: `make help` inherits PATH, CI, MAKEFLAGS and
    # MAKELEVEL from the caller otherwise, which made the module's hermeticity claim false
    # for this one test even though its output does not depend on them today.
    result = _run_make(["help"], env=_setup_mock_env(tmp_path, mock_docker=False), check=True)
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

    assert PREREQUISITE_HELP_LIST in stdout, (
        "'make help' prerequisite list drifted from the documented fatal/advisory split "
        f"(expected exactly: {PREREQUISITE_HELP_LIST})"
    )


def test_makefile_detects_the_host_platform_without_an_os_override(tmp_path: pathlib.Path) -> None:
    """Pin `DETECTED_OS := $(shell uname -s)`, which every other test overrides away.

    Each prerequisite test passes OS_LINUX or OS_DARWIN on the command line, so nothing
    exercised autodetection: replacing the shell call with the constant `Linux` left all
    seven tests green, and no CI job runs check-prerequisites at all. That matters because
    the user-visible half of the target is per-OS install advice, and Darwin gating decides
    whether install-docker/install-homebrew fire - a developer on macOS would have been told
    to run `sudo apt install gh` with nothing reported red.
    """
    env = _setup_mock_env(tmp_path, mock_docker=False)
    result = _run_make(["-n", "check-prerequisites"], env=env)
    assert result.returncode == 0, result.stderr
    host_os = os.uname().sysname
    assert f"OS: {host_os} |" in result.stdout, (
        f"make did not expand DETECTED_OS to the host platform {host_os!r}; a hardcoded "
        f"value would silently misdirect install advice. Header seen: "
        f"{[line for line in result.stdout.splitlines() if 'OS:' in line]}"
    )


def test_makefile_dry_run_core_targets(tmp_path: pathlib.Path) -> None:
    """Verify that dry-run mode (make -n) exits 0 without running recipes across core targets.

    The PATH contains no docker/gh/openssl, so a recipe that actually executed its checks would
    exit non-zero; exit 0 therefore proves the in-shell guard fired and short-circuited before
    the checks ran. Note that ``make -n`` still executes recipe lines containing ``$(MAKE)``,
    which is why ``check-prerequisites`` guards itself in-shell rather than relying on dry-run
    alone.

    Only the four guard-bearing targets are real regression cases here: ``check-prerequisites``,
    ``check-docker``, ``install-docker`` and ``prerequisites``. The remaining three
    (``help``, ``build-images``, ``install-homebrew``) are inert under ``-n`` regardless of the
    guard, since their recipes echo or delegate without running prerequisite probes.
    """
    env = _setup_mock_env(tmp_path, mock_docker=False)
    guard_regression_targets = [
        "check-prerequisites",
        "check-docker",
        "install-docker",
        "prerequisites",
    ]
    inert_under_dry_run_targets = ["help", "build-images", "install-homebrew"]
    core_targets = guard_regression_targets + inert_under_dry_run_targets
    for target in core_targets:
        result = _run_make(["-n", target, OS_LINUX], env=env)
        msg = f"make -n {target} failed with code {result.returncode}. stdout: {result.stdout} stderr: {result.stderr}"
        assert result.returncode == 0, msg


def _write_exe(path: pathlib.Path, script: str) -> None:
    """Write an executable shell script at path."""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _create_openssl(bin_dir: pathlib.Path) -> None:
    """Create a mock openssl executable."""
    _write_exe(bin_dir / "openssl", "#!/bin/sh\necho 'OpenSSL 3.0.0'\n")


def _gh_script(auth_exit: int, auth_output: str = "") -> str:
    """Return a mock gh script whose `auth status` exits with auth_exit."""
    auth_echo = f'echo "{auth_output}"; ' if auth_output else ""
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "gh version 2.50.0 (2026-05-01)"; exit 0; fi\n'
        f'if [ "$1" = "auth" ] && [ "$2" = "status" ]; then {auth_echo}exit {auth_exit}; fi\n'
        "exit 0\n"
    )


def _create_auth_gh(bin_dir: pathlib.Path) -> None:
    """Create a mock authenticated gh executable."""
    _write_exe(bin_dir / "gh", _gh_script(0, "Logged in to github.com"))


def _create_unauth_gh(bin_dir: pathlib.Path) -> None:
    """Create a mock unauthenticated gh executable."""
    _write_exe(bin_dir / "gh", _gh_script(1))


def _create_uv(bin_dir: pathlib.Path) -> None:
    """Create a mock uv executable that answers --version, as a healthy install would."""
    _write_exe(bin_dir / "uv", "#!/bin/sh\necho 'uv 0.12.0'\n")


def _setup_mock_env(
    tmp_path: pathlib.Path,
    *,
    mock_gh: typing.Callable[[pathlib.Path], None] | None = None,
    mock_openssl: typing.Callable[[pathlib.Path], None] | None = None,
    mock_docker: bool = True,
    mock_uv: typing.Callable[[pathlib.Path], None] | None = None,
    uv_present: bool = True,
    npx_present: bool = True,
) -> dict[str, str]:
    """Create an isolated PATH environment with fake binaries and essential tools.

    The host OS is not controlled here: ``DETECTED_OS`` is assigned with ``:=`` in the
    Makefile, so tests must pass it as a make command-line variable (see ``OS_LINUX``).

    Args:
        tmp_path: Pytest temporary directory.
        mock_gh: Optional callable to configure mock gh executable.
        mock_openssl: Optional callable to configure mock openssl executable.
        mock_docker: Whether to provide a mock docker binary satisfying check-docker.
        mock_uv: Callable replacing the default healthy uv stub (see _create_uv).
        uv_present: Set False to omit uv entirely, exercising the fatal missing-uv path.
        npx_present: Set False to omit npx, exercising the advisory missing-npx path.

    Returns:
        Environment dictionary with isolated PATH.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    if uv_present:
        (mock_uv or _create_uv)(bin_dir)
    if npx_present:
        _write_exe(bin_dir / "npx", "#!/bin/sh\necho '11.0.0'\n")

    if mock_docker:
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
    ]
    for tool in essential_tools:
        target = shutil.which(tool)
        if target and os.path.exists(target):
            with contextlib.suppress(OSError):
                (clean_bin / tool).symlink_to(target)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{clean_bin}"
    # A pytest suite launched from inside a make recipe would otherwise inherit the parent's
    # MAKEFLAGS (including -n) and MAKELEVEL, silently turning every child make into a dry run.
    env["MAKEFLAGS"] = ""
    env["MAKELEVEL"] = ""
    return env


def test_check_prerequisites_fails_when_gh_missing(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites fails with exit code 2 when gh is missing.

    make reports a failed recipe as exit 2, not 1; the docstring said 1 while the assertion
    below has always measured 2.
    """
    env = _setup_mock_env(tmp_path, mock_gh=None, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 2  # make exits 2 when a recipe fails
    assert "GitHub CLI (gh) not found" in result.stdout
    assert "sudo apt install gh" in result.stdout


def test_check_prerequisites_warns_when_gh_unauthenticated(tmp_path: pathlib.Path) -> None:
    """Verify advisory warning when gh is present but unauthenticated."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_unauth_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 0
    assert "gh is not authenticated" in result.stdout
    assert "gh auth login" in result.stdout
    assert "OpenSSL 3.0.0" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def test_check_prerequisites_passes_when_gh_authenticated(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites passes with no warnings when gh is authenticated."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 0
    assert "gh version 2.50.0" in result.stdout
    assert "gh is not authenticated" not in result.stdout
    assert "All prerequisites are satisfied!" in result.stdout


def test_check_prerequisites_fails_when_openssl_missing(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites fails with exit code 2 when openssl is missing."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=None)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)
    assert result.returncode == 2  # make exits 2 when a recipe fails
    assert "openssl not found" in result.stdout
    assert "ca_generator.py" in result.stdout
    assert "sudo apt install openssl" in result.stdout


def test_check_prerequisites_darwin_install_instructions(tmp_path: pathlib.Path) -> None:
    """Verify that check-prerequisites prints brew install instructions on Darwin."""
    env = _setup_mock_env(tmp_path, mock_gh=None, mock_openssl=None)

    result = _run_make(["check-prerequisites", OS_DARWIN], env=env)
    assert result.returncode == 2  # make exits 2 when a recipe fails
    assert "brew install gh" in result.stdout
    assert "brew install openssl" in result.stdout


def test_prerequisite_list_is_identical_in_make_help_and_readme(tmp_path: pathlib.Path) -> None:
    """Assert the sync claim instead of merely asserting it in a comment.

    The constant's comment says it keeps `make help` and README from drifting apart. Until
    this test existed that was true of `make help` only: README restates the same list by
    hand at the Quick Start block and nothing compared the two, so either could change and
    stay green.
    """
    help_stdout = _run_make(["help"], env=_setup_mock_env(tmp_path, mock_docker=False), check=True).stdout
    assert PREREQUISITE_HELP_LIST in help_stdout
    readme_text = (_get_repo_root() / "README.md").read_text(encoding="utf-8")
    assert PREREQUISITE_HELP_LIST in readme_text, "README's prerequisite list drifted from the list `make help` prints"


def test_check_prerequisites_treats_docker_as_advisory_not_fatal(tmp_path: pathlib.Path) -> None:
    """Pin the advisory half of the documented split, which had no coverage.

    The list names uv, gh and openssl as fatal and GNU Make, npx and all of Docker as
    advisory, but only the fatal half was tested: turning the Docker probes into hard
    failures kept the suite green, so a change meant as a warning could start failing CI on
    any machine without a running daemon. Docker is absent here via mock_docker=False.

    Coverage stated honestly: the advisory missing-npx path is covered by its own test
    (npx_present=False), and the unusable-npx path by a stub whose --version returns nothing.
    """
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, mock_docker=False)
    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, f"the advisory Docker check was treated as fatal:\n{result.stdout}"
    assert "optional/advisory check(s) raised warnings" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def _create_silent_gh(bin_dir: pathlib.Path) -> None:
    """Create a gh that exists on PATH but reports no version (a broken or shadowed binary)."""
    _write_exe(bin_dir / "gh", "#!/bin/sh\nexit 0\n")


def _create_silent_openssl(bin_dir: pathlib.Path) -> None:
    """Create an openssl that exists on PATH but reports no version."""
    _write_exe(bin_dir / "openssl", "#!/bin/sh\nexit 0\n")


def test_check_prerequisites_fails_when_gh_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    """A gh that cannot answer `--version` must not be reported as satisfied.

    `command -v gh` succeeds for a broken binary, a crashed wrapper or a PATH entry that
    shadows the real install, and the probe used to print a green tick with an empty version
    and exit 0 - so the gate this whole PR exists to create would green-light a machine
    where the flow cannot resolve a single Pull Request ref.
    """
    env = _setup_mock_env(tmp_path, mock_gh=_create_silent_gh, mock_openssl=_create_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: gh is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def test_check_prerequisites_fails_when_openssl_is_present_but_unusable(
    tmp_path: pathlib.Path,
) -> None:
    """Same failure mode for openssl, whose consumer mints the proxy root CA."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_silent_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: openssl is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def _create_libressl(bin_dir: pathlib.Path) -> None:
    """Create an openssl that is really LibreSSL, as macOS's /usr/bin/openssl reports itself."""
    _write_exe(bin_dir / "openssl", "#!/bin/sh\necho 'LibreSSL 3.3.6'\n")


def test_check_prerequisites_warns_when_the_gate_is_satisfied_by_libressl(
    tmp_path: pathlib.Path,
) -> None:
    """LibreSSL satisfies `command -v openssl` but cannot build this repository's Root CA.

    ca_generator.py passes -addext three times and LibreSSL does not support it, so a green
    tick here can still precede a failing CA step. The remedy is deliberately advisory - the
    intent asked for a presence-and-version report, so failing the run for a non-OpenSSL
    build would exceed it - but it must not stay silent, which is what this test pins.
    """
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_libressl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "a non-OpenSSL build must warn, not fail"
    assert "LibreSSL 3.3.6" in result.stdout
    assert "Not OpenSSL" in result.stdout
    assert "optional/advisory check(s) raised warnings" in result.stdout


def _create_silent_uv(bin_dir: pathlib.Path) -> None:
    """Create a uv that exists on PATH but reports no version."""
    _write_exe(bin_dir / "uv", "#!/bin/sh\nexit 0\n")


def test_check_prerequisites_fails_when_uv_is_present_but_unusable(tmp_path: pathlib.Path) -> None:
    """`make help` names uv as fatal, so the unusable-tool rule must cover it too.

    gh and openssl gained an empty-version guard; uv had the same `command -v` plus unchecked
    version capture, so a shadowed or half-installed uv printed a green tick while `uv run
    task …` could not work. Before these two uv tests existed the whole uv block had no
    coverage at all: deleting it left every test green.
    """
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, mock_uv=_create_silent_uv)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: uv is on PATH" in result.stdout
    assert "All prerequisites are satisfied!" not in result.stdout


def test_check_prerequisites_fails_when_uv_is_missing(tmp_path: pathlib.Path) -> None:
    """The fatal path for uv, previously unasserted despite being listed first in help."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl, uv_present=False)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Missing: uv not found" in result.stdout
    assert "Install uv via" in result.stdout


def test_check_prerequisites_warns_without_failing_when_npx_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    """npx is documented advisory, so its absence must warn and exit 0.

    This is the path the fixture previously could not express: npx was written
    unconditionally, so the yellow branch had no test and turning it fatal stayed green.
    """
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
    """A broken npx must be reported as unusable, not as `✅ Found: npx v` with nothing after it."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    _write_exe(tmp_path / "bin" / "npx", "#!/bin/sh\nexit 0\n")

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, "an advisory tool's failure must not fail the run"
    assert "Unusable: npx is on PATH" in result.stdout
    assert "Found: npx v" not in result.stdout


def _create_whitespace_gh(bin_dir: pathlib.Path) -> None:
    """Create a gh whose --version emits only whitespace, which `[ -z ]` alone calls non-empty."""
    _write_exe(bin_dir / "gh", "#!/bin/sh\nprintf '   '\n")


def test_check_prerequisites_treats_a_whitespace_version_as_unusable(tmp_path: pathlib.Path) -> None:
    """`[ -z ]` is false for a blank-but-spaced string, so the guard must trim first.

    A tool printing "   " from --version is not reporting a version; without trimming the run
    prints a green tick with invisible content, which is the same false confidence the
    empty-string case was fixed for.
    """
    env = _setup_mock_env(
        tmp_path,
        mock_gh=_create_whitespace_gh,
        mock_openssl=_create_openssl,
    )

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 2
    assert "Unusable: gh is on PATH" in result.stdout


def test_detected_os_is_derived_from_uname_not_written_as_a_constant() -> None:
    """The runtime header check cannot tell `$(shell uname -s)` from a constant that equals it.

    Hardcoding `DETECTED_OS := Darwin` passes the host-matching test on a macOS runner and
    misdirects every Linux user, so the derivation itself is pinned here as well.
    """
    makefile_lines = (_get_repo_root() / "Makefile").read_text(encoding="utf-8").splitlines()
    detected = [line for line in makefile_lines if line.startswith("DETECTED_OS")]
    assert detected == ["DETECTED_OS := $(shell uname -s)"], (
        f"DETECTED_OS must be derived from `uname -s`, found: {detected}"
    )


def _probe_lines(stdout: str) -> list[str]:
    """Lines the target prints only when it actually runs its probes."""
    return [line for line in stdout.splitlines() if line.startswith("Checking ")]


def test_check_prerequisites_stays_inert_under_every_dry_run_spelling(tmp_path: pathlib.Path) -> None:
    """`-n`, `-s -n`, `-Bn` and `--dry-run` must all probe nothing."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    for flags in (["-n"], ["-n", "-s"], ["-Bn"], ["--dry-run"]):
        result = _run_make([*flags, "check-prerequisites"], env=env)
        assert result.returncode == 0, f"{flags} failed: {result.stderr}"
        assert _probe_lines(result.stdout) == [], f"{flags} executed the checks: {result.stdout}"


def test_check_prerequisites_runs_when_an_option_argument_spells_n(tmp_path: pathlib.Path) -> None:
    """`make -I incdir_n ...` must probe for real: GNU make keeps -n only in the first word.

    The guard used to scan every MAKEFLAGS word that is not a VAR=value or --long token, so
    the -I argument itself was scanned, "incdir_n" matched the n, and the target printed its
    banner, probed nothing and exited 0. Measured on this branch before the fix: 0 probe
    lines and exit 0. The sibling repository carries the same guard and had the same hole.
    """
    include_dir = tmp_path / "incdir_n"
    include_dir.mkdir()
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    result = _run_make(["-I", str(include_dir), "check-prerequisites"], env=env)

    probes = _probe_lines(result.stdout)
    assert probes, f"the guard misread an option argument as a dry run:\n{result.stdout}"
    assert any(line.startswith("Checking GitHub CLI") for line in probes)


def test_check_prerequisites_runs_when_a_command_line_assignment_spells_n(
    tmp_path: pathlib.Path,
) -> None:
    """The older first-word-only form failed here: 'Linux' contains an n."""
    env = _setup_mock_env(tmp_path, mock_gh=_create_auth_gh, mock_openssl=_create_openssl)
    result = _run_make(["DETECTED_OS=Linux", "check-prerequisites"], env=env)

    probes = _probe_lines(result.stdout)
    assert probes, f"the guard misread an assignment as a dry run:\n{result.stdout}"
    assert any(line.startswith("Checking uv") for line in probes)


def test_dry_run_guard_does_not_scan_every_makeflags_word() -> None:
    """Static pin: the word-scan form must not come back.

    Both wrong answers are measurable (see the two tests above), so the shape of the
    detector is pinned too: consult the compacted option cluster only.
    """
    makefile = (_get_repo_root() / "Makefile").read_text(encoding="utf-8")
    assert "findstring n,$(foreach" not in makefile, "MAKEFLAGS word scan is back"
    assert "DRY_RUN := $(if $(findstring n,$(MF_OPTION_CLUSTER)),1,)" in makefile


def _create_noisy_gh(bin_dir: pathlib.Path) -> None:
    """Create a gh that emits a loader warning before its version line."""
    _write_exe(
        bin_dir / "gh",
        "#!/bin/sh\nprintf 'dyld: warning, malformed path\\n'\nprintf 'gh version 2.50.0 (2026-05-01)\\n'\n",
    )


def _create_noisy_openssl(bin_dir: pathlib.Path) -> None:
    """Create an openssl that emits a loader warning before its version line."""
    _write_exe(
        bin_dir / "openssl",
        "#!/bin/sh\nprintf 'dyld: warning, malformed path\\n'\nprintf 'OpenSSL 3.0.0  1 Jan 2026\\n'\n",
    )


def test_version_probes_pick_the_version_line_not_the_first_line(tmp_path: pathlib.Path) -> None:
    """A warning printed before the version must not become the reported version.

    Taking the first line did two wrong things at once: it displayed `dyld: warning,
    malformed path` as the tool's version, and, because the OpenSSL classification keyed off
    that same first line, it warned "Not OpenSSL" about a perfectly working OpenSSL. The
    probes now look for the version line and classify on the whole output.
    """
    env = _setup_mock_env(tmp_path, mock_gh=_create_noisy_gh, mock_openssl=_create_noisy_openssl)

    result = _run_make(["check-prerequisites", OS_LINUX], env=env)

    assert result.returncode == 0, result.stdout
    assert "gh version 2.50.0" in result.stdout
    assert "OpenSSL 3.0.0" in result.stdout
    assert "Not OpenSSL" not in result.stdout, "a working OpenSSL was misclassified"
    assert "dyld" not in result.stdout, f"loader noise reported as a version: {result.stdout}"
