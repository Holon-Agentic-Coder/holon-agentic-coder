from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from typing import Any

from sandbox_executor.flow.github import merge
from sandbox_executor.git import is_metadata_path
from sandbox_executor.pr_patch_paths import patch_paths
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.touch_set import normalise_touch_path

_DIFF_TOO_LARGE_MARKERS = ("http 406", "too_large")


def _normalise_changed_paths(raw_paths: Iterable[str]) -> tuple[str, ...]:
    normalised = (normalise_touch_path(path) for path in raw_paths)
    return tuple(dict.fromkeys(path for path in normalised if path and not is_metadata_path(path)))


def _format_command_failure(command: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = redact_agent_secrets((result.stderr or "").strip())[:300]
    return f"'{command}' failed: {detail or 'no output'}"


def _is_diff_too_large(result: subprocess.CompletedProcess[str]) -> bool:
    output = ((result.stderr or "").strip() or (result.stdout or "")).lower()
    return any(marker in output for marker in _DIFF_TOO_LARGE_MARKERS)


def _extract_entry_paths(entry: Any, command: str) -> list[str] | str:
    if not isinstance(entry, dict):
        return f"'{command}' returned a non-object file entry"
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        return f"'{command}' returned a file entry without a valid filename"
    previous = entry.get("previous_filename")
    if previous is not None and not isinstance(previous, str):
        return f"'{command}' returned a file entry with a non-string previous_filename"
    return [filename, previous] if previous else [filename]


def _parse_slurped_pr_files(stdout: str, command: str) -> tuple[str, ...] | str:
    if not stdout.strip():
        return f"'{command}' returned empty output"
    try:
        pages = json.loads(stdout)
    except json.JSONDecodeError:
        return f"'{command}' returned output that is not JSON"
    if not isinstance(pages, list):
        return f"'{command}' returned JSON that is not a list of pages"
    raw_paths: list[str] = []
    for page in pages:
        if not isinstance(page, list):
            return f"'{command}' returned a page that is not a list"
        for entry in page:
            extracted = _extract_entry_paths(entry, command)
            if isinstance(extracted, str):
                return extracted
            raw_paths.extend(extracted)
    return _normalise_changed_paths(raw_paths)


def _pull_request_files_via_api(run_gh: merge.GhRunner, number: int, repo_dir: str) -> tuple[str, ...] | str:
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{number}/files"
    command = f"gh api {endpoint}"
    result = run_gh(["api", "--paginate", "--slurp", endpoint], repo_dir)
    if result.returncode != 0:
        return _format_command_failure(command, result)
    return _parse_slurped_pr_files(result.stdout or "", command)


def _pull_request_changed_files(run_gh: merge.GhRunner, number: int, repo_dir: str) -> tuple[str, ...] | str:
    result = run_gh(["pr", "diff", str(number), "--color", "never"], repo_dir)
    if result.returncode == 0:
        return _normalise_changed_paths(patch_paths(result.stdout or ""))
    if _is_diff_too_large(result):
        return _pull_request_files_via_api(run_gh, number, repo_dir)
    return _format_command_failure(f"gh pr diff {number}", result)
