"""Per-test evidence and verdict wording for the CI timing gate (``scripts/ci_timing_gate.py``).

The gate hands this module the per-test times of the current job and of its ``main`` baseline records. The module
does two things with them:

- It renders the per-test report: the tests whose time grew the most, which never fails a job on its own.
- It gathers the evidence that tells runner noise from a regression once a limit is breached. Every test has a
  baseline, the median of its times in the baseline records. The suite-wide scale is the median of current over
  baseline across the comparable tests, never below 1. A test's attributed seconds are its growth beyond that scale,
  so a slowdown that hits every test alike attributes nothing. The offenders are all the tests whose attributed
  seconds pass the report thresholds; only the ten largest are named. New tests are attributed together: their time
  less the scaled baseline time of the tests that vanished, so a renamed test attributes nothing. That net time is
  shared out among the new tests, largest first, and a new test is an offender only for a part above the report
  minimum. With fewer than twenty comparable tests, no baseline per-test times or no excess there is no evidence.

A breached limit is forgiven as runner variance only on positive evidence, and both parts are required:

- The offenders and the new tests explain less than half of the per-test excess, so the slowdown is not concentrated.
- The suite-wide scale accounts for the breach: the judged value divided by the scale is back inside the limit. A
  slowdown that leaves the median test where it was is therefore never forgiven, however it is spread.

In every other case the breach stands and the verdict line says which part failed. A ceiling breach can hide a relative
one. Forgiving the ceiling then leaves the relative breach to be judged the same way, so a slower runner never lifts a
regression over the relative check: when the scale does not account for it, the relative verdict is the outcome.

The module also holds the gate's ``Verdict`` and ``Breach``, ``rejudge`` (which applies the evidence to a verdict with
a breached limit) and the wording of every verdict line: one line each, stating the measure judged, the baseline, the
threshold and the reason.

The module uses the standard library only and keeps Python 3.9 syntax, because the workflows run the gate with the
runner's own ``python3``. It never imports from ``scripts/ci_timing_gate.py``; the gate imports it.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

REPORT_LIMIT = 10
REPORT_GROWTH = 0.5
REPORT_MIN_SECONDS = 5.0
COMPARABLE_FLOOR_SECONDS = 0.05
MIN_COMPARABLE_TESTS = 20
CONCENTRATED_SHARE = 0.5
TEST_TIME_NAMES = {"suite": "suite test time", "sum": "summed test time"}


@dataclass(frozen=True)
class Offender:
    """A test whose time grew beyond the report thresholds; ``is_new`` marks a test in no baseline record."""

    test_id: str
    baseline: float
    current: float
    is_new: bool = False

    @property
    def growth(self) -> float:
        """Return the absolute growth in seconds."""
        return self.current - self.baseline


def top_offenders(
    current: dict[str, float],
    baselines: Sequence[dict[str, float]],
    limit: int = REPORT_LIMIT,
    growth: float = REPORT_GROWTH,
    min_seconds: float = REPORT_MIN_SECONDS,
) -> list[Offender]:
    """Return the tests whose time grew by more than ``growth`` and ``min_seconds`` against their baseline median.

    Tests absent from every baseline record are ignored. Rows are sorted by absolute growth, largest first.
    """
    rows: list[Offender] = []
    for test_id, seconds in current.items():
        values = [b[test_id] for b in baselines if test_id in b]
        if not values:
            continue
        median = float(statistics.median(values))
        if seconds > median * (1 + growth) and seconds - median > min_seconds:
            rows.append(Offender(test_id, median, seconds))
    rows.sort(key=lambda row: (-row.growth, row.test_id))
    return rows[:limit]


def format_report(rows: Sequence[Offender]) -> list[str]:
    """Render the per-test report as markdown lines."""
    if not rows:
        return ["Per-test report: no test grew by more than 50 % and 5 s against its baseline median."]
    lines = [
        f"Per-test report: top {len(rows)} tests that grew by more than 50 % and 5 s against their baseline median.",
        "",
        "| test | baseline median (s) | current (s) | growth (s) |",
        "| ---- | ------------------- | ----------- | ---------- |",
    ]
    for row in rows:
        lines.append(f"| `{row.test_id}` | {row.baseline:.1f} | {row.current:.1f} | +{row.growth:.1f} |")
    return lines


def per_test_report(current: dict[str, float], baselines: Sequence[dict[str, float]]) -> list[str]:
    """Render the per-test report, or say it was skipped when no baseline record carries per-test times."""
    if not any(baselines):
        return ["Per-test report: skipped, no baseline records with per-test times yet."]
    return format_report(top_offenders(current, baselines))


@dataclass(frozen=True)
class NoiseEvidence:
    """What the per-test times say about a slowdown: how far the whole suite moved and what specific tests explain.

    ``offenders`` holds every offender, largest first. ``attributed`` sums the slowed offenders and ``new_seconds``,
    the time of the new tests net of the tests that vanished.
    """

    comparable: int
    median_ratio: float
    excess: float
    attributed: float
    offenders: tuple[Offender, ...]
    new_seconds: float = 0.0

    @property
    def scale(self) -> float:
        """Return the suite-wide scale: the median per-test ratio, never below 1."""
        return max(1.0, self.median_ratio)

    @property
    def share(self) -> float:
        """Return the part of the excess that the offenders and the new tests explain, between 0 and 1."""
        return self.attributed / self.excess if self.excess > 0 else 0.0

    @property
    def concentrated(self) -> bool:
        """Return true when the offenders explain enough of the excess to call the slowdown a regression."""
        return self.share >= CONCENTRATED_SHARE


def baseline_medians(baselines: Sequence[dict[str, float]]) -> dict[str, float]:
    """Map every test to the median of its times over the baseline records that contain it."""
    samples: dict[str, list[float]] = {}
    for record in baselines:
        for test_id, seconds in record.items():
            samples.setdefault(test_id, []).append(seconds)
    return {test_id: float(statistics.median(values)) for test_id, values in samples.items()}


def comparable_ratios(current: dict[str, float], medians: dict[str, float]) -> list[float]:
    """Return current over baseline for every shared test whose baseline is at least the comparable floor."""
    return [
        current[test_id] / median
        for test_id, median in medians.items()
        if median >= COMPARABLE_FLOOR_SECONDS and test_id in current
    ]


def net_new_seconds(current: dict[str, float], medians: dict[str, float], scale: float = 1.0) -> float:
    """Return the time of the new tests less the baseline time of the vanished tests times ``scale``, never below 0.

    A new test is in no baseline record and a vanished test is in no current report, so a renamed test is both and
    nets to nothing.
    """
    new = sum(seconds for test_id, seconds in current.items() if test_id not in medians)
    vanished = sum(median for test_id, median in medians.items() if test_id not in current)
    return max(0.0, new - vanished * scale)


def per_test_excess(current: dict[str, float], medians: dict[str, float]) -> float:
    """Sum every shared test's growth over its baseline, plus the time of the new tests net of the vanished ones."""
    shared = sum(max(0.0, seconds - medians[test_id]) for test_id, seconds in current.items() if test_id in medians)
    return shared + net_new_seconds(current, medians)


