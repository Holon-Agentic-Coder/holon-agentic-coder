from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

from sandbox_executor.redaction import redact_agent_secrets

MergeMethodRunner = Callable[[list[str], str], subprocess.CompletedProcess[str]]

MERGE_FLAG = "--merge"
SQUASH_FLAG = "--squash"
REPO_SETTINGS_API_PATH = "repos/{owner}/{repo}"
_MAX_ERROR_DETAIL_CHARS = 300


def choose_merge_flag(settings: object) -> tuple[str | None, str]:
    if not isinstance(settings, dict):
        return None, "the repository merge settings could not be read"
    if settings.get("allow_merge_commit") is True:
        return MERGE_FLAG, ""
    if settings.get("allow_squash_merge") is True:
        return SQUASH_FLAG, ""
    return None, "the repository allows neither merge commits nor squash merges (rebase is never used)"


def lookup_merge_flag(repo_dir: str, runner: MergeMethodRunner) -> tuple[str | None, str]:
    proc = runner(["api", REPO_SETTINGS_API_PATH], repo_dir)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = (
            redact_agent_secrets(str(proc.stderr or "").strip())[:_MAX_ERROR_DETAIL_CHARS]
            or f"exit code {proc.returncode}"
        )
        return None, f"gh api {REPO_SETTINGS_API_PATH} failed: {detail}"
    try:
        settings = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, f"gh api {REPO_SETTINGS_API_PATH} did not return JSON"
    return choose_merge_flag(settings)
