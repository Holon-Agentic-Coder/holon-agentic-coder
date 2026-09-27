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

import subprocess
import uuid

CONTAINER_REMOTE_PATH = "/mock_remote.git"
DEFAULT_FIXTURE_IMAGE = "holon/orchestrator"
IMAGE_UID = "1000:1000"

_SEED_SCRIPT = f"""\
set -eu
git init --bare -b main {CONTAINER_REMOTE_PATH}
rm -rf /tmp/holon-fixture-seed
git init -b main -q /tmp/holon-fixture-seed
cd /tmp/holon-fixture-seed
git config user.email test@holon.com
git config user.name "Holon Fixture"
mkdir -p holon-knowledge/ledger
: > holon-knowledge/ledger/intents.jsonl
git add -A
git commit -qm "fixture seed"
git remote add origin {CONTAINER_REMOTE_PATH}
git push -q origin main
# A freshly created volume is root-owned and the seed must run as root to populate it, so hand
# ownership to the image's unprivileged user; otherwise the role container cannot push into it.
chown -R {IMAGE_UID} {CONTAINER_REMOTE_PATH}
"""


class RemoteFixtureError(RuntimeError):
    """Raised when the seeded remote fixture cannot be created."""


def create_seeded_remote(image: str = DEFAULT_FIXTURE_IMAGE) -> str:
    """Creates a named volume holding a bare repository with one commit on ``main``.

    Returns the volume name. The caller must reclaim it with :func:`remove_remote_volume`, normally
    via ``self.addCleanup`` so a failing assertion cannot leak the volume.
    """
    volume = f"holon-it-{uuid.uuid4().hex[:16]}"
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
                f"{volume}:{CONTAINER_REMOTE_PATH}",
                "--entrypoint",
                "bash",
                image,
                "-c",
                _SEED_SCRIPT,
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
    return ["-v", f"{volume}:{CONTAINER_REMOTE_PATH}"]


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
            f"{volume}:{CONTAINER_REMOTE_PATH}",
            "--entrypoint",
            "git",
            image,
            "-C",
            CONTAINER_REMOTE_PATH,
            "show",
            f"{ref}:{path}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
