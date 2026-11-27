#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from scripts.self_explaining_comments import ENCODING_DECLARATION, MACHINE_DIRECTIVE, comment_body
except ImportError:
    from self_explaining_comments import ENCODING_DECLARATION, MACHINE_DIRECTIVE, comment_body

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_ROOTS = frozenset({"plans", "executions", "holon-knowledge"})
CODE_SUFFIXES = frozenset({".py", ".sh", ".bash", ".yml", ".yaml", ".toml", ".mk"})
CODE_NAMES = frozenset({"Makefile", "GNUmakefile", "Dockerfile"})
REASON_INDICATORS = ("because", "so that", "otherwise", "must", "avoid", "never", "race", "ensure")
_REASON_ALTERNATION = "|".join(r"\s+".join(re.escape(part) for part in term.split()) for term in REASON_INDICATORS)
REASON_INDICATOR_PATTERN = re.compile(rf"\b(?:{_REASON_ALTERNATION})\b|\bwhy:", re.IGNORECASE)
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
INTENT_CREATED_PATTERN = re.compile(r"^\*\s+intent:\s+created\s+([^\s/]+)", re.MULTILINE)
PR_NUMBER_PATTERN = re.compile(r"\(#(\d+)\)\s*$")


@dataclass(frozen=True)
class SelfDocSlice:
    sha: str
    pr_number: int | None
    intent_id: str | None
    subject: str


@dataclass(frozen=True)
class RemovedComment:
    line: int
    text: str


@dataclass(frozen=True)
class UnmatchedReason:
    file_path: str
    line: int
    text: str


@dataclass(frozen=True)
class SliceReport:
    slice_info: SelfDocSlice
    unmatched: list[UnmatchedReason]


@dataclass
class HunkState:
    old_line: int
    removed_reasons: list[RemovedComment]
    added_why_count: int = 0


@dataclass
class FileDiffState:
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[HunkState] | None = None

    def active_path(self) -> str | None:
        if self.new_path and self.new_path != "/dev/null":
            return self.new_path
        if self.old_path and self.old_path != "/dev/null":
            return self.old_path
        return None


def is_code_path(path: str) -> bool:
    pure_path = PurePosixPath(path)
    if not pure_path.parts or pure_path.parts[0] in EXCLUDED_ROOTS:
        return False
    if pure_path.name in CODE_NAMES or pure_path.name.startswith("Dockerfile."):
        return True
    return pure_path.suffix.lower() in CODE_SUFFIXES


def find_hash_comment_start(line: str) -> int | None:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote is not None:
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "#":
            return index
    return None


def extract_hash_comment(line: str) -> str | None:
    start = find_hash_comment_start(line)
    if start is None:
        return None
    raw_comment = line[start:].strip()
    if raw_comment.startswith("#!") or ENCODING_DECLARATION.search(raw_comment):
        return None
    body = comment_body(raw_comment)
    if not body or MACHINE_DIRECTIVE.match(body) is not None:
        return None
    return raw_comment


def is_why_comment(comment: str) -> bool:
    return comment_body(comment).lower().startswith("why:")


def has_reason_indicator(comment: str) -> bool:
    return REASON_INDICATOR_PATTERN.search(comment_body(comment)) is not None


def strip_diff_prefix(raw_path: str, prefix: str) -> str:
    cleaned = raw_path.strip()
    if cleaned.startswith(prefix):
        return cleaned[len(prefix) :]
    return cleaned


def resolve_file_unmatched(file_state: FileDiffState) -> list[UnmatchedReason]:
    path = file_state.active_path()
    if path is None or not is_code_path(path) or not file_state.hunks:
        return []
    pending_reasons: list[RemovedComment] = []
    surplus_why_count = 0
    for hunk in file_state.hunks:
        matched_in_hunk = min(len(hunk.removed_reasons), hunk.added_why_count)
        pending_reasons.extend(hunk.removed_reasons[matched_in_hunk:])
        surplus_why_count += hunk.added_why_count - matched_in_hunk
    remaining = pending_reasons[surplus_why_count:]
    return [UnmatchedReason(path, item.line, item.text) for item in remaining]


def process_hunk_line(hunk: HunkState, line: str) -> None:
    if line.startswith("\\"):
        return
    if line.startswith("-"):
        comment = extract_hash_comment(line[1:])
        if comment is not None and has_reason_indicator(comment):
            hunk.removed_reasons.append(RemovedComment(hunk.old_line, comment))
        hunk.old_line += 1
        return
    if line.startswith("+"):
        comment = extract_hash_comment(line[1:])
        if comment is not None and is_why_comment(comment):
            hunk.added_why_count += 1
        return
    hunk.old_line += 1


