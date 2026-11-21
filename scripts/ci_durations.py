#!/usr/bin/env python3
"""CI duration report: how long every CI job took over the last N days, against its baseline and ceiling.

Run from the repository root as ``python3 -m scripts.ci_durations --repo OWNER/NAME --out-dir ci-durations`` with
``GITHUB_TOKEN`` set (``actions: read`` is enough). It reads ``actions/runs`` and ``actions/runs/{id}/jobs`` from the
GitHub REST API and writes:

- ``ci-durations.json``: every completed job point with its timings, attribution, links, baseline median and ceiling;
- ``ci-durations.html``: one self-contained page charting each job and runner OS over time;
- a markdown summary of the jobs furthest above their baseline, appended to ``--summary`` (``GITHUB_STEP_SUMMARY``).

Real data only: without a token it exits 2 and writes nothing. It is a report, not a gate, so a slow job never fails
it; the CI timing gate keeps that role. The sibling ``scripts/ci_durations_*`` modules hold collection, statistics and
rendering. Standard library only, so the workflow needs no ``uv sync``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.ci_durations_api import ApiClient, Opener
from scripts.ci_durations_collect import Collection, collect
from scripts.ci_durations_html import ReportMeta, render_report
from scripts.ci_durations_stats import (
    DEFAULT_WINDOW,
    Ceilings,
    Regression,
    annotate,
    ceiling_for,
    load_ceilings,
    rolling_medians,
    slowest_vs_baseline,
)
from scripts.ci_timing_gate import FETCH_ERRORS, default_opener

SCHEMA_VERSION = 1
SUMMARY_LIMIT = 10


def positive_int(text: str) -> int:
    """Parse a positive integer argument."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser; repository, server and summary path default to the Actions environment."""
    parser = argparse.ArgumentParser(description="Report CI job durations over time from the GitHub Actions API.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name")
    parser.add_argument("--server", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    parser.add_argument("--days", type=positive_int, default=30, help="days of runs to read (default: 30)")
    parser.add_argument("--window", type=positive_int, default=DEFAULT_WINDOW, help="baseline runs (default: 5)")
    parser.add_argument("--max-runs", type=positive_int, default=500, help="runs to read at most (default: 500)")
    parser.add_argument("--out-dir", default="ci-durations", help="where to write the HTML and JSON files")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""), help="markdown summary file")
    parser.add_argument("--workflows-dir", default=".github/workflows", help="workflow files holding the ceilings")
    return parser


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def format_summary(regressions: list[Regression], ceilings: Ceilings, meta: ReportMeta) -> list[str]:
    """Return the markdown job summary: the window, then the jobs furthest above their baseline."""
    lines = [
        "### CI durations: slowest jobs against their baseline",
        "",
        f"Last {meta.days} days (runs created since {meta.since}): {meta.run_count} runs. Baseline: median of the "
        f"previous {meta.window} successful `main` runs. The full report is the `ci-durations` artefact.",
        "",
    ]
    lines += [f"> {warning.split('::')[-1]}" for warning in meta.warnings]
    if not regressions:
        return [*lines, "No job has a baseline yet."]
    lines += [
        "| job | OS | latest main | median | delta | ratio | ceiling | run |",
        "| --- | -- | ----------- | ------ | ----- | ----- | ------- | --- |",
    ]
    for reg in regressions:
        ceiling = ceiling_for(reg.row, ceilings)
        lines.append(
            f"| {_cell(reg.row.workflow)} / {_cell(reg.row.job_key)} | {_cell(reg.row.runner_os)} "
            f"| {reg.row.duration_seconds:.0f} s | {reg.median:.0f} s | {reg.delta:+.0f} s | {reg.ratio:.2f}x "
            f"| {f'{ceiling} s' if ceiling is not None else 'n/a'} | [run]({reg.row.run_url}) |"
        )
    return lines


def write_outputs(out_dir: Path, collection: Collection, ceilings: Ceilings, meta: ReportMeta) -> list[Regression]:
    """Write the JSON data file and the HTML report; return the slowest-versus-baseline ranking."""
    medians = rolling_medians(collection.rows, meta.window)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": SCHEMA_VERSION,
        **{key: value for key, value in meta.__dict__.items() if key != "warnings"},
        "warnings": list(meta.warnings),
        "rows": annotate(collection.rows, medians, ceilings),
    }
    (out_dir / "ci-durations.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    page = render_report(collection.rows, medians, ceilings, meta)
    (out_dir / "ci-durations.html").write_text(page, encoding="utf-8")
    return slowest_vs_baseline(collection.rows, medians, SUMMARY_LIMIT)


def main(argv: Sequence[str] | None = None, opener: Opener = default_opener, now: datetime | None = None) -> int:
    """Write the report; return 0 on success, 1 when the runs cannot be listed and 2 on bad input."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not args.repo:
        print("::error::GITHUB_TOKEN and --repo (or GITHUB_REPOSITORY) are required; no report was written.")
        return 2
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=args.days)).date()
    client = ApiClient(repo=args.repo, token=token, opener=opener, server=args.server)
    try:
        collection = collect(client, since, args.max_runs)
    except FETCH_ERRORS as exc:
        print(
            f"::error::Could not list the workflow runs of {args.repo} ({type(exc).__name__}); no report was written."
        )
        return 1
    meta = ReportMeta(
        repo=args.repo,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        since=since.isoformat(),
        days=args.days,
        window=args.window,
        run_count=collection.run_count,
        warnings=tuple(collection.warnings),
    )
    ceilings = load_ceilings(Path(args.workflows_dir))
    regressions = write_outputs(Path(args.out_dir), collection, ceilings, meta)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(format_summary(regressions, ceilings, meta)) + "\n")
    for warning in collection.warnings:
        print(warning)
    print(f"Wrote {len(collection.rows)} job points from {collection.run_count} runs to {args.out_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
