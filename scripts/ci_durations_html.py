"""Render the CI duration report as one self-contained static HTML file.

The page has an inline stylesheet, inline SVG charts from ``scripts.ci_durations_svg`` and no script, font, image or
other external asset, so it opens offline from a downloaded artefact. One section per workflow, job and runner OS
holds the duration chart, the queue-versus-run strip and a table that links every point to its run, commit and pull
request. Every API-derived string is escaped; links are only those built from the configured server.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from scripts.ci_durations_collect import JobDuration
from scripts.ci_durations_stats import Ceilings, Medians, ceiling_for, group_series
from scripts.ci_durations_svg import duration_chart, fmt_seconds, queue_strip


@dataclass(frozen=True)
class ReportMeta:
    """What the report covers: repository, window, baseline size, run count and collection warnings."""

    repo: str
    generated_at: str
    since: str
    days: int
    window: int
    run_count: int
    warnings: tuple[str, ...] = ()


STYLE = """
:root{color-scheme:light;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--grid:#e4e3df;--main:#2a78d6;
--pr:#eb6834;--critical:#d03b3b;--muted:#a3a29d;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;
--grid:#383835;--main:#3987e5;--pr:#d95926;--muted:#6b6a66}}
body{background:var(--surface);color:var(--ink);margin:0 auto;max-width:820px;padding:1rem 1.25rem;line-height:1.4}
a{color:var(--main)}h2{font-size:1.1rem;margin:2rem 0 .25rem}.meta,.legend{color:var(--ink-2);font-size:.85rem}
.warning{color:var(--critical)}.legend span{margin-right:1rem;white-space:nowrap}
svg{display:block;width:100%;height:auto}.tick,.ceiling-label{fill:var(--ink-2);font-size:11px}
.grid{stroke:var(--grid);stroke-width:1}.ceiling{stroke:var(--ink);stroke-width:1.5;stroke-dasharray:6 4}
.median{fill:none;stroke:var(--ink-2);stroke-width:2}.hit{fill:transparent}
.pt .mark{stroke:var(--surface);stroke-width:2}.pt.main .mark{fill:var(--main)}.pt.pr .mark{fill:var(--pr)}
.pt.other .mark{fill:var(--ink-2)}
.pt.fail .mark{fill:var(--surface);stroke:var(--critical);stroke-width:2.5}
.pt.cancelled .mark{fill:var(--muted)}.ring{fill:none;stroke:var(--ink);stroke-width:1.5}
.queue{fill:var(--muted)}.run{fill:var(--main)}
table{border-collapse:collapse;font-size:.8rem;width:100%;margin-top:.5rem}
th,td{border-bottom:1px solid var(--grid);padding:.2rem .4rem;text-align:left}td.num{text-align:right}
details{margin-top:.25rem}
"""

LEGEND = (
    '<p class="legend"><span>● main (circle)</span><span>■ pull-request head (square)</span>'
    "<span>▲ other: dispatch, schedule or branch push (triangle)</span>"
    "<span>○ red hollow: failed</span><span>grey: cancelled</span><span>◎ ring: re-run attempt</span>"
    "<span>— rolling median of the previous successful main runs</span><span>- - timing-gate ceiling</span>"
    "<span>strip: queue (grey) below run (blue)</span></p>"
)


def _link(url: str | None, text: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{escape(text)}</a>' if url else ""


def table_row(row: JobDuration, median: float | None) -> str:
    """Return one table row with the point's figures and its run, commit and pull request links."""
    links = [
        _link(row.run_url, "run"),
        _link(row.commit_url, row.head_sha[:7]),
        _link(row.pr_url, f"#{row.pr_number}"),
    ]
    cells = [
        f"<td>{escape(row.started_at.replace('T', ' ').removesuffix('Z'))}",
        f"<td>{'main' if row.ref_class == 'main' else escape(row.branch or row.ref_class)}",
        f'<td class="num">{fmt_seconds(row.duration_seconds)}',
        f'<td class="num">{fmt_seconds(row.queue_seconds)}',
        f'<td class="num">{fmt_seconds(median)}',
        f"<td>{escape(row.conclusion)}",
        f"<td>{row.run_attempt}{' (re-run)' if row.is_rerun else ''}",
        f"<td>{' '.join(link for link in links if link)}",
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def series_section(anchor: str, rows: list[JobDuration], medians: Medians, ceiling: int | None) -> str:
    """Return the section of one series: heading, chart, queue strip and point table."""
    head = rows[-1]
    title = f"{head.workflow} / {head.job_key} / {head.runner_os}"
    header = "<tr><th>start (UTC)<th>ref<th>duration<th>queue<th>baseline<th>conclusion<th>attempt<th>links</tr>"
    body = "".join(table_row(row, medians.get(row)) for row in reversed(rows))
    return (
        f'<section id="{anchor}"><h2>{escape(title)}</h2>'
        f'<p class="meta">{len(rows)} points; ceiling {fmt_seconds(ceiling)}</p>'
        f"{duration_chart(rows, medians, ceiling)}{queue_strip(rows)}"
        f"<details><summary>Every point</summary><table>{header}{body}</table></details></section>"
    )


def render_report(rows: list[JobDuration], medians: Medians, ceilings: Ceilings, meta: ReportMeta) -> str:
    """Return the whole report as one HTML document."""
    series = list(group_series(rows).items())
    toc = "".join(
        f'<li><a href="#s{index}">{escape(" / ".join(key))}</a> ({len(points)})</li>'
        for index, (key, points) in enumerate(series)
    )
    sections = "".join(
        series_section(f"s{index}", points, medians, ceiling_for(points[-1], ceilings))
        for index, (_, points) in enumerate(series)
    )
    warnings = "".join(f'<p class="warning">{escape(w.split("::")[-1])}</p>' for w in meta.warnings)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>CI durations: {escape(meta.repo)}</title><style>{STYLE}</style></head><body>"
        f"<h1>CI durations: {escape(meta.repo)}</h1>"
        f'<p class="meta">Last {meta.days} days (runs created since {escape(meta.since)}), {meta.run_count} runs, '
        f"{len(rows)} job points. Baseline: median of the previous {meta.window} successful main runs of the same "
        f"job and runner. Generated {escape(meta.generated_at)}.</p>{warnings}{LEGEND}"
        f"<nav><ul>{toc}</ul></nav>{sections or '<p>No completed jobs in the window.</p>'}</body></html>\n"
    )
