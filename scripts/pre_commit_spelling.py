import os
import re

try:
    from scripts.pre_commit_credentials import normalise_path
except ImportError:
    from pre_commit_credentials import normalise_path

US_SPELLING_VARIANTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(initializ(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "initialis..."),
    (re.compile(r"\b(behavior(?:s|al)?)\b", re.IGNORECASE), "behaviour..."),
    (re.compile(r"\b(optimiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "optimis..."),
    (re.compile(r"\b(serializ(?:e|es|ed|ing|ation|ations|er|ers)?)\b", re.IGNORECASE), "serialis..."),
    (re.compile(r"\b(prioritiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "prioritis..."),
    (re.compile(r"\b(centraliz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "centralis..."),
    (re.compile(r"\b(standardiz(?:e|es|ed|ing|ation|ations)?)\b", re.IGNORECASE), "standardis..."),
]

SPELLING_EXEMPT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|/)(?:LICENSE|LICENCE|NOTICE|COPYING)(?:[._-].*)?$", re.IGNORECASE),
    re.compile(r".*\.lock$"),
    re.compile(r".*package-lock\.json$"),
    re.compile(r"^tests/.*\.py$"),
    re.compile(r"^apps/sandbox-executor/tests/.*\.py$"),
    re.compile(r"^apps/holon-coherence/tests/.*\.py$"),
    re.compile(r"^plans/.*"),
    re.compile(r"^docs/.*"),
    re.compile(r"^holon-knowledge/.*"),
    re.compile(r"^\.holon/.*"),
    re.compile(r"^scripts/pre_commit_check\.py$"),
    re.compile(r"^scripts/pre_commit_spelling\.py$"),
]


def check_spelling_orthography(file_path: str) -> list[str]:
    violations: list[str] = []
    norm = normalise_path(file_path)

    if any(pat.match(norm) for pat in SPELLING_EXEMPT_PATTERNS):
        return violations

    if not norm.endswith((".py", ".sh", ".md")):
        return violations

    if not os.path.isfile(file_path):
        return violations

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
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