def find_unmatched_reasons_in_diff(diff_text: str) -> list[UnmatchedReason]:
    unmatched: list[UnmatchedReason] = []
    current_file: FileDiffState | None = None
    current_hunk: HunkState | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_file is not None:
                unmatched.extend(resolve_file_unmatched(current_file))
            current_file = FileDiffState(hunks=[])
            current_hunk = None
            continue
        if current_file is None:
            continue
        if current_hunk is None and line.startswith("--- "):
            current_file.old_path = strip_diff_prefix(line[4:], "a/")
            continue
        if current_hunk is None and line.startswith("+++ "):
            current_file.new_path = strip_diff_prefix(line[4:], "b/")
            continue
        hunk_match = HUNK_HEADER.match(line)
        if hunk_match is not None:
            current_hunk = HunkState(old_line=int(hunk_match.group(1)), removed_reasons=[])
            assert current_file.hunks is not None
            current_file.hunks.append(current_hunk)
            continue
        if current_hunk is not None:
            process_hunk_line(current_hunk, line)
    if current_file is not None:
        unmatched.extend(resolve_file_unmatched(current_file))
    return unmatched


def run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def parse_self_doc_slice(sha: str, subject: str, body: str) -> SelfDocSlice | None:
    intent_match = INTENT_CREATED_PATTERN.search(body)
    if intent_match is not None:
        intent_id = intent_match.group(1)
        if "self-doc" not in intent_id.lower():
            return None
    else:
        intent_id = None
        if "self-doc" not in subject.lower():
            return None
    pr_match = PR_NUMBER_PATTERN.search(subject)
    pr_number = int(pr_match.group(1)) if pr_match is not None else None
    return SelfDocSlice(sha=sha, pr_number=pr_number, intent_id=intent_id, subject=subject)


def list_merged_self_doc_slices(repo_root: Path, ref: str = "main") -> list[SelfDocSlice]:
    raw_log = run_git(repo_root, "log", ref, "--first-parent", "--format=%H%x1f%s%x1f%b%x1e")
    slices: list[SelfDocSlice] = []
    for record in raw_log.split("\x1e"):
        stripped = record.strip()
        if not stripped:
            continue
        parts = stripped.split("\x1f", 2)
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1].strip()
        body = parts[2] if len(parts) > 2 else ""
        parsed = parse_self_doc_slice(sha, subject, body)
        if parsed is not None:
            slices.append(parsed)
    return slices


def scan_slice_diff(repo_root: Path, slice_info: SelfDocSlice) -> SliceReport:
    diff_text = run_git(
        repo_root,
        "-c",
        "core.quotePath=false",
        "show",
        "-m",
        "--first-parent",
        "--format=",
        "--patch",
        "-U3",
        "--no-color",
        "--no-ext-diff",
        slice_info.sha,
    )
    return SliceReport(slice_info=slice_info, unmatched=find_unmatched_reasons_in_diff(diff_text))


def scan_self_doc_history(repo_root: Path, ref: str = "main") -> list[SliceReport]:
    return [scan_slice_diff(repo_root, slice_info) for slice_info in list_merged_self_doc_slices(repo_root, ref)]


def format_slice_header(report: SliceReport) -> str:
    info = report.slice_info
    pr_label = f"PR #{info.pr_number}" if info.pr_number is not None else "commit"
    intent_label = f" {info.intent_id}" if info.intent_id else ""
    return f"{pr_label} ({info.sha[:9]}){intent_label}: {len(report.unmatched)} unmatched"


def format_text_report(reports: list[SliceReport]) -> str:
    lines: list[str] = []
    total_unmatched = 0
    for report in reports:
        lines.append(format_slice_header(report))
        for item in report.unmatched:
            lines.append(f"  {item.file_path}:{item.line}: {item.text}")
        total_unmatched += len(report.unmatched)
    lines.append(f"Scanned {len(reports)} self-doc slice(s); {total_unmatched} unmatched removed reason(s).")
    return "\n".join(lines)


def format_json_report(ref: str, reports: list[SliceReport]) -> str:
    payload = {
        "ref": ref,
        "slices_scanned": len(reports),
        "unmatched_count": sum(len(report.unmatched) for report in reports),
        "slices": [
            {
                "sha": report.slice_info.sha,
                "pr_number": report.slice_info.pr_number,
                "intent_id": report.slice_info.intent_id,
                "subject": report.slice_info.subject,
                "unmatched": [
                    {"file": item.file_path, "line": item.line, "text": item.text} for item in report.unmatched
                ],
            }
            for report in reports
        ],
    }
    return json.dumps(payload, indent=2)


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure removed reason comments lacking '# why:' restorations across merged self-doc PRs."
    )
    parser.add_argument("--repo", type=Path, default=REPOSITORY_ROOT, help="Repository root.")
    parser.add_argument("--ref", default="main", help="Git ref to scan (default: main).")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    reports = scan_self_doc_history(arguments.repo, ref=arguments.ref)
    if arguments.format == "json":
        print(format_json_report(arguments.ref, reports))
    else:
        print(format_text_report(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
