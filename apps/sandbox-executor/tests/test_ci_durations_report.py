"""Tests for the CI duration report's HTML: self-contained, escaped, and linking every point to run, commit and PR."""

from __future__ import annotations

import re

from scripts.ci_durations_collect import JobDuration
from scripts.ci_durations_html import ReportMeta, render_report
from scripts.ci_durations_stats import rolling_medians
from scripts.ci_durations_svg import nice_max, point_classes
from tests.test_ci_durations import REPO, SHA_PR, point

META = ReportMeta(
    repo=REPO, generated_at="2026-11-20T00:00:00Z", since="2026-10-21", days=30, window=5, run_count=9, warnings=()
)
CEILINGS = {("test-unit.yml", "unit"): 600}


def fixture_rows() -> list[JobDuration]:
    rows = [point(day, 100.0 + day) for day in range(1, 7)]
    rows.append(
        point(
            7,
            150.0,
            ref_class="pr",
            event="pull_request",
            branch="feature/<script>alert(1)</script>",
            head_sha=SHA_PR,
            pr_number=7,
            pr_url=f"https://github.com/{REPO}/pull/7",
            commit_url=f"https://github.com/{REPO}/commit/{SHA_PR}",
            conclusion="failure",
        )
    )
    rows.append(point(8, 120.0, run_attempt=2))
    rows.append(point(9, 50.0, job="lint", workflow_path=".github/workflows/test-hygiene.yml", conclusion="cancelled"))
    return rows


def render(rows: list[JobDuration] | None = None, meta: ReportMeta = META) -> str:
    rows = rows if rows is not None else fixture_rows()
    return render_report(rows, rolling_medians(rows), CEILINGS, meta)


def test_the_report_is_self_contained() -> None:
    page = render()
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page
    lowered = page.lower()
    for forbidden in ("<script", "<link", "@import", "url(", "src=", "<img", "<iframe", "<object", "<embed"):
        assert forbidden not in lowered, forbidden
    assert "<style>" in page and "<svg" in page


def test_every_href_is_an_anchor_or_a_repository_link() -> None:
    hrefs = re.findall(r'href="([^"]*)"', render())
    assert hrefs
    allowed = re.compile(
        rf"#s\d+|https://github\.com/{re.escape(REPO)}/(actions/runs/\d+/job/\d+|commit/[0-9a-f]+|pull/\d+)"
    )
    assert all(allowed.fullmatch(href) for href in hrefs), [h for h in hrefs if not allowed.fullmatch(h)]


def test_every_point_links_its_run_commit_and_pull_request() -> None:
    page = render()
    for row in fixture_rows():
        assert page.count(f'href="{row.run_url}"') >= 3  # chart point, queue strip bar and table row
        assert f'href="{row.commit_url}"' in page
    assert f'href="https://github.com/{REPO}/pull/7">#7</a>' in page


def test_failure_rerun_and_ref_markers_are_present() -> None:
    page = render()
    assert 'class="pt pr fail"' in page
    assert 'class="pt main rerun"' in page
    assert 'class="pt main cancelled"' in page
    assert '<circle class="ring"' in page
    assert '<rect class="mark"' in page and '<circle class="mark"' in page
    assert point_classes(point(1, 1.0, conclusion="timed_out")) == "pt main fail"


def test_median_and_ceiling_lines_appear_only_where_defined() -> None:
    page = render()
    lint, unit = page.split('<section id="s1">')
    assert '<polyline class="median"' in unit and "ceiling 600 s" in unit
    assert '<polyline class="median"' not in lint and 'class="ceiling"' not in lint
    assert "ceiling n/a" in lint


def test_api_strings_are_escaped() -> None:
    page = render()
    assert "<script>alert(1)" not in page
    assert "feature/&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_the_header_states_the_window_and_the_warnings() -> None:
    meta = ReportMeta(**{**META.__dict__, "warnings": ("::warning::The run listing was truncated after 500 runs",)})
    page = render(meta=meta)
    assert "Last 30 days (runs created since 2026-10-21), 9 runs, 9 job points" in page
    assert '<p class="warning">The run listing was truncated after 500 runs</p>' in page


def test_an_empty_window_renders_a_page_without_charts() -> None:
    page = render([])
    assert "No completed jobs in the window." in page and "<svg" not in page


def test_nice_max_rounds_up_to_half_a_power_of_ten() -> None:
    assert [nice_max(v) for v in (0, 7, 130, 600, 601)] == [10.0, 7.0, 150.0, 600.0, 650.0]
