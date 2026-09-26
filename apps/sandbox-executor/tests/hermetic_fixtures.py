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


def relax_bind_mount_permissions(path: str) -> None:
    """Makes a host fixture writable by the container's uid.

    A read-only-ish ``0700`` temporary directory is fine for the test process itself, but the
    container's uid differs from the host uid on Linux CI, so a ``git push`` into a bind-mounted
    bare repository fails with ``remote unpack failed: unable to create temporary object
    directory`` unless the fixture is group/world writable.
    """
    for root, dirs, files in os.walk(path):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o777)
        for name in files:
            os.chmod(os.path.join(root, name), 0o666)
    os.chmod(path, 0o777)
