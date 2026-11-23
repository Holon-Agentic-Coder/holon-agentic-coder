"""Docker mount arguments and tmpfs configuration for Antigravity."""

from __future__ import annotations

# The parent /home/holon/.gemini/antigravity-cli stays :ro for credential isolation.
# brain/ and conversations/ must be writable for agy's OnTaskCompleted background-task wakeup and runtime state.
ANTIGRAVITY_WRITABLE_TMPFS: tuple[str, ...] = (
    "/home/holon/.gemini/config:uid=1000,gid=1000",
    "/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000",
    "/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000",
    "/home/holon/.gemini/antigravity-cli/brain:uid=1000,gid=1000",
    "/home/holon/.gemini/antigravity-cli/conversations:uid=1000,gid=1000",
)


def antigravity_session_mounts(source: str) -> list[str]:
    """Construct read-only session mount with writable tmpfs overlays for Antigravity."""
    mounts = ["-v", f"{source}:/home/holon/.gemini/antigravity-cli:ro"]
    for spec in ANTIGRAVITY_WRITABLE_TMPFS:
        mounts.extend(["--tmpfs", spec])
    return mounts
