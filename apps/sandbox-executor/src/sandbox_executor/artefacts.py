"""Validation of the artefacts authoring agents write incrementally (Bean 0138).

Planner and executor agents are asked to create their artefact (``plans/P-*.md`` or ``executions/E-*.md``) with their
file-write tool straight away and extend it section by section, instead of emitting everything in one final stdout
message. The file on disk is therefore the source of truth: a truncated final message (``stopReason: length``, exit
code 1) costs nothing as long as the file is complete.

This module decides what "complete" means. An :class:`ArtefactSpec` lists the required headings, a size floor and the
metric rows that must parse as numbers; :func:`validate_artefact` measures a file against it, and
:func:`build_reprompt` produces the single targeted follow-up prompt that names only what is missing.
"""

import os
import re
from dataclasses import dataclass, field

# A metrics row is ``| name | number |``; anything non-numeric in the value cell is skipped.
_METRIC_ROW = re.compile(r"\|\s*([a-zA-Z_0-9]+)\s*\|\s*([0-9\.\-]+)\s*\|")


def _find_heading(lines: list[str], pattern: str) -> int | None:
    """Index of the first line whose stripped text matches ``pattern`` case-insensitively, or None.

    Shared by the section check and :func:`parse_section_metrics`, so a heading the validator accepts is always the
    heading the metrics are read from.
    """
    regex = re.compile(pattern, re.IGNORECASE)
    return next((index for index, line in enumerate(lines) if regex.match(line.strip())), None)


@dataclass(frozen=True)
class ArtefactSection:
    """A required section: a human-readable label for re-prompts and the heading pattern that satisfies it."""

    label: str
    pattern: str

    def present_in(self, content: str) -> bool:
        """True when a heading matching :attr:`pattern` starts any line (case-insensitive, leading spaces ignored)."""
        return _find_heading(content.splitlines(), self.pattern) is not None


@dataclass(frozen=True)
class ArtefactSpec:
    """Structural and numeric requirements for an authored artefact."""

    name: str
    sections: tuple[ArtefactSection, ...]
    size_floor: int
    required_metrics: tuple[str, ...] = ()
    # Suffixes stripped from metric names before matching, so ``p_success_pred`` satisfies ``p_success``.
    metric_suffixes: tuple[str, ...] = ("_pred",)
    # Heading of the section whose rows are the artefact's metrics (see :func:`parse_section_metrics`).
    metrics_section: str | None = None


@dataclass
class ArtefactReport:
    """Result of measuring one file against an :class:`ArtefactSpec`."""

    path: str
    spec: ArtefactSpec
    exists: bool = False
    size: int = 0
    missing_sections: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    missing_metrics: list[str] = field(default_factory=list)

    @property
    def size_ok(self) -> bool:
        """True when the file reaches the spec's size floor."""
        return self.exists and self.size >= self.spec.size_floor

    @property
    def metrics_ok(self) -> bool:
        """True when every required metric parsed as a number from the spec's metrics section."""
        return self.exists and not self.missing_metrics

    @property
    def usable(self) -> bool:
        """True when the artefact can be committed: it exists, exceeds the size floor and its metrics parse."""
        return self.size_ok and self.metrics_ok

    @property
    def complete(self) -> bool:
        """True when the artefact is usable and every required section is present."""
        return self.usable and not self.missing_sections

    def missing_labels(self) -> list[str]:
        """Everything a follow-up turn must add, in spec order: sections first, then metric rows."""
        labels = list(self.missing_sections)
        if self.missing_metrics:
            labels.append("metrics table rows: " + ", ".join(self.missing_metrics))
        if self.exists and not self.size_ok and not labels:
            labels.append(f"substantive content (file is {self.size} bytes, at least {self.spec.size_floor} expected)")
        return labels

    def describe(self) -> str:
        """One-line summary for logs."""
        if not self.exists:
            return f"{self.spec.name} artefact {self.path} does not exist"
        state = "complete" if self.complete else "usable" if self.usable else "invalid"
        missing = self.missing_labels()
        suffix = f"; missing: {'; '.join(missing)}" if missing else ""
        return f"{self.spec.name} artefact {self.path} is {state} ({self.size} bytes){suffix}"