def attributed_offenders(
    current: dict[str, float], medians: dict[str, float], scale: float, new_seconds: float
) -> list[tuple[float, Offender]]:
    """Return ``(attributed seconds, offender)`` for the tests that explain a slowdown, largest first.

    A shared test's attributed seconds are ``current - baseline * scale``, and it is an offender when it passes both
    report thresholds against its scaled baseline. The new tests share ``new_seconds``, their net time: the largest
    takes up to its whole time, then the next largest, and a new test is an offender while its part is above
    ``REPORT_MIN_SECONDS``. A rename whose net time is a few seconds therefore names none of the renamed tests. Every
    offender is returned: capping the rows here would let a slowdown of many tests fall under the concentrated share.
    """
    rows: list[tuple[float, Offender]] = []
    added: list[tuple[str, float]] = []
    for test_id, seconds in current.items():
        if test_id not in medians:
            added.append((test_id, seconds))
            continue
        median = medians[test_id]
        attributed = seconds - median * scale
        if attributed > REPORT_MIN_SECONDS and seconds > median * scale * (1 + REPORT_GROWTH):
            rows.append((attributed, Offender(test_id, median, seconds)))
    remaining = new_seconds
    for test_id, seconds in sorted(added, key=lambda item: (-item[1], item[0])):
        part = min(seconds, remaining)
        if part <= REPORT_MIN_SECONDS:
            break
        rows.append((part, Offender(test_id, 0.0, seconds, is_new=True)))
        remaining -= part
    rows.sort(key=lambda row: (-row[0], row[1].test_id))
    return rows


