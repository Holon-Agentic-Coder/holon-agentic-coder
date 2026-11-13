"""The rejected attempt of a failed pre-push test run: its patch, failing tests and summary, kept for the next pass.

The resolver's working tree dies with the review-loop container, so when verification fails the uncommitted changes
are saved as a patch under the git-ignored ``.holon/review/rejected/`` directory of the (host-mounted) workspace,
next to a small JSON record naming the failing tests and the summary. Everything is redacted before it is capped and
written, and again when it is read back for the next resolver prompt. Nothing here raises: a failure to capture
degrades to no artefact.
"""

import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from sandbox_executor.entrypoint.pytest_summary import counts_line, summary_excerpt
from sandbox_executor.entrypoint.review_failure_detail import failed_test_ids
from sandbox_executor.knowledge import CredentialRedactor

REJECTED_DIR = os.path.join(".holon", "review", "rejected")
PATCH_MAX_CHARS = 200_000
SUMMARY_MAX_CHARS = 2000
PROMPT_PATCH_MAX_CHARS = 6000
_TRUNCATION_MARKER = "\n[... patch truncated ...]\n"
_DIFF_PATHSPEC = ["--", ".", ":(exclude)holon-knowledge/ledger/executions.jsonl", ":(exclude).holon"]


@dataclass(frozen=True)
class RejectedAttempt:
    """A failed attempt: the workspace-relative patch path (None when unsaved), failing tests and summary."""

    patch_path: str | None
    failed_tests: tuple[str, ...]
    summary: str
    head_sha: str = ""


def _git(workspace: str, args: list[str], env: dict[str, str]) -> str | None:
    """Run git in ``workspace`` and return its stdout, or None on any failure.

    The workspace is trusted explicitly: a container user that does not own a bind-mounted worktree would otherwise
    make every git call fail with "dubious ownership", leaving the diff (and so the patch) empty.
    """
    trust = ["-c", f"safe.directory={os.path.abspath(workspace)}"]
    try:
        proc = subprocess.run(
            ["git", *trust, *args], cwd=workspace, env=env, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def uncommitted_diff(workspace: str) -> str | None:
    """Return the diff of the working tree against HEAD, new files included, leaving the real index untouched.

    A temporary copy of the index takes the intent-to-add entries, so the repository's own index is never altered.
    """
    index_path = _git(workspace, ["rev-parse", "--path-format=absolute", "--git-path", "index"], dict(os.environ))
    if index_path is None:
        return None
    fd, scratch = tempfile.mkstemp(prefix="holon-rejected-index-")
    os.close(fd)
    try:
        with contextlib.suppress(OSError), open(index_path.strip(), "rb") as source, open(scratch, "wb") as target:
            target.write(source.read())
        env = {**os.environ, "GIT_INDEX_FILE": scratch}
        if _git(workspace, ["add", "-N", *_DIFF_PATHSPEC], env) is None:
            return None
        return _git(workspace, ["diff", "--no-color", "HEAD", *_DIFF_PATHSPEC], env)
    finally:
        with contextlib.suppress(OSError):
            os.remove(scratch)


def _cap(text: str, limit: int) -> str:
    """Cap ``text`` at ``limit`` characters, marking the cut."""
    return text if len(text) <= limit else text[:limit] + _TRUNCATION_MARKER


def _write(workspace: str, relative: str, text: str) -> bool:
    """Write ``text`` under ``workspace``; False when it cannot be written."""
    target = os.path.join(workspace, relative)
    try:
        os.makedirs(os.path.join(workspace, REJECTED_DIR), exist_ok=True)
        # A temporary name and an atomic replace, so a partial file is never recorded.
        with open(target + ".tmp", "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(target + ".tmp", target)
    except OSError:
        return False
    return True


def capture_rejected_attempt(workspace: str, iteration: int, raw_output: str) -> RejectedAttempt:
    """Save the redacted patch and a JSON record of the failing tests and summary for ``iteration``."""
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
    """Read the record saved for ``iteration``, redacted again on the way in; None when absent or unreadable."""
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
    """Remove every saved attempt, so a passing run leaves no stale evidence to feed back."""
    directory = os.path.join(workspace, REJECTED_DIR)
    with contextlib.suppress(OSError):
        for name in os.listdir(directory):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(directory, name))


def latest_attempt_for_head(workspace: str, head_sha: str) -> tuple[int, RejectedAttempt] | None:
    """Return the highest-numbered saved attempt made on ``head_sha``, so an attempt on an older head is ignored."""
    directory = os.path.join(workspace, REJECTED_DIR)
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    numbers = sorted(
        (
            int(name[5:-5])
            for name in names
            if name.startswith("iter-") and name[5:-5].isdigit() and name.endswith(".json")
        ),
        reverse=True,
    )
    for number in numbers:
        attempt = load_rejected_attempt(workspace, number)
        if attempt and attempt.head_sha == head_sha:
            return number, attempt
    return None


def read_saved_patch(workspace: str, attempt: RejectedAttempt, limit: int = PATCH_MAX_CHARS) -> str:
    """Return the saved patch redacted and capped at ``limit``, or an empty string when unreadable."""
    if not attempt.patch_path:
        return ""
    try:
        with open(os.path.join(workspace, attempt.patch_path), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ""
    return _cap(CredentialRedactor.redact_text(text), limit)


def previous_failure_section(workspace: str, head_sha: str) -> str:
    """Build the resolver prompt section for the latest failed attempt on ``head_sha``; empty when there is none."""
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
