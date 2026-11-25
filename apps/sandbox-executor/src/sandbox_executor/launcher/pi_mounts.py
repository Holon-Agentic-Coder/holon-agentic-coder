import os

CONTAINER_HOME = "/home/holon"
OWNED_TMPFS_OPTIONS = "uid=1000,gid=1000,mode=0700"
_MODELS_JSON_FILENAME = "models.json"

_MODELS_JSON_CANDIDATES = (
    (os.path.join(".pi", "agent"), f"{CONTAINER_HOME}/.pi/agent"),
    (os.path.join(".config", "pi"), f"{CONTAINER_HOME}/.config/pi"),
)
_ALWAYS_WRITABLE_DIRS = (f"{CONTAINER_HOME}/.pi", f"{CONTAINER_HOME}/.pi/agent")


def _owned_tmpfs(container_dir: str) -> list[str]:
    return ["--tmpfs", f"{container_dir}:{OWNED_TMPFS_OPTIONS}"]


def _owned_parent_tmpfs(container_dir: str) -> list[str]:
    return [
        *_owned_tmpfs(os.path.dirname(container_dir)),
        *_owned_tmpfs(container_dir),
    ]


def get_pi_mounts(home: str) -> list[str]:
    args: list[str] = []
    for container_dir in _ALWAYS_WRITABLE_DIRS:
        args.extend(_owned_tmpfs(container_dir))
    for host_dir, container_dir in _MODELS_JSON_CANDIDATES:
        host_file = os.path.join(home, host_dir, _MODELS_JSON_FILENAME)
        if not os.path.exists(host_file):
            continue
        if container_dir not in _ALWAYS_WRITABLE_DIRS:
            args.extend(_owned_parent_tmpfs(container_dir))
        args.extend(["-v", f"{host_file}:{container_dir}/{_MODELS_JSON_FILENAME}:ro"])
    return args
