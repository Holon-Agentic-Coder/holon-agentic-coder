"""Which merge method the flow may use for a child PR: a merge commit when allowed, otherwise a squash, never a rebase.

The repository's allowed methods are read from ``gh api repos/{owner}/{repo}`` (``gh`` fills the placeholders from the
working directory's git remote) before anything is written. ``allow_merge_commit`` and ``allow_squash_merge`` count as
allowed only when they are exactly ``true``: GitHub returns them only for a token with push access, so a missing,
``null`` or non-boolean value is not allowed. A failed lookup or no allowed method is a refusal (fail closed).
``allow_rebase_merge`` is never consulted. ``gh`` is reached only through the runner the caller passes in.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

from sandbox_executor.redaction import redact_agent_secrets

#: Runs ``gh <args>`` in a directory and returns the completed process (the same shape as ``merge.GhRunner``).
MergeMethodRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

MERGE_FLAG = "--merge"
SQUASH_FLAG = "--squash"
REPO_SETTINGS_API_PATH = "repos/{owner}/{repo}"


def choose_merge_flag(settings: object) -> tuple[str | None, str]:
    """Return the ``gh pr merge`` flag the repository ``settings`` allow, preferring a merge commit to a squash.

    Args:
        settings: The parsed ``gh api repos/{owner}/{repo}`` response; anything but a dict is unreadable.

    Returns:
        tuple[str | None, str]: ``(MERGE_FLAG, "")`` when merge commits are allowed, ``(SQUASH_FLAG, "")`` when only
        squash merges are, else ``(None, reason)``. Pure: no side-effects.
    """
    if not isinstance(settings, dict):
        return None, "the repository merge settings could not be read"
    if settings.get("allow_merge_commit") is True:
        return MERGE_FLAG, ""
    if settings.get("allow_squash_merge") is True:
        return SQUASH_FLAG, ""
    return None, "the repository allows neither merge commits nor squash merges (rebase is never used)"


def lookup_merge_flag(repo_dir: str, runner: MergeMethodRunner) -> tuple[str | None, str]:
    """Read the repository's allowed merge methods with ``gh api`` and return the flag to merge a child PR with.

    Args:
        repo_dir: Working directory whose git remote names the repository.
        runner: Runs ``gh <args>`` in a directory; tests pass a fake.

    Returns:
        tuple[str | None, str]: ``(flag, "")`` or ``(None, reason)``. A non-zero exit, empty output or invalid JSON is
        a refusal; the reason never carries a token. Side-effects: one read-only ``gh api`` call.
    """
    proc = runner(["api", REPO_SETTINGS_API_PATH], repo_dir)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = redact_agent_secrets(str(proc.stderr or "").strip())[:300] or f"exit code {proc.returncode}"
        return None, f"gh api {REPO_SETTINGS_API_PATH} failed: {detail}"
    try:
        settings = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, f"gh api {REPO_SETTINGS_API_PATH} did not return JSON"
    return choose_merge_flag(settings)
