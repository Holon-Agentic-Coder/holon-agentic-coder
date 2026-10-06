"""Fixture helpers for container-based integration tests.

These exist because a fixture that works under Docker Desktop can fail on a Linux CI runner. The
tests need a git remote that a *container* can clone from and push back to. Bind-mounting a host
temporary directory cannot provide that: on macOS the mount's ownership is remapped into the
container, while on Linux it keeps the host owner (the CI runner user), which is unrelated to the
image's unprivileged ``holon`` user (uid 1000). Every workaround for that mismatch was a dead end:

* ``git`` refuses a repository owned by a foreign uid (``detected dubious ownership``), and neither
  ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_<n>`` (not honoured by ``git clone``, verified against git
  2.47.3 in ``holon/orchestrator``) nor the ``file://`` transport (``safe.directory`` applies to
  ``file://`` sources too) can express the exception;
* the container uid cannot write the mount at all, so a push dies with ``remote unpack failed:
  unable to create temporary object directory``;
* relaxing the fixture to ``0o777`` is a CWE-732 insecure-permissions violation (CodeQL, high);
* ``--user $(id -u)`` breaks the image entrypoint because ``/home/holon`` is ``drwx------``;
* and whatever the container creates, the host process then cannot delete at teardown.

A named Docker volume removes all of it: one uid owns everything inside it, the fixture is never
visible to the host, and ``docker volume rm`` reclaims it regardless of inner ownership.
"""

import os
import shutil
import subprocess
import uuid

DEFAULT_FIXTURE_IMAGE = "holon/orchestrator"
IMAGE_UID = "1000:1000"


def remote_path(volume: str) -> str:
    """Return the in-container path at which *volume* is mounted, and the URL it is cloned from.

    Derived from the volume name, so every fixture instance owns a distinct path. It must not be a
    fixed literal: a shared path is shared state between concurrently running tests, and this one
    collides with the CLI's ``HOLON_MOCK_REMOTE_PATH`` default, which means a test would silently
    exercise that override branch -- or silently depend on it -- instead of the normal path.
    """
    return f"/holon-remote-{volume}.git"


def _seed_script(remote: str) -> str:
    return f"""\
set -eu
git init --bare -b main {remote}
rm -rf /tmp/holon-fixture-seed
git init -b main -q /tmp/holon-fixture-seed
cd /tmp/holon-fixture-seed
git config user.email test@holon.com
git config user.name "Holon Fixture"
mkdir -p holon-knowledge/ledger
: > holon-knowledge/ledger/intents.jsonl
git add -A
git commit -qm "fixture seed"
git remote add origin {remote}
git push -q origin main
# A freshly created volume is root-owned and the seed must run as root to populate it, so hand
# ownership to the image's unprivileged user; otherwise the role container cannot push into it.
chown -R {IMAGE_UID} {remote}
"""


class RemoteFixtureError(RuntimeError):
    """Raised when the seeded remote fixture cannot be created."""


def create_seeded_remote(image: str = DEFAULT_FIXTURE_IMAGE) -> str:
    """Creates a named volume holding a bare repository with one commit on ``main``.

    Returns the volume name. The caller must reclaim it with :func:`remove_remote_volume`, normally
    via ``self.addCleanup`` so a failing assertion cannot leak the volume.
    """
    volume = f"holon-it-{uuid.uuid4().hex[:16]}"
    remote = remote_path(volume)
    create = subprocess.run(["docker", "volume", "create", volume], capture_output=True, text=True, check=False)
    if create.returncode != 0:
        raise RemoteFixtureError(f"docker volume create failed: {create.stderr}")

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "root",
                "-v",
                f"{volume}:{remote}",
                "--entrypoint",
                "bash",
                image,
                "-c",
                _seed_script(remote),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.SubprocessError as exc:
        remove_remote_volume(volume)
        raise RemoteFixtureError(f"seeding docker run failed: {exc}") from exc

    if result.returncode != 0:
        remove_remote_volume(volume)
        raise RemoteFixtureError(
            f"seeding remote fixture failed with code {result.returncode}.\n"
            f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        )
    return volume


def remove_remote_volume(volume: str) -> None:
    """Best-effort removal of a fixture volume."""
    subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, text=True, check=False)


def remote_volume_args(volume: str) -> list[str]:
    """Docker arguments mounting the fixture volume at the path ``HOLON_REPO_URL`` refers to."""
    return ["-v", f"{volume}:{remote_path(volume)}"]


def remote_show_file(
    ref: str, path: str, volume: str, image: str = DEFAULT_FIXTURE_IMAGE
) -> subprocess.CompletedProcess:
    """Runs ``git show <ref>:<path>`` against the fixture repository from inside a container.

    The repository deliberately never exists on the host, so all inspection goes through the
    container; the inspecting container runs as the image's own user, which also owns the volume
    contents, so no ownership exception is required.
    """
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:{remote_path(volume)}",
            "--entrypoint",
            "git",
            image,
            "-C",
            remote_path(volume),
            "show",
            f"{ref}:{path}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------------------------