def parse_metrics(content: str, suffixes: tuple[str, ...] = ("_pred",)) -> dict[str, float]:
    """Parse every ``| name | number |`` row in ``content``, stripping the given suffixes from names.

    Later rows overwrite earlier ones with the same name; use :func:`parse_section_metrics` when only one section (such
    as a plan's overall table) should count.
    """
    metrics: dict[str, float] = {}
    for line in content.splitlines():
        match = _METRIC_ROW.search(line)
        if not match:
            continue
        name = match.group(1).strip()
        try:
            value = float(match.group(2).strip())
        except ValueError:
            # Ignore malformed numeric values and continue parsing other metric rows.
            continue
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        metrics[name.strip()] = value
    return metrics


def parse_section_metrics(
    content: str, heading: str | None, suffixes: tuple[str, ...] = ("_pred",)
) -> dict[str, float]:
    """Parse the metric rows of the section headed by ``heading`` (a regex matched case-insensitively per line).

    This is the single metrics parser for authored artefacts: :func:`validate_artefact` uses it to gate a file and the
    planner (and ``holon flow``, through ``planner.parse_metrics``) uses the same result for the ``plans.jsonl`` row, so
    the gate and the ledger can never read different numbers from one document. When the heading is present, only the
    rows up to the next ``## `` heading count, wherever the section sits, so per-step tables before or after it cannot
    stand in for the overall values (PR #91). When it is absent (or ``heading`` is None), every row in the document
    counts and the last matching row wins, which is PR #91's fallback.
    """
    lines = content.splitlines()
    if heading is not None:
        start = _find_heading(lines, heading)
        if start is not None:
            section: list[str] = []
            for line in lines[start + 1 :]:
                if line.startswith("## "):
                    break
                section.append(line)
            lines = section
    return parse_metrics("\n".join(lines), suffixes)


def plan_metrics(content: str) -> dict[str, float]:
    """Plan-level predicted metrics: the ``## Overall Plan Metrics`` rows, parsed exactly as :data:`PLAN_SPEC` does."""
    return parse_section_metrics(content, PLAN_SPEC.metrics_section, PLAN_SPEC.metric_suffixes)


def validate_artefact(path: str, spec: ArtefactSpec) -> ArtefactReport:
    """Measure the file at ``path`` against ``spec``; never raises for a missing or unreadable file."""
    report = ArtefactReport(path=path, spec=spec)
    if not os.path.isfile(path):
        report.missing_sections = [section.label for section in spec.sections]
        report.missing_metrics = list(spec.required_metrics)
        return report
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        report.missing_sections = [section.label for section in spec.sections]
        report.missing_metrics = list(spec.required_metrics)
        return report
    report.exists = True
    report.size = len(content.encode("utf-8"))
    report.missing_sections = [section.label for section in spec.sections if not section.present_in(content)]
    report.metrics = parse_section_metrics(content, spec.metrics_section, spec.metric_suffixes)
    report.missing_metrics = [name for name in spec.required_metrics if name not in report.metrics]
    return report


def build_reprompt(missing: list[str], path: str, *, partial: bool = False, modify_code: bool = True) -> str:
    """Prompt for the single follow-up turn, listing only what is missing from the artefact at ``path``.

    Args:
        missing: Labels from :meth:`ArtefactReport.missing_labels`.
        path: Repository-relative artefact path the agent must extend.
        partial: True after a budget breach, when the previous turn was cut off mid-write.
        modify_code: False for the executor, whose follow-up must only touch its notes.
    """
    lines = []
    if partial:
        lines.append(
            f"Your previous turn was stopped by a time budget. The file `{path}` on disk is partial (create it if it "
            "does not exist yet); keep everything already in it."
        )
    else:
        lines.append(f"The file `{path}` is missing required content.")
    lines.append("Append only the following missing sections to that file with your file write or edit tool:")
    lines.extend(f"- {label}" for label in missing)
    lines.append("Do not rewrite or reorder sections that are already present.")
    if not modify_code:
        lines.append("Do not modify any code or other files; only append the listed sections to the notes file.")
    lines.append("When the file is complete, reply with a single short confirmation line.")
    return "\n".join(lines)


