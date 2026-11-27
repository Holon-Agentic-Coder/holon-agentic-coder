import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from sandbox_executor.entrypoint.pytest_summary import counts_line, summary_excerpt
from sandbox_executor.entrypoint.review_failure_detail import failed_test_ids
from sandbox_executor.knowledge import CredentialRedactor
from sandbox_executor.ledger.fragments import PENDING_EXCLUDE_PATHSPEC
from sandbox_executor.review_paths import REJECTED_DIR

PATCH_MAX_CHARS = 200_000
SUMMARY_MAX_CHARS = 2000
PROMPT_PATCH_MAX_CHARS = 6000
_TRUNCATION_MARKER = "\n[... patch truncated ...]\n"
_DIFF_PATHSPEC = [
    "--",
    ".",
    ":(exclude)holon-knowledge/ledger/executions.jsonl",
    PENDING_EXCLUDE_PATHSPEC,
    ":(exclude).holon",
]


@dataclass(frozen=True)
class RejectedAttempt:
    patch_path: str | None
    failed_tests: tuple[str, ...]
    summary: str
    head_sha: str = ""


def _git(workspace: str, args: list[str], env: dict[str, str]) -> str | None:
    safe_directory_flags = ["-c", f"safe.directory={os.path.abspath(workspace)}"]
    try:
        proc = subprocess.run(
            ["git", *safe_directory_flags, *args],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def uncommitted_diff(workspace: str) -> str | None:
    index_path = _git(workspace, ["rev-parse", "--path-format=absolute", "--git-path", "index"], dict(os.environ))
    if index_path is None:
        return None
    fd, scratch_index_path = tempfile.mkstemp(prefix="holon-rejected-index-")
    os.close(fd)
    try:
        with (
            contextlib.suppress(OSError),
            open(index_path.strip(), "rb") as source,
            open(scratch_index_path, "wb") as target,
        ):
            target.write(source.read())
        scratch_index_env = {**os.environ, "GIT_INDEX_FILE": scratch_index_path}
        if _git(workspace, ["add", "-N", *_DIFF_PATHSPEC], scratch_index_env) is None:
            return None
        return _git(workspace, ["diff", "--no-color", "HEAD", *_DIFF_PATHSPEC], scratch_index_env)
    finally:
        with contextlib.suppress(OSError):
            os.remove(scratch_index_path)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + _TRUNCATION_MARKER


def _write(workspace: str, relative: str, text: str) -> bool:
    target = os.path.join(workspace, relative)
    atomic_tmp_path = target + ".tmp"
    try:
        os.makedirs(os.path.join(workspace, REJECTED_DIR), exist_ok=True)
        # why: write to a temporary sibling and atomically replace so a partial file is never recorded
        with open(atomic_tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(atomic_tmp_path, target)
    except OSError:
        return False
    return True


def capture_rejected_attempt(workspace: str, iteration: int, raw_output: str) -> RejectedAttempt:
    output = CredentialRedactor.redact_text(raw_output)
    diff = uncommitted_diff(workspace)
    head = (_git(workspace, ["rev-parse", "HEAD"], dict(os.environ)) or "").strip()
    patch_rel = os.path.join(REJECTED_DIR, f"iter-{iteration}.patch")
    saved = bool(diff and diff.strip()) and _write(
        workspace, patch_rel, _cap(CredentialRedactor.redact_text(diff or ""), PATCH_MAX_CHARS)
    )
    attempt = RejectedAttempt(
        patch_path=patch_rel if saved else None,
        failed_tests=tuple(failed_test_ids(output)),
        summary=summary_excerpt(output, SUMMARY_MAX_CHARS),
        head_sha=head,
    )
    record = {
        "patch_path": attempt.patch_path,
        "failed_tests": list(attempt.failed_tests),
        "summary": attempt.summary,
        "counts": counts_line(output),
        "head_sha": head,
    }
    _write(workspace, os.path.join(REJECTED_DIR, f"iter-{iteration}.json"), json.dumps(record, indent=2))
    return attempt


def load_rejected_attempt(workspace: str, iteration: int) -> RejectedAttempt | None:
    try:
        with open(os.path.join(workspace, REJECTED_DIR, f"iter-{iteration}.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tests = data.get("failed_tests")
    patch = data.get("patch_path")
    return RejectedAttempt(
        patch_path=patch if isinstance(patch, str) else None,
        failed_tests=tuple(CredentialRedactor.redact_text(str(t)) for t in tests) if isinstance(tests, list) else (),
        summary=CredentialRedactor.redact_text(str(data.get("summary") or "")),
        head_sha=str(data.get("head_sha") or ""),
    )


def clear_rejected_attempts(workspace: str) -> None:
    directory = os.path.join(workspace, REJECTED_DIR)
    with contextlib.suppress(OSError):
        for name in os.listdir(directory):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(directory, name))


def _parse_iteration_number(filename: str) -> int | None:
    if filename.startswith("iter-") and filename.endswith(".json") and filename[5:-5].isdigit():
        return int(filename[5:-5])
    return None


def latest_attempt_for_head(workspace: str, head_sha: str) -> tuple[int, RejectedAttempt] | None:
    directory = os.path.join(workspace, REJECTED_DIR)
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    numbers = sorted(
        (number for name in names if (number := _parse_iteration_number(name)) is not None),
        reverse=True,
    )
    for number in numbers:
        attempt = load_rejected_attempt(workspace, number)
        if attempt and attempt.head_sha == head_sha:
            return number, attempt
    return None


def read_saved_patch(workspace: str, attempt: RejectedAttempt, limit: int = PATCH_MAX_CHARS) -> str:
    if not attempt.patch_path:
        return ""
    try:
        with open(os.path.join(workspace, attempt.patch_path), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ""
    return _cap(CredentialRedactor.redact_text(text), limit)


def previous_failure_section(workspace: str, head_sha: str) -> str:
    found = latest_attempt_for_head(workspace, head_sha)
    if found is None:
        return ""
    number, attempt = found
    lines = [f"\n## Previous attempt failed verification (iteration {number})\n"]
    lines.append("Your earlier change broke the tests, so it was not committed. Do not repeat that change.\n")
    if attempt.failed_tests:
        lines.append("Failing tests:\n" + "\n".join(f"- `{test}`" for test in attempt.failed_tests) + "\n")
    if attempt.summary:
        lines.append(f"Test summary:\n```text\n{attempt.summary}\n```\n")
    patch = read_saved_patch(workspace, attempt, PROMPT_PATCH_MAX_CHARS)
    if patch:
        lines.append(f"Rejected diff (also saved at `{attempt.patch_path}`):\n```diff\n{patch}\n```\n")
    elif attempt.patch_path:
        lines.append(f"The rejected diff is saved at `{attempt.patch_path}`; read it before changing code.\n")
    return "\n".join(lines)
