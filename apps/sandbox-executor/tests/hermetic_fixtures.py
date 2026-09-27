"""Shared helpers for hermetic, container-mounted test fixtures.

These exist because a fixture that behaves correctly under Docker Desktop can fail on a Linux CI
runner. On macOS the bind-mounted host path is ownership-mapped into the container, so the
container's unprivileged ``holon`` user (uid 1000) can read and write it. On Linux the bind mount
keeps the host owner (the CI runner user, typically uid 1001) and the container user has no write
permission at all, and git additionally refuses to trust a repository owned by a different uid.

Both effects are invisible locally, so any test that bind-mounts a host temporary directory into a
container must handle them explicitly.
"""

import os
import shutil
import subprocess

CONTAINER_TRUSTED_REMOTE = "/mock_remote.git"


def write_trusted_gitconfig(tmp_dir: str, remote_path: str = CONTAINER_TRUSTED_REMOTE) -> str:
    """Writes a fixture git config declaring the bind-mounted remote trusted.

    Git aborts a clone from a repository owned by another uid with ``detected dubious ownership``.
    ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_<n>`` cannot express this because those variables are not
    honoured by ``git clone``, and the ``file://`` transport does not bypass the ownership check
    either, so the config has to be delivered as a real global config file via ``GIT_CONFIG_GLOBAL``.

    Returns the path to mount read-only into the container.
    """
    gitconfig_path = os.path.join(tmp_dir, "gitconfig")
    with open(gitconfig_path, "w", encoding="utf-8") as handle:
        handle.write(f"[safe]\n\tdirectory = {remote_path}\n")
    os.chmod(gitconfig_path, 0o644)
    return gitconfig_path


def container_root_args() -> list[str]:
    """Runs the test container as root so it can write the bind-mounted fixture.

    The image's default ``holon`` user (uid 1000) is unrelated to the CI runner user that owns the
    bind-mounted temporary fixture. On macOS Docker Desktop remaps ownership so the mismatch is
    invisible; on Linux the container uid cannot create objects in the mount, so a ``git push`` into
    the bind-mounted bare repository dies with ``remote unpack failed: unable to create temporary
    object directory``.

    Two alternatives were tried and rejected: running as the fixture owner's uid fails because
    ``/home/holon`` is not traversable by foreign uids (the image entrypoint becomes
    ``Permission denied``), and making the fixture group/world writable is an insecure-permissions
    violation that CodeQL reports as a high-severity finding.
    """
    return ["--user", "root"]


def purge_container_written_tree(path: str) -> None:
    """Deletes a fixture tree that a container wrote into, as root, then best-effort from the host.

    Files created by the container are not removable by the host test process on Linux CI, which
    would otherwise surface as a spurious ``PermissionError`` from ``TemporaryDirectory`` teardown
    long after the assertions passed. Register this with ``self.addCleanup`` so it also runs when an
    assertion fails.
    """
    if shutil.which("docker"):
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "root",
                "-v",
                f"{path}:/purge",
                "--entrypoint",
                "rm",
                "holon/base",
                "-rf",
                "/purge",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    shutil.rmtree(path, ignore_errors=True)
