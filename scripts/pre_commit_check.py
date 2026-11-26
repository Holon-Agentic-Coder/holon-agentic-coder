#!/usr/bin/env python3
"""Pre-commit security guard and scratch file detector.

Validates git staged files or explicit file paths to prevent accidental commits of:
1. Hardcoded API keys, tokens, private keys, and sensitive credentials.
2. Temporary scratch files, unmerged diffs, or stray artifacts in forbidden directories.

Strictly preserves leading directory dots (e.g. .index/, .agents/) by using
`.removeprefix('./')` instead of `.lstrip('./')`.
"""

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence

try:
    from scripts.check_self_explaining import main as check_self_explaining
    from scripts.pre_commit_credentials import _GITHUB_CREDENTIAL_MARKERS as _GITHUB_CREDENTIAL_MARKERS
    from scripts.pre_commit_credentials import CREDENTIAL_PATTERNS as CREDENTIAL_PATTERNS
    from scripts.pre_commit_credentials import FALSE_POSITIVE_INDICATORS as FALSE_POSITIVE_INDICATORS
    from scripts.pre_commit_credentials import GITHUB_CREDENTIAL_PATTERN_NAMES as GITHUB_CREDENTIAL_PATTERN_NAMES
    from scripts.pre_commit_credentials import TEST_FILE_WHITELIST, check_file_credentials, normalise_path
    from scripts.pre_commit_credentials import find_github_credentials as find_github_credentials
    from scripts.pre_commit_spelling import SPELLING_EXEMPT_PATTERNS as SPELLING_EXEMPT_PATTERNS
    from scripts.pre_commit_spelling import US_SPELLING_VARIANTS as US_SPELLING_VARIANTS
    from scripts.pre_commit_spelling import check_spelling_orthography
except ImportError:
    from check_self_explaining import main as check_self_explaining
    from pre_commit_credentials import _GITHUB_CREDENTIAL_MARKERS as _GITHUB_CREDENTIAL_MARKERS
    from pre_commit_credentials import CREDENTIAL_PATTERNS as CREDENTIAL_PATTERNS
    from pre_commit_credentials import FALSE_POSITIVE_INDICATORS as FALSE_POSITIVE_INDICATORS
    from pre_commit_credentials import GITHUB_CREDENTIAL_PATTERN_NAMES as GITHUB_CREDENTIAL_PATTERN_NAMES
    from pre_commit_credentials import TEST_FILE_WHITELIST, check_file_credentials, normalise_path
    from pre_commit_credentials import find_github_credentials as find_github_credentials
    from pre_commit_spelling import SPELLING_EXEMPT_PATTERNS as SPELLING_EXEMPT_PATTERNS
    from pre_commit_spelling import US_SPELLING_VARIANTS as US_SPELLING_VARIANTS
    from pre_commit_spelling import check_spelling_orthography

# Forbidden scratch file patterns (relative to repo root)
SCRATCH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Temporary directory file", re.compile(r"^tmp/.*")),
    ("Scratch directory file", re.compile(r"^scratch/.*")),
    ("Temporary test file", re.compile(r".*\.test_tmp\.py$")),
    ("Temporary file extension", re.compile(r".*\.(?:tmp|bak|orig|swp)$")),
]


def is_scratch_file(path: str) -> tuple[bool, str]:
    """Check if file matches any forbidden scratch pattern unless whitelisted.

    Args:
        path: Normalised file path.

    Returns:
        Tuple of (is_scratch: bool, reason: str).
    """
    normalised = normalise_path(path)

    # Check whitelist first
    for whitelist_pat in TEST_FILE_WHITELIST:
        if whitelist_pat.match(normalised):
            return False, ""

    # Check forbidden scratch patterns
    for name, pattern in SCRATCH_PATTERNS:
        if pattern.match(normalised):
            return True, f"Matches forbidden pattern: {name} ('{pattern.pattern}')"

    return False, ""


def get_git_files(mode: str) -> list[str]:
    """Retrieve list of files to check from git.

    Args:
        mode: Either 'staged' or 'all'.

    Returns:
        List of relative file paths.
    """
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if mode == "staged" else ["git", "ls-files"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def run_checks(files: Sequence[str]) -> int:
    """Run scratch and credential checks on the given list of files.

    Args:
        files: Sequence of file path strings.

    Returns:
        int: 0 if all clean, 1 if violations found.
    """
    total_violations = 0

    for raw_path in files:
        path = normalise_path(raw_path)
        if not path:
            continue

        # 1. Scratch file check
        scratch, reason = is_scratch_file(path)
        if scratch:
            print(f"❌ [SCRATCH FILE] {path}: {reason}", file=sys.stderr)
            total_violations += 1
            continue

        # 2. Credential content check
        if os.path.exists(path):
            cred_violations = check_file_credentials(path)
            for v in cred_violations:
                print(f"❌ [CREDENTIAL] {path}: {v}", file=sys.stderr)
                total_violations += 1

        # 3. Australian English orthography check
        if os.path.exists(path):
            spelling_violations = check_spelling_orthography(path)
            for v in spelling_violations:
                print(f"❌ [SPELLING EN-AU] {path}: {v}", file=sys.stderr)
                total_violations += 1

    if total_violations > 0:
        print(
            f"\nPre-commit check failed with {total_violations} violation(s). "
            "Please remove credentials and scratch files before committing.",
            file=sys.stderr,
        )
        return 1

    print(f"✅ Pre-commit check passed for {len(files)} file(s).")
    return 0


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Holon Pre-Commit Security Guard and Scratch File Detector.")
    parser.add_argument(
        "files",
        nargs="*",
        help="Explicit file paths to scan.",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        default=True,
        help="Check only git staged files (default).",
    )
    parser.add_argument(
        "--all",
        "--all-files",
        dest="all",
        action="store_true",
        help="Check all tracked files in git repository.",
    )

    args = parser.parse_args()

    staged_mode = not args.files and not args.all
    target_files: list[str] = []
    if args.files:
        target_files = args.files
    elif args.all:
        target_files = get_git_files("all")
    else:
        target_files = get_git_files("staged")

    exit_code = run_checks(target_files)
    if staged_mode:
        exit_code = max(exit_code, check_self_explaining(["--staged"]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
