import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

STRICT_ENV = "HOLON_ATTRIBUTION_STRICT"
_TRUTHY = ("1", "true", "yes", "on")
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
ATTRIBUTION_PATTERN = re.compile(
    r"^\s*(?:co-authored-by:.*noreply@anthropic\.com|.*generated with \[claude code\]).*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class AttributionFinding:
    sha: str
    trailer: str


def find_attribution_trailers(base_ref: str, git_runner: Callable[[list[str]], str]) -> list[AttributionFinding]:
    fmt = f"{_RECORD_SEP}%h{_FIELD_SEP}%B"
    try:
        out = git_runner(["log", "--no-merges", f"--format={fmt}", f"{base_ref}..HEAD"])
    # why: subprocess runners raise SubprocessError/OSError while _entrypoint_git_runner raises RuntimeError
    except (subprocess.SubprocessError, OSError, RuntimeError):
        return []
    findings: list[AttributionFinding] = []
    for record in out.split(_RECORD_SEP):
        sha, _, body = record.partition(_FIELD_SEP)
        findings.extend(AttributionFinding(sha.strip(), m.group(0).strip()) for m in ATTRIBUTION_PATTERN.finditer(body))
    return findings


def format_attribution_report(findings: list[AttributionFinding]) -> str:
    if not findings:
        return ""
    lines = [f"Attribution trailers found in {len(findings)} line(s) of the execution branch's own commits:"]
    lines.extend(f"  {f.sha}: {f.trailer}" for f in findings)
    lines.append("History is not rewritten; the maintainer's rule is that no commit carries an attribution trailer.")
    return "\n".join(lines)


def attribution_is_strict() -> bool:
    return os.getenv(STRICT_ENV, "").lower() in _TRUTHY


def check_attribution(base_ref: str | None, git_runner: Callable[[list[str]], str]) -> tuple[bool, str]:
    if not base_ref:
        return True, ""
    report = format_attribution_report(find_attribution_trailers(base_ref, git_runner))
    return not (report and attribution_is_strict()), report


def attribution_allows_publish(base_ref: str | None, git_runner: Callable[[list[str]], str]) -> bool:
    from sandbox_executor.redaction import redact_text

    ok, report = check_attribution(base_ref, git_runner)
    if report:
        print(redact_text(report), file=sys.stderr)
    return ok
