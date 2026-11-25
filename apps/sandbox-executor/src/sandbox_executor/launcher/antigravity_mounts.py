from __future__ import annotations

_ANTIGRAVITY_CLI_CONTAINER_DIR = "/home/holon/.gemini/antigravity-cli"

ANTIGRAVITY_WRITABLE_TMPFS: tuple[str, ...] = (
    "/home/holon/.gemini/config:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/log:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/cache:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/brain:uid=1000,gid=1000",
    f"{_ANTIGRAVITY_CLI_CONTAINER_DIR}/conversations:uid=1000,gid=1000",
)


def antigravity_session_mounts(source: str) -> list[str]:
    mounts = ["-v", f"{source}:{_ANTIGRAVITY_CLI_CONTAINER_DIR}:ro"]
    for spec in ANTIGRAVITY_WRITABLE_TMPFS:
        mounts.extend(["--tmpfs", spec])
    return mounts