def gather_evidence(current: dict[str, float], baselines: Sequence[dict[str, float]]) -> NoiseEvidence | None:
    """Gather the noise evidence for ``current`` against ``baselines``, or ``None`` when it is insufficient.

    Insufficient means fewer than ``MIN_COMPARABLE_TESTS`` comparable tests (which covers baseline records without
    per-test times) or no positive per-test excess; ``insufficient_reason`` words the cause.

    The new tests are attributed as one net amount, whatever their size and number, so many small new tests count in
    full and a rename counts for nothing. A new test is named as an offender only for its part of that net amount.
    """
    medians = baseline_medians(baselines)
    ratios = comparable_ratios(current, medians)
    excess = per_test_excess(current, medians)
    if len(ratios) < MIN_COMPARABLE_TESTS or excess <= 0:
        return None
    median_ratio = float(statistics.median(ratios))
    scale = max(1.0, median_ratio)
    new_seconds = net_new_seconds(current, medians, scale)
    rows = attributed_offenders(current, medians, scale, new_seconds)
    attributed = sum(seconds for seconds, offender in rows if not offender.is_new) + new_seconds
    offenders = tuple(offender for _, offender in rows)
    return NoiseEvidence(len(ratios), median_ratio, excess, attributed, offenders, new_seconds)


def insufficient_reason(current: dict[str, float], baselines: Sequence[dict[str, float]]) -> str:
    """Say in a few words why ``gather_evidence`` found no evidence in these times."""
    if not any(baselines):
        return "no baseline per-test times"
    found = len(comparable_ratios(current, baseline_medians(baselines)))
    if found < MIN_COMPARABLE_TESTS:
        return f"only {found} comparable tests, fewer than {MIN_COMPARABLE_TESTS}"
    return "no per-test excess"


def describe_evidence(evidence: NoiseEvidence) -> str:
    """Word the evidence as a one-line fragment: suite-wide ratio, tests compared, offenders and their share.

    The net time of the new tests is worded only when there is some, and the share then says that it counts them.
    """
    new, share = "", "offender share"
    if evidence.new_seconds > 0:
        new = f"{evidence.new_seconds:.1f} s of new test time net of removed tests, "
        share = "offender and new-test share"
    return (
        f"median per-test ratio {evidence.median_ratio:.2f} over {evidence.comparable} comparable tests, "
        f"{len(evidence.offenders)} offenders, {new}{share} {evidence.share:.1%} of the {evidence.excess:.1f} s "
        f"per-test excess (concentrated from {CONCENTRATED_SHARE:.0%})"
    )


def printable_test_id(test_id: str) -> str:
    """Return ``test_id`` on one line and safe inside a markdown code span: no control character, no backtick."""
    return "".join(char if char.isprintable() else " " for char in test_id).replace("`", "'")


def describe_offenders(offenders: Sequence[Offender]) -> str:
    """Word the offenders as a one-line fragment: each test id with its time before (or ``new``) and after."""
    parts: list[str] = []
    for offender in offenders:
        before = "new" if offender.is_new else f"{offender.baseline:.1f} s"
        parts.append(f"`{printable_test_id(offender.test_id)}` {before} -> {offender.current:.1f} s")
    return ", ".join(parts)


