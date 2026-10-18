#!/usr/bin/env python3
"""Pre-commit security credential leakage and ephemeral scratch file detection (Bean 0110).

Scans git staged or tracked files for:
1. Secret credential tokens (Google AI API keys, GitHub PATs, OpenAI keys, Anthropic keys).
2. Ephemeral scratch and log files that violate repository hygiene policies.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import NamedTuple

# Ephemeral scratch file path blocklist patterns
SCRATCH_BLOCKLIST_PATTERNS = [
    re.compile(r"^tmp/.*", re.IGNORECASE),
    re.compile(r".*/tmp/.*", re.IGNORECASE),
    re.compile(r".*\.tmp$", re.IGNORECASE),
    re.compile(r".*\.log$", re.IGNORECASE),
    re.compile(r".*\.index/.*", re.IGNORECASE),
]

# Secret credential token regex patterns
SECRET_PATTERNS = {
    "Google AI API Key": re.compile(r"AIza[0-9A-Za-z\-_]{30,40}"),
    "GitHub PAT (Classic)": re.compile(r"ghp_[0-9A-Za-z]{30,50}"),
    "GitHub PAT (Fine-Grained)": re.compile(r"github_pat_[0-9A-Za-z_]{70,90}"),
    "GitHub PAT (General)": re.compile(r"github_pat_[0-9A-Za-z_]+"),
    "OpenAI API Key": re.compile(r"sk-[0-9A-Za-z\-_]{20,}"),
    "Anthropic API Key": re.compile(r"sk-ant-[0-9A-Za-z\-_]{20,}"),
}

# Whitelist patterns for repository self-scanning to prevent false positives on rule definitions and test fixtures
SCAN_WHITELIST_PATTERNS = [
    re.compile(r".*test_pre_commit_check\.py$"),
    re.compile(r".*scripts/pre_commit_check\.py$"),
    re.compile(r".*\.agents/AGENTS\.md$"),
    re.compile(r".*holon-config/.*"),
]


class SecretFinding(NamedTuple):
    """Represents an identified secret credential finding."""

    file_path: str
    line_number: int
    rule_name: str
    redacted_token: str


def redact_token(token: str) -> str:
    """Redact secret token preserving prefix and length indicator.

    Args:
        token: Raw matched secret substring.

    Returns:
        str: Redacted string representation.
    """
    if len(token) <= 8:
        return "[REDACTED]"
    prefix = token[:6]
    return f"{prefix}...[REDACTED:{len(token)}chars]"


def is_scratch_file(file_path: str) -> bool:
    """Check whether a file path matches disallowed ephemeral scratch patterns.

    Args:
        file_path: Relative or normalised file path.

    Returns:
        bool: True if file matches scratch patterns, False otherwise.
    """
    clean_path = file_path.replace("\\", "/").strip().lstrip("./")
    return any(pattern.match(clean_path) for pattern in SCRATCH_BLOCKLIST_PATTERNS)


def is_whitelisted_for_content_scan(file_path: str) -> bool:
    """Check whether a file is whitelisted from regex content scanning.

    Args:
        file_path: Relative or normalised file path.

    Returns:
        bool: True if whitelisted, False otherwise.
    """
    clean_path = file_path.replace("\\", "/").strip().lstrip("./")
    return any(pattern.match(clean_path) for pattern in SCAN_WHITELIST_PATTERNS)


def is_binary_file(full_path: str) -> bool:
    """Check whether a file contains binary content by inspecting leading bytes.

    Args:
        full_path: Absolute or filesystem path to file.

    Returns:
        bool: True if binary, False if text.
    """
    try:
        with open(full_path, "rb") as f:
            chunk = f.read(2048)
            return b"\x00" in chunk
    except OSError:
        return False


def scan_file_for_secrets(
    full_path: str,
    display_path: str | None = None,
    enforce_whitelist: bool = True,
) -> list[SecretFinding]:
    """Scan an individual text file for secret credential patterns.

    Args:
        full_path: Filesystem path to the target file.
        display_path: Optional display path for reporting (defaults to full_path).
        enforce_whitelist: Whether to skip whitelisted pattern/test files.

    Returns:
        list[SecretFinding]: Detected secret credential findings.
    """
    path_str = display_path or full_path
    if enforce_whitelist and is_whitelisted_for_content_scan(path_str):
        return []

    if is_binary_file(full_path):
        return []

    findings: list[SecretFinding] = []
    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            for line_idx, line in enumerate(f, start=1):
                for rule_name, pattern in SECRET_PATTERNS.items():
                    matches = pattern.finditer(line)
                    for m in matches:
                        raw = m.group(0)
                        findings.append(
                            SecretFinding(
                                file_path=path_str,
                                line_number=line_idx,
                                rule_name=rule_name,
                                redacted_token=redact_token(raw),
                            )
                        )
    except OSError:
        pass
    return findings


def get_git_files(repo_dir: str, mode: str = "staged") -> list[str]:
    """Retrieve git file paths to inspect based on mode.

    Args:
        repo_dir: Path to repository root.
        mode: Either 'staged' or 'all-files'.

    Returns:
        list[str]: Relative file paths.
    """
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    else:
        cmd = ["git", "ls-files"]

    try:
        proc = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def run_pre_commit_check(
    repo_dir: str = ".",
    staged: bool = True,
    all_files: bool = False,
    custom_files: list[str] | None = None,
    enforce_whitelist: bool = True,
) -> int:
    """Execute pre-commit security and hygiene check.

    Args:
        repo_dir: Path to git repository root.
        staged: Inspect git staged files.
        all_files: Inspect all tracked git files.
        custom_files: Optional explicit list of file paths to inspect.
        enforce_whitelist: Whether to apply whitelist exclusions to content scanning.

    Returns:
        int: 0 if all checks pass, 1 if scratch or secret violations are detected.
    """
    if custom_files is not None:
        target_files = custom_files
        scan_mode = "custom"
    elif all_files:
        target_files = get_git_files(repo_dir, mode="all-files")
        scan_mode = "all tracked files"
    else:
        target_files = get_git_files(repo_dir, mode="staged")
        scan_mode = "staged files"

    print(f"--> [Pre-Commit Check] Scanning {len(target_files)} {scan_mode} in '{repo_dir}'...")

    scratch_violations: list[str] = []
    secret_findings: list[SecretFinding] = []

    for rel_path in target_files:
        clean_path = rel_path.replace("\\", "/").strip().lstrip("./")
        # 1. Ephemeral scratch file check
        if is_scratch_file(clean_path):
            scratch_violations.append(clean_path)

        # 2. Secret credential pattern check
        full_path = os.path.join(repo_dir, rel_path) if not os.path.isabs(rel_path) else rel_path
        if os.path.exists(full_path):
            file_findings = scan_file_for_secrets(
                full_path=full_path,
                display_path=clean_path,
                enforce_whitelist=enforce_whitelist,
            )
            secret_findings.extend(file_findings)

    has_errors = False

    if scratch_violations:
        has_errors = True
        print("\n❌ [Hygiene Violation] Disallowed ephemeral scratch/log files detected:")
        for sv in scratch_violations:
            print(f"   - {sv}")
        print("   Blocked extensions/directories: tmp/*, *.tmp, *.log, .index/*")

    if secret_findings:
        has_errors = True
        print("\n❌ [Security Violation] Potential secret credentials detected:")
        for sf in secret_findings:
            print(f"   - {sf.file_path}:{sf.line_number} [{sf.rule_name}]: {sf.redacted_token}")
        print("   Please remove or redact credentials before committing.")

    if has_errors:
        print("\n❌ Pre-commit validation failed. Fix violations to proceed.\n")
        return 1

    print("✅ Pre-commit security & hygiene checks passed cleanly.\n")
    return 0


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Pre-commit secret credential and scratch file hygiene scanner (Bean 0110)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--staged",
        action="store_true",
        default=True,
        help="Scan git staged files (default).",
    )
    group.add_argument(
        "--all-files",
        action="store_true",
        help="Scan all tracked git repository files.",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Path to repository root (default: current directory).",
    )
    args = parser.parse_args()

    return run_pre_commit_check(
        repo_dir=args.repo_dir,
        staged=not args.all_files,
        all_files=args.all_files,
    )


if __name__ == "__main__":
    sys.exit(main())
