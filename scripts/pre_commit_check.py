#!/usr/bin/env python3
"""Pre-commit security guard and scratch file detector (Bean 0110).

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

# Regex patterns for detecting API keys, tokens, and private keys
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9_\-]{20,}")),
    ("Anthropic API Key", re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}")),
    ("Google AI Studio / Gemini Key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub Personal Access Token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{20,}")),
    ("GitHub Fine-Grained PAT", re.compile(r"github_pat_[0-9a-zA-Z_]{22,}")),
    # A token embedded in a clone URL's userinfo. Masked forms (`*******`, `***REDACTED***`) and
    # `${GITHUB_TOKEN}` references fall outside the character class, so they never match.
    ("URL-embedded GitHub credential", re.compile(r"x-access-token:[A-Za-z0-9_\-]{8,}@")),
    ("Slack Token", re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,}")),
    ("Private Key Header", re.compile(r"-----BEGIN (?:[A-Z0-9_\s]+ )?PRIVATE KEY-----")),
    (
        "Generic Secret Assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|secret|password|passwd|auth[_-]?token)\s*[:=]\s*['\"]([0-9a-zA-Z_\-]{16,})['\"]"
        ),
    ),
]

# The GitHub credential shapes that `find_github_credentials` enforces across every tracked file.
GITHUB_CREDENTIAL_PATTERN_NAMES: tuple[str, ...] = (
    "GitHub Personal Access Token",
    "GitHub Fine-Grained PAT",
    "URL-embedded GitHub credential",
)

# Patterns representing non-production test / mock placeholders that should not trigger false positives
FALSE_POSITIVE_INDICATORS = [
    "REDACTED",
    "placeholder",
    "dummy",
    "example",
    "test_secret",
    "fake_key",
    "fake_token",
    "your_key_here",
    "my_secret_token",
    "mock_token",
    "env_token",
]

# Forbidden scratch file patterns (relative to repo root)
SCRATCH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Temporary directory file", re.compile(r"^tmp/.*")),
    ("Scratch directory file", re.compile(r"^scratch/.*")),
    ("Temporary test file", re.compile(r".*\.test_tmp\.py$")),
    ("Temporary file extension", re.compile(r".*\.(?:tmp|bak|orig|swp)$")),
]

# Whitelist patterns for files exempt from scratch rules
TEST_FILE_WHITELIST: list[re.Pattern[str]] = [
    re.compile(r"^tests/test_.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/test_.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/.*\.py$"),
]

# Australian English spelling enforcement patterns (Bean 0125)
# Maps American English variants to suggested Australian English equivalents
US_SPELLING_VARIANTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(initializ(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "initialis..."),
    (re.compile(r"\b(behavior(?:s|al)?)\b", re.IGNORECASE), "behaviour..."),
    (re.compile(r"\b(optimiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "optimis..."),
    (re.compile(r"\b(serializ(?:e|es|ed|ing|ation|ations|er|ers)?)\b", re.IGNORECASE), "serialis..."),
    (re.compile(r"\b(prioritiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "prioritis..."),
    (re.compile(r"\b(centraliz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "centralis..."),
    (re.compile(r"\b(standardiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "standardis..."),
]

# Patterns for files strictly exempt from spelling checks (e.g. upstream LICENSE, lockfiles)
SPELLING_EXEMPT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|/)(?:LICENSE|LICENCE|NOTICE|COPYING)(?:[._-].*)?$", re.IGNORECASE),
    re.compile(r".*\.lock$"),
    re.compile(r".*package-lock\.json$"),
    re.compile(r"^tests/.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/.*\.py$"),
    re.compile(r"^apps/holon-coherence/tests/.*\.py$"),
    re.compile(r"^plans/.*"),
    re.compile(r"^docs/.*"),
    re.compile(r"^todo/.*"),
    re.compile(r"^holon-knowledge/.*"),
    re.compile(r"^\.holon/.*"),
    re.compile(r"^scripts/pre_commit_check\.py$"),
]


def normalise_path(path: str) -> str:
    """Normalise path preserving leading dots in directory names.

    Uses `.removeprefix('./')` instead of `.lstrip('./')` to avoid stripping
    leading dots from hidden directories like `.index/` or `.agents/`.

    Args:
        path: File path string.

    Returns:
        Normalised relative path.
    """
    clean = path.strip()
    clean = clean.removeprefix("./")
    clean = clean.replace("\\", "/")
    return clean


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


def check_file_credentials(file_path: str) -> list[str]:
    """Scan file content for embedded credentials and API keys.

    Args:
        file_path: Path to target file on disk.

    Returns:
        List of detected violation descriptions.
    """
    violations: list[str] = []
    norm = normalise_path(file_path)
    if any(pat.match(norm) for pat in TEST_FILE_WHITELIST) or norm.startswith("docs/"):
        return violations

    if not os.path.isfile(file_path):
        return violations

    # Skip files larger than 2MB or binary files
    try:
        if os.path.getsize(file_path) > 2 * 1024 * 1024:
            return violations
    except OSError:
        return violations

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                # Ignore comment-only lines or test assertions mentioning secret patterns
                for name, pat in CREDENTIAL_PATTERNS:
                    match = pat.search(line)
                    if match:
                        matched_val = match.group(0)
                        # Check false-positive indicators
                        if any(
                            fp.lower() in matched_val.lower() or fp.lower() in line.lower()
                            for fp in FALSE_POSITIVE_INDICATORS
                        ):
                            continue
                        # Mask matched value for secure logging
                        masked = matched_val[:4] + "***" + matched_val[-4:] if len(matched_val) > 8 else "***"
                        violations.append(f"Potential {name} detected at line {line_no} (match: '{masked}')")
    except Exception as exc:
        violations.append(f"Unable to read file for scanning: {exc}")

    return violations


def find_github_credentials(files: Sequence[str]) -> list[tuple[str, int, str]]:
    """Scan files for GitHub token shapes, with no path or placeholder exemptions.

    Unlike `check_file_credentials`, this applies to tests, docs and the append-only ledger too: a
    GitHub token or a tokenised clone URL has no legitimate reason to be in any tracked file, and
    test fixtures build fake tokens at runtime instead. Missing, binary-unreadable and oversized
    (over 2 MB) files are skipped.

    Args:
        files: Paths of the files to scan.

    Returns:
        One `(path, line_number, pattern_name)` entry per match. The matched value is deliberately
        never returned, so a caller cannot print the credential it found.
    """
    patterns = [(name, pat) for name, pat in CREDENTIAL_PATTERNS if name in GITHUB_CREDENTIAL_PATTERN_NAMES]
    findings: list[tuple[str, int, str]] = []
    for file_path in files:
        try:
            if not os.path.isfile(file_path) or os.path.getsize(file_path) > 2 * 1024 * 1024:
                continue
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                for line_no, line in enumerate(f, start=1):
                    findings.extend((file_path, line_no, name) for name, pat in patterns if pat.search(line))
        except OSError:
            continue
    return findings


def check_spelling_orthography(file_path: str) -> list[str]:
    """Scan file content for non-conforming US spelling variants (Bean 0125).

    Strictly preserves all LICENSE files, license headers, and third-party vendor
    boilerplate word-for-word per the License Invariant.
    """
    violations: list[str] = []
    norm = normalise_path(file_path)

    # Check file exemption
    if any(pat.match(norm) for pat in SPELLING_EXEMPT_PATTERNS):
        return violations

    # Only scan code, markdown, and shell files
    if not norm.endswith((".py", ".sh", ".md")):
        return violations

    if not os.path.isfile(file_path):
        return violations

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                # Ignore URLs, import statements, or license header boilerplate
                if "http://" in line or "https://" in line or "Apache License" in line:
                    continue
                for pat, suggestion in US_SPELLING_VARIANTS:
                    match = pat.search(line)
                    if match:
                        word = match.group(0)
                        violations.append(
                            f"US spelling variant '{word}' at line {line_no} "
                            f"(suggested Australian English: '{suggestion}')"
                        )
    except Exception as exc:
        violations.append(f"Unable to read file for spelling check: {exc}")

    return violations


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

        # 3. Australian English orthography check (Bean 0125)
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

    target_files: list[str] = []
    if args.files:
        target_files = args.files
    elif args.all:
        target_files = get_git_files("all")
    else:
        target_files = get_git_files("staged")

    sys.exit(run_checks(target_files))


if __name__ == "__main__":
    main()