def name_offenders(offenders: Sequence[Offender]) -> str:
    """Word the sentence that names at most ``REPORT_LIMIT`` offenders, with a leading space; empty without any."""
    if not offenders:
        return ""
    shown = f" (top {REPORT_LIMIT} of {len(offenders)})" if len(offenders) > REPORT_LIMIT else ""
    return f" Offenders{shown}: {describe_offenders(offenders[:REPORT_LIMIT])}."


def took(seconds: float, kind: str = "") -> str:
    """Word a judged value with its measure: job wall clock, or test time of ``kind`` (``suite`` or ``sum``)."""
    return f"{TEST_TIME_NAMES[kind]} was {seconds:.0f} s" if kind else f"job took {seconds:.0f} s"


def fallback_note(lacking: int, window: int, kind: str) -> str:
    """Word, as a clause of a verdict line, that ``lacking`` of ``window`` baseline records lack the run's test time."""
    return (
        f"; {lacking} of {window} baseline records carry no {TEST_TIME_NAMES[kind]}, so only the job's wall clock can "
        "be compared with them"
    )


def over_ceiling(judged: str, ceiling: float) -> str:
    """Word a ceiling breach; ``judged`` is the judged value in the words of ``took``."""
    return f"{judged}, above the {ceiling:.0f} s ceiling"


def ceiling_error(breach: str, label: str) -> str:
    """Word the ceiling failure: the breach, then that the override ``label`` cannot lift the ceiling and what can."""
    return (
        f"::error::{breach[0].upper()}{breach[1:]}. The ceiling cannot be overridden by the '{label}' label; it "
        "changes only through a committed edit to the --ceiling flag in the workflow file."
    )


def cold_start_notice(found: int, runs: int, ceiling: float, judged: str) -> str:
    """Word the cold start: too few baseline records for the relative check, so only the ceiling applied."""
    return (
        f"::notice::Cold start: {found} of {runs} main baseline records found, so the relative check is skipped; only "
        f"the {ceiling:.0f} s ceiling applies ({judged})."
    )


def against_baseline(judged: str, median: float, runs: int, tolerance: float, min_delta: float) -> str:
    """Word a relative comparison: the judged value, the baseline median it is held against and both thresholds."""
    return (
        f"{judged} against a baseline median of {median:.0f} s over {runs} main runs "
        f"(tolerance {tolerance:.0%}, minimum delta {min_delta:.0f} s)"
    )


def accepted_warning(comparison: str, label: str) -> str:
    """Word a relative breach that the override ``label`` accepted."""
    return f"::warning::Timing regression accepted by the '{label}' label: {comparison}."


def regression_error(comparison: str, label: str) -> str:
    """Word a relative breach that fails the job, with the two ways out."""
    return (
        f"::error::Timing regression: {comparison}. Speed the job up, or add the '{label}' label to accept a "
        "deliberate slow-down."
    )


def without_command(line: str) -> str:
    """Return ``line`` without a leading workflow command such as ``::error::``, as the job summary shows it."""
    return line.split("::", 2)[2] if line.startswith("::") and line.count("::") > 1 else line


@dataclass(frozen=True)
class Breach:
    """A breached limit: its wording, the judged value in seconds and the value above which the limit is breached.

    ``words`` states the measure, its value, the baseline and the threshold. ``limit`` is the ceiling, or for the
    relative check the larger of the baseline median plus the tolerance and plus the minimum delta.
    """

    words: str
    seconds: float
    limit: float


@dataclass(frozen=True)
class Verdict:
    """The gate's decision and the message lines that explain it.

    ``breach`` is the limit that was breached. It is ``None`` when no limit was breached and stays set when the
    override label accepted a relative breach. ``beneath`` is set only on a ceiling breach that hides a relative one:
    it is the verdict of the relative check, which is the outcome once runner variance forgives the ceiling alone.
    """

    passed: bool
    lines: tuple[str, ...]
    breach: Breach | None = None
    beneath: Verdict | None = None

    def with_reason(self, reason: str) -> Verdict:
        """Return the same decision with ``reason`` appended to its line."""
        return Verdict(self.passed, (f"{self.lines[0]} {reason}",), self.breach, self.beneath)


