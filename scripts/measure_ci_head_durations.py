#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.ci_bookkeeping_gate import is_bookkeeping_path
    from scripts.ci_durations_collect import job_key, runner_os, seconds_between
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.ci_bookkeeping_gate import is_bookkeeping_path
    from scripts.ci_durations_collect import job_key, runner_os, seconds_between

GhRunner = Callable[[Sequence[str]], Any]

DEFAULT_PRS = "360-366"
BOOKKEEPING_SUBJECT_RE = re.compile(
    r"^(?:(?:chore|docs|ci)\()?(?:ledger|calibration|review|plan|bookkeeping)\b", re.IGNORECASE
)
ITERATION_RE = re.compile(r"\[Review Loop\]\s+Starting review iteration\s+(\d+)/")
CI_WAIT_DONE_RE = re.compile(r"\[Review Loop\]\s+CI wait\s+(settled|stopped on a failure)\s+after\s+(\d+(?:\.\d+)?)s")
CI_WAIT_BUDGET_RE = re.compile(r"\[Review Loop\]\s+CI wait budget of\s+(\d+(?:\.\d+)?)s\s+spent")
RESOLVER_HEAD_RE = re.compile(r"\[Review Loop\]\s+Resolver pushed new head '([0-9a-f]{7,40})'\s+at iteration\s+(\d+)")
PR_REF_RE = re.compile(r"(?:PR\s*#|pull/|pr[-_]|target\s+')(\d+)\b", re.IGNORECASE)
FLOW_LOG_SUFFIXES = frozenset({".log", ".txt", ".jsonl"})


@dataclass(frozen=True)
class HeadCommit:
    pr_number: int
    head_sha: str
    short_sha: str
    head_kind: str
    subject: str
    committed_at: str
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class HeadJobTiming:
    pr_number: int
    head_sha: str
    head_kind: str
    workflow: str
    job: str
    job_key: str
    runner_os: str
    run_id: int
    run_attempt: int
    started_at: str
    completed_at: str
    duration_seconds: float
    conclusion: str


@dataclass(frozen=True)
class ReviewerWait:
    pr_number: int | None
    iteration: int
    head_sha: str
    waited_seconds: float
    state: str


@dataclass(frozen=True)
class DurationStats:
    count: int
    median_seconds: float
    max_seconds: float