# Suite-level runner helpers, shared by the canary guard and the stress suite
#
# These live here so that the child-suite invocation exists in exactly one place. Two modules each
# assembling their own ``uv run pytest ...`` command line is how the runner choice and the marker
# selection drifted apart from ``.github/workflows/test-unit.yml`` once already.
# ---------------------------------------------------------------------------------------------

#: The only sanctioned way to run this suite (see ``.agents/rules.md``): never invoke ``python3``,
#: a bare ``pytest``, or a ``.venv`` binary. Never ``python -m unittest`` either -- ``unittest``
#: does not understand pytest markers, so it re-runs the ``integration_test``-marked tests that the
#: unit job deliberately deselects and that need images it never builds.
UV = "uv"

#: Marker expression matching the unit job. ``stress`` is excluded because that suite clones the
#: repository and re-runs the suite, so a child that selected it would spawn another child.
UNIT_MARKER_EXPRESSION = "not integration_test and not stress"

#: Repository root, derived from this file rather than assumed: ``pyproject.toml`` lives here and
#: is what supplies pytest's ``testpaths``/``pythonpath``/``markers`` configuration, so every child
#: suite run must use this directory as its cwd.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: The suite directory relative to :data:`REPO_ROOT`, also derived. Children run the whole
#: directory, so a new test file is covered the moment it is added.
TESTS_DIR = os.path.relpath(os.path.dirname(os.path.abspath(__file__)), REPO_ROOT)

#: Generous ceiling for a child suite run: process startup plus the whole non-integration suite.
CHILD_TIMEOUT_SECONDS = 1800


#: Sentinel set across all child suite environments to prevent nested child runners from
#: spawning downstream child suites.
CHILD_SUITE_SENTINEL = "HOLON_CHILD_SUITE_ACTIVE"


def pytest_suite_command(*extra_args: str) -> list[str]:
    """Build the sanctioned command that runs the whole suite in a child process.

    Launched from :data:`REPO_ROOT` with ``uv`` so the child inherits the repository's pytest
    configuration and marker policy. ``no:cacheprovider`` stops a child whose ``HOME`` points at a
    throwaway directory writing cache state, and ``--import-mode=importlib`` avoids
    ``prepend``-mode basename collisions if a sibling branch ever adds a ``conftest.py``.
    """
    return [
        UV,
        "run",
        "--no-sync",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "-q",
        "--tb=short",
        "-m",
        UNIT_MARKER_EXPRESSION,
        TESTS_DIR,
        *extra_args,
    ]


def simulated_container_env(sim_home: str, sentinel_var: str) -> dict[str, str]:
    """Build the environment of an executor container whose home is a throwaway directory.

    ``HOLON_REPO_DIR`` is deliberately absent: the bean 0019 incident happened precisely because
    nothing pinned the workspace, so an unpatched ``get_workspace_dir()`` resolves into
    ``sim_home``. ``sentinel_var`` is set so a child can never recurse into its own guard.
    """
    env = os.environ.copy()
    env["HOME"] = sim_home
    env["USER"] = "holon"
    env["USERNAME"] = "holon"
    env["HOLON_ROLE"] = "executor"
    env["HOLON_IN_SANDBOX"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("HOLON_REPO_DIR", None)
    env[sentinel_var] = "1"
    env[CHILD_SUITE_SENTINEL] = "1"
    # ``HOME`` no longer points at the real user home, so uv can neither find its managed
    # interpreters nor the project environment on its own: a child launched this way was observed
    # downloading CPython and building a fresh ``.venv`` inside the fixture, which then failed on
    # the missing ``holon`` console script. Name the environment explicitly and forbid downloads so
    # the child reuses the interpreter and installed dependencies this process is already running
    # under, and never reaches for the network.
    env["UV_PROJECT_ENVIRONMENT"] = project_environment()
    env["UV_PYTHON_DOWNLOADS"] = "never"
    return env


def project_environment() -> str:
    """Return the virtualenv a child suite run must use, preferring the one already active.

    Required because a child runs with a rewritten ``HOME`` (and, for the stress suite, outside the
    repository directory entirely), where uv's normal project-environment discovery does not apply.
    """
    active = os.environ.get("VIRTUAL_ENV")
    if active and os.path.isdir(active):
        return os.path.abspath(active)
    return os.path.join(REPO_ROOT, ".venv")


def uv_available() -> bool:
    """Return True when the sanctioned test runner is present.

    Callers skip rather than fall back to another runner: silently switching to ``unittest`` is
    what made the original guard measure the wrong artifact.
    """
    return bool(shutil.which(UV))
