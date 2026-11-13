"""Host-side copy of a rejected resolver patch carried in the container's result payload."""

from __future__ import annotations

import os

from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.review_paths import REJECTED_DIR


def _within_rejected_dir(workspace: str, patch_path: str) -> bool:
    """True when the container-supplied ``patch_path`` is relative and resolves inside the rejected directory."""
    if os.path.isabs(patch_path):
        return False
    base = os.path.realpath(os.path.join(workspace, REJECTED_DIR))
    return os.path.commonpath([base, os.path.realpath(os.path.join(workspace, patch_path))]) == base


def _persist_rejected_patch(workspace: str, iteration: int, patch_path: str | None, patch_text: str) -> str | None:
    """Return the workspace-relative path of the rejected patch, writing ``patch_text`` there if the file is absent.

    The container's own file survives only on a bind mount; the payload text makes the evidence independent of it.
    Best-effort: a failed write leaves the container's path (or None) untouched.
    A ``patch_path`` outside the rejected directory is ignored in favour of the default ``iter-N.patch`` name.
    """
    if not patch_text.strip():
        return patch_path
    relative = os.path.join(REJECTED_DIR, f"iter-{iteration}.patch")
    if patch_path and _within_rejected_dir(workspace, patch_path):
        relative = patch_path
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
