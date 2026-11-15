"""Hermetic guard that keeps the repository self-contained.

No tracked file may live under, or name, the directories of the tooling that drives this repository from outside (a
top-level local-notes directory and a hidden agent-state directory), and none may cite an external task identifier
or a review-loop finding identifier. Comments, docstrings and help text describe the behaviour they guard instead.

The append-only ledgers, the ``plans/`` and ``executions/`` records, and the ``holon-knowledge/kb/`` entries (generated
by calibration from review text) are historical and are skipped. Repository-native
finding ids used as data (for example ``R1-F1`` or ``FIND-CI-001``) are not citations and never match.

Allowlist (:data:`ALLOWLIST`): ``.gitignore`` may hold exactly one line naming the hidden agent-state directory, so that
outside tooling state stays untracked, and the workspace reset in ``scripts/ab_measure_all_methods.py`` may exclude it
from ``git clean`` so that state survives.

The tracked-file list comes from ``git ls-files`` on the local checkout (no network); the scan reads files only.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

HISTORICAL_PREFIXES = ("holon-knowledge/ledger/", "holon-knowledge/kb/", "plans/", "executions/")

# Assembled from fragments so this module never matches its own rules.
LOCAL_NOTES_DIR = "to" + "do"
HIDDEN_STATE_DIR = "." + "sub" + "agent"
FORBIDDEN_DIRS = (LOCAL_NOTES_DIR, HIDDEN_STATE_DIR)

#: ``(path, exact line, reason)``: the only lines allowed to break a rule.
ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    (".gitignore", HIDDEN_STATE_DIR, "keeps the outside tooling's agent state untracked"),
    (
        "scripts/ab_measure_all_methods.py",
        f'                "--exclude={HIDDEN_STATE_DIR}/",',
        "keeps the workspace reset's git clean from deleting the outside tooling's agent state",
    ),
)

_PATH_BOUNDARY = r"""(?:^|(?<=[\s"'`/=(]))"""
FORBIDDEN_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(_PATH_BOUNDARY + re.escape(LOCAL_NOTES_DIR) + r"/"), "local-notes directory"),
    (re.compile(_PATH_BOUNDARY + re.escape(HIDDEN_STATE_DIR) + r"(?![\w.-])"), "hidden agent-state directory"),
    (re.compile(r"\bbean[ _-]?[0-9]{4}\b", re.IGNORECASE), "external task identifier"),
    (re.compile(r"\b[CR][0-9]+[A-Z]?-[NI][0-9]+\b"), "review-loop finding citation"),
    (re.compile(r"#[0-9]+ C[0-9]+\b"), "review-loop finding citation"),
    (re.compile(r"\breview finding N[0-9]+\b"), "review-loop finding citation"),
)


def is_historical(path: str) -> bool:
    """Whether ``path`` is an append-only ledger, a plan or execution record, or a generated KB entry."""
    return path.startswith(HISTORICAL_PREFIXES)


def is_allowlisted(path: str, line: str) -> bool:
    """Whether ``line`` of ``path`` is exactly an allowlisted entry."""
    return any(path == allowed_path and line == allowed_line for allowed_path, allowed_line, _ in ALLOWLIST)


def find_path_violation(path: str) -> str | None:
    """A violation message if ``path`` lives under a forbidden directory, else ``None``."""
    top = path.split("/", 1)[0]
    if top in FORBIDDEN_DIRS:
        return f"{path}: tracked under a forbidden directory"
    return None


def find_hygiene_violations(path: str, text: str) -> list[str]:
    """``path:line: reason`` for every forbidden prefix or identifier in ``text``."""
    if is_historical(path):
        return []
    violations = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if is_allowlisted(path, line):
            continue
        violations.extend(
            f"{path}:{line_no}: {reason}" for pattern, reason in FORBIDDEN_TEXT_PATTERNS if pattern.search(line)
        )
    return violations


def _tracked_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return [path for path in listed.stdout.split("\0") if path]


def _read_text(path: str) -> str | None:
    """The UTF-8 text of a regular tracked file; ``None`` for gitlinks, symlinks and binary files."""
    full = REPO_ROOT / path
    if full.is_symlink() or not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def test_tracked_tree_has_no_hygiene_violations() -> None:
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("the working tree is not a git checkout, so there is no tracked-file list to scan")
    violations: list[str] = []
    for path in _tracked_files():
        if is_historical(path):
            continue
        path_violation = find_path_violation(path)
        if path_violation:
            violations.append(path_violation)
        text = _read_text(path)
        if text is not None:
            violations.extend(find_hygiene_violations(path, text))

    assert violations == [], "Repository hygiene violations:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "line",
    [
        f"see {LOCAL_NOTES_DIR}/notes.md",
        f"default='{LOCAL_NOTES_DIR}/cache'",
        f"WIRE_LOG_DIR={LOCAL_NOTES_DIR}/logs",
        f"`{HIDDEN_STATE_DIR}/review/`",
        f"{HIDDEN_STATE_DIR}",
        f'os.path.join(ws, "{HIDDEN_STATE_DIR}")',
        "fixes " + "Be" + "an 1234",
        "the " + "be" + "an 0019 incident",
        "never as a pass (" + "C10" + "-N1)",
        "# " + "R2" + "-I1 repro",
        "the doctor never crashes (" + "#94" + " C21)",
        "beyond http(s) (" + "review finding" + " N2)",
    ],
)
def test_detector_fires_on_forbidden_text(line: str) -> None:
    assert find_hygiene_violations("docs/example.md", line)


@pytest.mark.parametrize(
    "line",
    [
        "R1-F1 and FIND-CI-001 are review finding ids used as data",
        "import os  # noqa: F401",
        "two beans 12 ways",
        "mytodo/ and /tmp/review are fine",
        "httpx and subagents are words, not paths",
        "write scratch under tmp/review/",
    ],
)
def test_detector_ignores_legitimate_text(line: str) -> None:
    assert find_hygiene_violations("docs/example.md", line) == []


@pytest.mark.parametrize("forbidden_dir", FORBIDDEN_DIRS)
def test_tracked_path_under_a_forbidden_directory_is_a_violation(forbidden_dir: str) -> None:
    assert find_path_violation(f"{forbidden_dir}/script.py")
    assert find_path_violation(f"scripts/{forbidden_dir}.py") is None


@pytest.mark.parametrize("prefix", HISTORICAL_PREFIXES)
def test_historical_records_are_skipped(prefix: str) -> None:
    assert find_hygiene_violations(f"{prefix}record.md", f"{LOCAL_NOTES_DIR}/x and {HIDDEN_STATE_DIR}") == []


def test_only_the_exact_allowlisted_line_is_skipped() -> None:
    assert find_hygiene_violations(".gitignore", HIDDEN_STATE_DIR) == []
    assert find_hygiene_violations(".gitignore", f"{HIDDEN_STATE_DIR}/review/")
    assert find_hygiene_violations("docs/other.md", HIDDEN_STATE_DIR)
