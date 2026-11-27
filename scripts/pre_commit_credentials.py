import os
import re
from collections.abc import Sequence

CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9_\-]{20,}")),
    ("Anthropic API Key", re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}")),
    ("Google AI Studio / Gemini Key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub Personal Access Token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[0-9a-zA-Z]{20,}")),
    ("GitHub Fine-Grained PAT", re.compile(r"github_pat_[0-9a-zA-Z_]{22,}")),
    # why: masked forms (`*******`, `***REDACTED***`) and `${GITHUB_TOKEN}` references fall outside the class
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

GITHUB_CREDENTIAL_PATTERN_NAMES: tuple[str, ...] = (
    "GitHub Personal Access Token",
    "GitHub Fine-Grained PAT",
    "URL-embedded GitHub credential",
)

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

TEST_FILE_WHITELIST: list[re.Pattern[str]] = [
    re.compile(r"^tests/test_.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/test_.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/.*\.py$"),
]


def normalise_path(path: str) -> str:
    clean = path.strip()
    clean = clean.removeprefix("./")
    clean = clean.replace("\\", "/")
    return clean


def check_file_credentials(file_path: str) -> list[str]:
    violations: list[str] = []
    norm = normalise_path(file_path)
    if any(pat.match(norm) for pat in TEST_FILE_WHITELIST) or norm.startswith("docs/"):
        return violations

    if not os.path.isfile(file_path):
        return violations

    try:
        if os.path.getsize(file_path) > 2 * 1024 * 1024:
            return violations
    except OSError:
        return violations

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                for name, pat in CREDENTIAL_PATTERNS:
                    match = pat.search(line)
                    if match:
                        matched_val = match.group(0)
                        if any(
                            fp.lower() in matched_val.lower() or fp.lower() in line.lower()
                            for fp in FALSE_POSITIVE_INDICATORS
                        ):
                            continue
                        masked = matched_val[:4] + "***" + matched_val[-4:] if len(matched_val) > 8 else "***"
                        violations.append(f"Potential {name} detected at line {line_no} (match: '{masked}')")
    except Exception as exc:
        violations.append(f"Unable to read file for scanning: {exc}")

    return violations


_GITHUB_CREDENTIAL_MARKERS: tuple[str, ...] = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "x-access-token:",
)


def find_github_credentials(files: Sequence[str]) -> list[tuple[str, int, str]]:
    # why: the matched value is never returned, so a caller cannot print the credential it found
    patterns = [(name, pat) for name, pat in CREDENTIAL_PATTERNS if name in GITHUB_CREDENTIAL_PATTERN_NAMES]
    findings: list[tuple[str, int, str]] = []
    for file_path in files:
        try:
            if not os.path.isfile(file_path) or os.path.getsize(file_path) > 2 * 1024 * 1024:
                continue
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if not any(marker in content for marker in _GITHUB_CREDENTIAL_MARKERS):
                continue
            for line_no, line in enumerate(content.splitlines(), start=1):
                findings.extend((file_path, line_no, name) for name, pat in patterns if pat.search(line))
        except OSError:
            continue
    return findings
