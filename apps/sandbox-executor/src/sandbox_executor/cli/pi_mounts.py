"""Docker mount arguments for the pi runner.

Only pi's ``models.json`` is mounted from the host, read-only: the agent directory also holds ``auth.json``,
``sessions/`` and ``run-history.jsonl``, none of which a sandboxed agent needs. Docker creates the missing parents of a
bind-mounted file as root, so each parent is first given an owned scratch tmpfs; otherwise pi, running as an
unprivileged user, cannot create ``~/.pi/agent/sessions``.
"""

import os

CONTAINER_HOME = "/home/holon"
OWNED_TMPFS_OPTIONS = "uid=1000,gid=1000,mode=0700"

# (host directory relative to the home directory, container directory) holding a `models.json`
_MODELS_JSON_CANDIDATES = (
    (os.path.join(".pi", "agent"), f"{CONTAINER_HOME}/.pi/agent"),
    (os.path.join(".config", "pi"), f"{CONTAINER_HOME}/.config/pi"),
)
_ALWAYS_WRITABLE_DIRS = (f"{CONTAINER_HOME}/.pi", f"{CONTAINER_HOME}/.pi/agent")


def _owned_tmpfs(container_dir: str) -> list[str]:
    return ["--tmpfs", f"{container_dir}:{OWNED_TMPFS_OPTIONS}"]


def get_pi_mounts(home: str) -> list[str]:
    """Return docker arguments giving pi a writable home plus its read-only ``models.json``.

    Args:
        home: Host home directory searched for ``models.json``.

    Returns:
        Owned tmpfs mounts for every parent first, then a read-only bind per existing ``models.json``.
    """
    args: list[str] = []
    for container_dir in _ALWAYS_WRITABLE_DIRS:
        args.extend(_owned_tmpfs(container_dir))
    for host_dir, container_dir in _MODELS_JSON_CANDIDATES:
        host_file = os.path.join(home, host_dir, "models.json")
        if not os.path.exists(host_file):
            continue
        if container_dir not in _ALWAYS_WRITABLE_DIRS:
            args.extend(_owned_tmpfs(os.path.dirname(container_dir)))
            args.extend(_owned_tmpfs(container_dir))
        args.extend(["-v", f"{host_file}:{container_dir}/models.json:ro"])
    return args