def run_gh_json(args: Sequence[str]) -> Any:
    text = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def resolve_repo(repo: str | None, runner: GhRunner) -> str:
    if repo:
        return repo
    result = runner(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return str(result["nameWithOwner"] if isinstance(result, dict) else result).strip()


def parse_pr_spec(spec: str) -> list[int]:
    if not spec.strip():
        raise ValueError("PR specification must not be empty")
    numbers: set[int] = set()
    for token in (part.strip() for part in spec.split(",")):
        parts = token.split("-")
        if len(parts) == 2 and all(p.isdigit() for p in parts) and 0 < int(parts[0]) <= int(parts[1]):
            numbers.update(range(int(parts[0]), int(parts[1]) + 1))
        elif len(parts) == 1 and token.isdigit() and int(token) > 0:
            numbers.add(int(token))
        else:
            raise ValueError(f"Invalid PR token {token!r}")
    return sorted(numbers)


def flatten_paged_items(payload: Any, key: str | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get(key) if key else [payload]
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if not isinstance(payload, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in payload:
        if isinstance(entry, list):
            result.extend(i for i in entry if isinstance(i, dict))
        elif isinstance(entry, dict) and key and isinstance(entry.get(key), list):
            result.extend(i for i in entry[key] if isinstance(i, dict))
        elif isinstance(entry, dict) and not key:
            result.append(entry)
    return result


def classify_head_commit(changed_files: Sequence[str], subject: str) -> str:
    if changed_files:
        return "bookkeeping" if all(is_bookkeeping_path(p) for p in changed_files) else "code"
    return "bookkeeping" if BOOKKEEPING_SUBJECT_RE.search(subject.strip()) else "code"


def fetch_pr_commits(repo: str, pr_number: int, runner: GhRunner) -> list[HeadCommit]:
    payload = runner(["api", "--paginate", "--slurp", f"repos/{repo}/pulls/{pr_number}/commits?per_page=100"])
    heads: list[HeadCommit] = []
    for commit in flatten_paged_items(payload):
        sha = str(commit.get("sha") or "")
        if not sha:
            continue
        info = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        subject = (str(info.get("message") or "").splitlines() or [""])[0]
        committer = info.get("committer") or info.get("author") or {}
        committed_at = str(committer.get("date") or "") if isinstance(committer, dict) else ""
        detail = runner(["api", f"repos/{repo}/commits/{sha}"])
        files = detail.get("files") if isinstance(detail, dict) else []
        changed = tuple(
            e[f] for e in (files or []) if isinstance(e, dict) for f in ("filename", "previous_filename") if e.get(f)
        )
        kind = classify_head_commit(changed, subject)
        heads.append(HeadCommit(pr_number, sha, sha[:8], kind, subject, committed_at, changed))
    return heads


def normalise_head_job(head: HeadCommit, run: dict[str, Any], job: dict[str, Any]) -> HeadJobTiming | None:
    started, completed, conclusion = job.get("started_at"), job.get("completed_at"), job.get("conclusion")
    if conclusion == "skipped" or not started or not completed or not conclusion:
        return None
    name = str(job.get("name") or "")
    attempt = int(job.get("run_attempt") or run.get("run_attempt") or 1)
    dur = seconds_between(str(started), str(completed))
    return HeadJobTiming(
        head.pr_number,
        head.head_sha,
        head.head_kind,
        str(run.get("name") or ""),
        name,
        job_key(name),
        runner_os(job),
        int(run["id"]),
        attempt,
        str(started),
        str(completed),
        dur,
        str(conclusion),
    )


def fetch_head_job_timings(repo: str, head: HeadCommit, runner: GhRunner) -> list[HeadJobTiming]:
    runs_url = f"repos/{repo}/actions/runs?head_sha={head.head_sha}&per_page=100"
    timings: list[HeadJobTiming] = []
    for run in flatten_paged_items(runner(["api", "--paginate", "--slurp", runs_url]), key="workflow_runs"):
        if run.get("status") != "completed":
            continue
        jobs_url = f"repos/{repo}/actions/runs/{int(run['id'])}/jobs?filter=all&per_page=100"
        for job in flatten_paged_items(runner(["api", "--paginate", "--slurp", jobs_url]), key="jobs"):
            if (timing := normalise_head_job(head, run, job)) is not None:
                timings.append(timing)
    return timings


def collect_pr_head_timings(
    repo: str, pr_numbers: Sequence[int], runner: GhRunner
) -> tuple[list[HeadCommit], list[HeadJobTiming]]:
    heads = [head for pr in pr_numbers for head in fetch_pr_commits(repo, pr, runner)]
    timings = [timing for head in heads for timing in fetch_head_job_timings(repo, head, runner)]
    return heads, timings


def _parse_jsonl_wait(line: str, fallback_pr: int | None) -> ReviewerWait | None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    cw = rec.get("ci_wait") if isinstance(rec, dict) else None
    if not isinstance(cw, dict) or cw.get("waited_seconds") is None:
        return None
    it = int(rec.get("review_iteration") or rec.get("iteration") or 1)
    pr = rec["pr_number"] if isinstance(rec.get("pr_number"), int) else fallback_pr
    sha, st = str(rec.get("head_sha") or ""), str(cw.get("state") or "recorded")
    return ReviewerWait(pr, it, sha, float(cw["waited_seconds"]), st)


def _parse_flow_log_file(path: Path, default_pr: int | None) -> list[ReviewerWait]:
    file_pr = int(m.group(1)) if (m := PR_REF_RE.search(path.name)) else default_pr
    cur_pr, cur_iter, cur_head = file_pr, 1, ""
    waits: list[ReviewerWait] = []
    for line in (raw.strip() for raw in path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line:
            continue
        if line.startswith("{"):
            if parsed := _parse_jsonl_wait(line, file_pr):
                waits.append(parsed)
            continue
        if (pr_match := PR_REF_RE.search(line)) and (new_pr := int(pr_match.group(1))) != cur_pr:
            cur_pr, cur_iter, cur_head = new_pr, 1, ""
        if m := ITERATION_RE.search(line):
            cur_iter = int(m.group(1))
        elif m := RESOLVER_HEAD_RE.search(line):
            cur_head, cur_iter = m.group(1), int(m.group(2))
        elif m := CI_WAIT_DONE_RE.search(line):
            state = "settled" if m.group(1) == "settled" else "failure"
            waits.append(ReviewerWait(cur_pr, cur_iter, cur_head, float(m.group(2)), state))
        elif m := CI_WAIT_BUDGET_RE.search(line):
            waits.append(ReviewerWait(cur_pr, cur_iter, cur_head, float(m.group(1)), "budget_spent"))
    return waits


def load_reviewer_waits(paths: Sequence[Path], default_pr: int | None = None) -> list[ReviewerWait]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in FLOW_LOG_SUFFIXES))
    return [wait for file_path in files for wait in _parse_flow_log_file(file_path, default_pr)]


def summarize_durations(values: Sequence[float]) -> DurationStats:
    if not values:
        return DurationStats(0, 0.0, 0.0)
    return DurationStats(len(values), float(statistics.median(values)), max(values))


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    w = [max([len(headers[i]), *(len(r[i]) for r in rows)]) for i in range(len(headers))]
    all_rows = [headers, ["-" * n for n in w], *rows]
    return ["| " + " | ".join(r[i].ljust(w[i]) for i in range(len(headers))) + " |" for r in all_rows]


def _match_wait(head: HeadCommit, index: int, waits: Sequence[ReviewerWait]) -> ReviewerWait | None:
    scoped = [w for w in waits if w.pr_number in (None, head.pr_number)]
    by_sha = next((w for w in scoped if w.head_sha and head.head_sha.startswith(w.head_sha)), None)
    return by_sha or next((w for w in scoped if not w.head_sha and w.iteration == index), None)


def _head_rows(
    heads: Sequence[HeadCommit], timings: Sequence[HeadJobTiming], waits: Sequence[ReviewerWait]
) -> list[list[str]]:
    by_head: dict[tuple[int, str], list[HeadJobTiming]] = defaultdict(list)
    for t in timings:
        by_head[(t.pr_number, t.head_sha)].append(t)
    rows: list[list[str]] = []
    per_pr_index: dict[int, int] = defaultdict(int)
    for head in heads:
        per_pr_index[head.pr_number] += 1
        ht = by_head[(head.pr_number, head.head_sha)]
        s = summarize_durations([t.duration_seconds for t in ht])
        longest = max(ht, key=lambda item: item.duration_seconds).job if ht else "n/a"
        w = _match_wait(head, per_pr_index[head.pr_number], waits)
        wait_s = f"{w.waited_seconds:.1f}s (iter {w.iteration}, {w.state})" if w else "n/a"
        med, mx = (f"{s.median_seconds:.1f}s", f"{s.max_seconds:.1f}s") if s.count else ("n/a", "n/a")
        rows.append([f"#{head.pr_number}", head.short_sha, head.head_kind, str(s.count), med, mx, longest, wait_s])
    return rows


def render_summary(heads: Sequence[HeadCommit], timings: Sequence[HeadJobTiming], waits: Sequence[ReviewerWait]) -> str:
    overall = []
    for label, kind in (("all", None), ("code", "code"), ("bookkeeping", "bookkeeping")):
        hc = sum(1 for h in heads if kind is None or h.head_kind == kind)
        s = summarize_durations([t.duration_seconds for t in timings if kind is None or t.head_kind == kind])
        med, mx = (f"{s.median_seconds:.1f}s", f"{s.max_seconds:.1f}s") if s.count else ("n/a", "n/a")
        overall.append([label, str(hc), str(s.count), med, mx])
    by_job: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for t in timings:
        by_job[(t.job_key, t.runner_os, t.head_kind)].append(t.duration_seconds)
    job_rows = [
        [name, os_l, kind, str(s.count), f"{s.median_seconds:.1f}s", f"{s.max_seconds:.1f}s"]
        for (name, os_l, kind), vals in sorted(by_job.items())
        if (s := summarize_durations(vals))
    ]
    head_cols = ["PR", "Head", "Kind", "Jobs", "Median", "Longest", "Longest Job", "Reviewer Wait"]
    sections: list[tuple[str, Sequence[str], Sequence[Sequence[str]]]] = [
        ("## Overall Summary", ["Kind", "Heads", "Jobs", "Median", "Longest"], overall),
        ("## Per-Job Durations", ["Job", "Runner OS", "Kind", "Count", "Median", "Longest"], job_rows),
        ("## Per-Head Durations", head_cols, _head_rows(heads, timings, waits)),
    ]
    if waits:
        w_rows = []
        for w in waits:
            pr_s = f"#{w.pr_number}" if w.pr_number else "n/a"
            w_rows.append([pr_s, str(w.iteration), w.head_sha[:8] or "n/a", w.state, f"{w.waited_seconds:.1f}s"])
        sections.append(("## Reviewer Iteration Waits", ["PR", "Iteration", "Head", "State", "Waited"], w_rows))
    return "\n\n".join("\n".join([title, *_render_table(headers, rows)]) for title, headers, rows in sections)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure per-head, per-job CI durations across pull requests.")
    parser.add_argument("--prs", default=DEFAULT_PRS, help="PR numbers or ranges, e.g. 360-366 or 360,362-364.")
    parser.add_argument("--repo", default=None, help="GitHub owner/repo; defaults to gh repo view.")
    parser.add_argument("--flow-log", action="append", default=[], help="Optional flow log file or directory.")
    return parser


def main(argv: Sequence[str] | None = None, runner: GhRunner = run_gh_json) -> int:
    try:
        args = build_parser().parse_args(argv)
        pr_numbers = parse_pr_spec(args.prs)
    except (SystemExit, ValueError) as error:
        if isinstance(error, SystemExit):
            return int(error.code) if isinstance(error.code, int) else 2
        print(f"error: {error}", file=sys.stderr)
        return 2
    repo = resolve_repo(args.repo, runner)
    heads, timings = collect_pr_head_timings(repo, pr_numbers, runner)
    default_pr = pr_numbers[0] if len(pr_numbers) == 1 else None
    pr_set = set(pr_numbers)
    waits = load_reviewer_waits([Path(p) for p in args.flow_log], default_pr)
    waits = [w for w in waits if w.pr_number is None or w.pr_number in pr_set]
    print(render_summary(heads, timings, waits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