def describe_scale(evidence: NoiseEvidence, breach: Breach) -> str:
    """Word the judged value with the suite-wide slowdown taken out, and the side of the limit it then falls on."""
    rescaled = breach.seconds / evidence.scale
    side = "inside" if rescaled <= breach.limit else "still above"
    return (
        f"divided by the suite-wide scale of {evidence.scale:.2f} the judged value is {rescaled:.1f} s, {side} its "
        f"limit of {breach.limit:.1f} s"
    )


def forgive(verdict: Verdict, breach: Breach, evidence: NoiseEvidence, basis: str, offenders: str) -> Verdict:
    """Forgive ``breach``, a spread slowdown that the suite-wide scale accounts for, as runner variance.

    The relative breach beneath a ceiling breach is forgiven with it when the scale accounts for it as well. When it
    does not, the relative verdict is returned and its line says which of the two breaches is forgiven.
    """
    forgiven = (
        f"::warning::Runner variance: {breach.words}. The breach is forgiven and the gate passes, because the "
        "slowdown is spread across the suite and not concentrated in specific tests: "
        f"{describe_scale(evidence, breach)}."
    )
    hidden = verdict.beneath
    if hidden is None or hidden.breach is None:
        return Verdict(True, (f"{forgiven} {basis}",), breach)
    relative = describe_scale(evidence, hidden.breach)
    if hidden.breach.seconds / evidence.scale > hidden.breach.limit:
        unexplained = (
            f"Runner variance does not account for the breach: {relative}. It accounts only for the ceiling breach, "
            f"which is forgiven: {breach.words}, and {describe_scale(evidence, breach)}."
        )
        return hidden.with_reason(f"{unexplained} {basis}{offenders}")
    both = f"{forgiven} The relative breach is forgiven with it: {hidden.breach.words}, and {relative}. {basis}"
    return Verdict(True, (both,), breach)


def rejudge(verdict: Verdict, current: dict[str, float], baselines: Sequence[dict[str, float]]) -> Verdict:
    """Judge a breached limit again with the per-test times; a verdict without a breach is returned unchanged.

    - No evidence: the verdict stands, and its line says why runner variance could not be assessed.
    - Concentrated slowdown: the verdict stands, and its line names the offenders with their times before and after.
    - Spread slowdown that the suite-wide scale does not account for: the verdict stands, and its line says so.
    - Spread slowdown that the suite-wide scale accounts for: runner variance. The verdict passes with one warning that
      states the breach and the evidence.

    A ceiling verdict with a relative breach beneath it is forgiven only when the scale accounts for both. When it
    accounts for the ceiling alone, the relative verdict is returned, failed or accepted by the label as it was, and
    its line says that the ceiling breach is forgiven and the relative one is not.
    """
    breach = verdict.breach
    if breach is None:
        return verdict
    evidence = gather_evidence(current, baselines)
    if evidence is None:
        return verdict.with_reason(f"Runner variance could not be assessed: {insufficient_reason(current, baselines)}.")
    basis = (
        f"Evidence against each test's median over the main baseline records ({sum(1 for b in baselines if b)} with "
        f"per-test times): {describe_evidence(evidence)}."
    )
    offenders = name_offenders(evidence.offenders)
    if evidence.concentrated:
        return verdict.with_reason(f"The slowdown is concentrated in specific tests. {basis}{offenders}")
    if breach.seconds / evidence.scale > breach.limit:
        unexplained = f"Runner variance does not account for the breach: {describe_scale(evidence, breach)}."
        return verdict.with_reason(f"{unexplained} {basis}{offenders}")
    return forgive(verdict, breach, evidence, basis, offenders)