PLAN_METRICS = ("p_success", "entropy", "impact", "cost", "learning_value", "ev")
# The planner recomputes EV from the five inputs with the config-driven formula, so an ``ev_pred`` row is optional.
PLAN_REQUIRED_METRICS = tuple(name for name in PLAN_METRICS if name != "ev")

PLAN_METRICS_HEADING = r"^##\s+Overall Plan Metrics\b"
EXECUTION_METRICS_HEADING = r"^##\s+Actual Metrics\b"

PLAN_SPEC = ArtefactSpec(
    name="plan",
    sections=(
        ArtefactSection("`# Plan for ...` title", r"^#\s+Plan\b"),
        ArtefactSection("`## Overall Plan Metrics` table", PLAN_METRICS_HEADING),
        ArtefactSection("at least one `## Step ...` section", r"^##\s+Step\b"),
        ArtefactSection(
            "`### Strategy Rationale` (or a justification section)",
            r"^#{2,3}\s+(Strategy Rationale|.*Justification)",
        ),
    ),
    size_floor=800,
    required_metrics=PLAN_REQUIRED_METRICS,
    metrics_section=PLAN_METRICS_HEADING,
)

EXECUTION_METRICS = ("success", "entropy", "impact", "cost", "learning_value")

EXECUTION_SPEC = ArtefactSpec(
    name="execution notes",
    sections=(
        ArtefactSection("`# Execution Record: ...` title", r"^#\s+Execution\b"),
        ArtefactSection("`## Summary`", r"^##\s+Summary\b"),
        ArtefactSection("`## Steps Executed`", r"^##\s+Steps?\b"),
        ArtefactSection("`## Tests Run`", r"^##\s+(Tests|Verification)\b"),
        ArtefactSection("`## Actual Metrics` table", EXECUTION_METRICS_HEADING),
    ),
    size_floor=400,
    required_metrics=EXECUTION_METRICS,
    metric_suffixes=("_actual", "_pred"),
    metrics_section=EXECUTION_METRICS_HEADING,
)


def plan_write_protocol(path: str) -> str:
    """Closing instruction for planner prompts: write the plan file incrementally, keep the reply short."""
    return (
        "WRITE PROTOCOL (mandatory): create the plan file "
        f"`{path}` with your file-write tool now, before any long analysis. Write the header and the "
        "'## Overall Plan Metrics' table ('| metric | value |' with numeric values) first, then append each "
        "'## Step N' section as you finish it, then the strategy rationale, justification and safety sections. "
        "The file on disk is the deliverable; do not repeat the plan in your final message. When the file is complete, "
        "reply with one short confirmation line."
    )


def execution_write_protocol(path: str) -> str:
    """Closing instruction for executor prompts: create the execution notes first and extend them per step."""
    return (
        "WRITE PROTOCOL (mandatory): before changing any code, create the execution notes file "
        f"`{path}` with your file-write tool. Start it with a '# Execution Record: ...' title, a '## Summary' "
        "section and an '## Actual Metrics' table ('| metric | value |' with numeric success_actual, "
        "entropy_actual, impact_actual, cost_actual and learning_value_actual rows; update the values as you go). "
        "Append to a '## Steps Executed' section as each plan step completes, then add a '## Tests Run' section "
        "with the verification commands and their results. Keep your final message to one short confirmation line."
    )
