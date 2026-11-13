"""Host-side copy of a rejected resolver patch carried in the container's result payload."""

from __future__ import annotations

import os

from sandbox_executor.knowledge import CredentialRedactor

REJECTED_DIR = os.path.join(".holon", "review", "rejected")


def _persist_rejected_patch(workspace: str, iteration: int, patch_path: str | None, patch_text: str) -> str | None:
    """Return the workspace-relative path of the rejected patch, writing ``patch_text`` there if the file is absent.

    The container's own file survives only on a bind mount; the payload text makes the evidence independent of it.
    Best-effort: a failed write leaves the container's path (or None) untouched.
    """
    if not patch_text.strip():
        return patch_path
    relative = patch_path or os.path.join(REJECTED_DIR, f"iter-{iteration}.patch")
    target = os.path.join(workspace, relative)
    if os.path.isfile(target):
        return relative
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".tmp", "w", encoding="utf-8") as handle:
            handle.write(CredentialRedactor.redact_text(patch_text))
        os.replace(target + ".tmp", target)
    except OSError:
        return patch_path
    return relative
