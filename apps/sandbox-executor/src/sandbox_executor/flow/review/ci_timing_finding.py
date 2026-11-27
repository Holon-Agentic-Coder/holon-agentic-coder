from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sandbox_executor.flow.github.ci_failure_detail import (
    DetailLookup,
    bare_detail,
    describe_failure,
    gh_detail_lookup,
)
from sandbox_executor.flow.github.ci_slowest_tests import SlowestLookup, gh_slowest_lookup
from sandbox_executor.review_report import FindingRow

MAX_JOBS_DESCRIBED = 5


def _check_of(ci_status: Mapping[str, Any], name: str) -> dict[str, Any]:
    checks = [c for c in ci_status.get("checks") or [] if isinstance(c, dict)]
    return next((c for c in checks if c.get("name") == name), {"name": name})


def timing_gate_details(
    ci_status: Mapping[str, Any], failed_names: Sequence[str], lookup: DetailLookup
) -> list[dict[str, Any]] | None:
    if not failed_names or len(failed_names) > MAX_JOBS_DESCRIBED or ci_status.get("pending"):
        return None
    details = [lookup(_check_of(ci_status, n)) or bare_detail(n) for n in failed_names]
    return details if all(d["timing_gate"] for d in details) else None


def _slowest_text(slowest: Sequence[tuple[str, float]]) -> str:
    if not slowest:
        return "the slowest tests could not be fetched; profile the job with `pytest --durations=10` and start there"
    return "slowest tests: " + "; ".join(f"`{name}` ({seconds:.1f} s)" for name, seconds in slowest)


def gh_timing_seams(repo_dir: str, proxy_env: Mapping[str, str]) -> tuple[DetailLookup, SlowestLookup]:
    return gh_detail_lookup(repo_dir, proxy_env), gh_slowest_lookup(repo_dir, proxy_env)


def timing_gate_finding(
    iteration: int,
    ci_status: Mapping[str, Any],
    failed_names: Sequence[str],
    seams: tuple[DetailLookup, SlowestLookup],
) -> FindingRow | None:
    # why: a timing gate is already rerun once as noise, so a repeat failure is fixed in the slowest tests.
    detail_lookup, slowest_lookup = seams
    details = timing_gate_details(ci_status, failed_names, detail_lookup)
    if details is None:
        return None
    parts = [
        f"{describe_failure(d)}: {_slowest_text(slowest_lookup(_check_of(ci_status, d['name'])))}" for d in details
    ]
    return FindingRow(
        id=f"FIND-CI-{iteration:03d}",
        severity="important",
        path="",
        line=None,
        reviewers=(),
        summary=f"CI timing gate failed in iteration {iteration}: every test passed but the job exceeded its time "
        f"limit. {' | '.join(parts)}.",
        recommendation="Speed up the slowest tests named above or split the job so it fits the limit; do not "
        "repeat the run, because the same job will exceed the limit again.",
    )
