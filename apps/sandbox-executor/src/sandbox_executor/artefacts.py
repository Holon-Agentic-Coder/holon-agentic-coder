import os
import re
from dataclasses import dataclass, field

_METRIC_ROW = re.compile(r"\|\s*([a-zA-Z_0-9]+)\s*\|\s*([0-9\.\-]+)\s*\|")


def _find_heading(lines: list[str], pattern: str) -> int | None:
    regex = re.compile(pattern, re.IGNORECASE)
    return next((index for index, line in enumerate(lines) if regex.match(line.strip())), None)


def _strip_metric_suffix(name: str, suffixes: tuple[str, ...]) -> str:
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name.strip()


def _extract_section_lines(lines: list[str], heading: str) -> list[str]:
    start = _find_heading(lines, heading)
    if start is None:
        return lines
    section: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        section.append(line)
    return section


@dataclass(frozen=True)
class ArtefactSection:
    label: str
    pattern: str

    def present_in(self, content: str) -> bool:
        return _find_heading(content.splitlines(), self.pattern) is not None


@dataclass(frozen=True)
class ArtefactSpec:
    name: str
    sections: tuple[ArtefactSection, ...]
    size_floor: int
    required_metrics: tuple[str, ...] = ()
    metric_suffixes: tuple[str, ...] = ("_pred",)
    metrics_section: str | None = None


@dataclass
class ArtefactReport:
    path: str
    spec: ArtefactSpec
    exists: bool = False
    size: int = 0
    missing_sections: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    missing_metrics: list[str] = field(default_factory=list)

    @property
    def size_ok(self) -> bool:
        return self.exists and self.size >= self.spec.size_floor

    @property
    def metrics_ok(self) -> bool:
        return self.exists and not self.missing_metrics

    @property
    def usable(self) -> bool:
        return self.size_ok and self.metrics_ok

    @property
    def complete(self) -> bool:
        return self.usable and not self.missing_sections

    def missing_labels(self) -> list[str]:
        labels = list(self.missing_sections)
        if self.missing_metrics:
            labels.append("metrics table rows: " + ", ".join(self.missing_metrics))
        if self.exists and not self.size_ok and not labels:
            labels.append(f"substantive content (file is {self.size} bytes, at least {self.spec.size_floor} expected)")
        return labels

    def describe(self) -> str:
        if not self.exists:
            return f"{self.spec.name} artefact {self.path} does not exist"
        state = "complete" if self.complete else "usable" if self.usable else "invalid"
        missing = self.missing_labels()
        suffix = f"; missing: {'; '.join(missing)}" if missing else ""
        return f"{self.spec.name} artefact {self.path} is {state} ({self.size} bytes){suffix}"


def parse_metrics(content: str, suffixes: tuple[str, ...] = ("_pred",)) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in content.splitlines():
        match = _METRIC_ROW.search(line)
        if not match:
            continue
        raw_name = match.group(1).strip()
        try:
            value = float(match.group(2).strip())
        except ValueError:
            continue
        metrics[_strip_metric_suffix(raw_name, suffixes)] = value
    return metrics


def parse_section_metrics(
    content: str, heading: str | None, suffixes: tuple[str, ...] = ("_pred",)
) -> dict[str, float]:
    lines = content.splitlines()
    if heading is not None:
        lines = _extract_section_lines(lines, heading)
    return parse_metrics("\n".join(lines), suffixes)


def plan_metrics(content: str) -> dict[str, float]:
    return parse_section_metrics(content, PLAN_SPEC.metrics_section, PLAN_SPEC.metric_suffixes)


def validate_artefact(path: str, spec: ArtefactSpec) -> ArtefactReport:
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
    return (
        "WRITE PROTOCOL (mandatory): create the plan file "
        f"`{path}` with your file-write tool now, before any long analysis. Write the header and the "
        "'## Overall Plan Metrics' table ('| metric | value |' with numeric values) first, then append each "
        "'## Step N' section as you finish it, then the strategy rationale, justification and safety sections. "
        "The file on disk is the deliverable; do not repeat the plan in your final message. When the file is complete, "
        "reply with one short confirmation line."
    )


def execution_write_protocol(path: str) -> str:
    return (
        "WRITE PROTOCOL (mandatory): before changing any code, create the execution notes file "
        f"`{path}` with your file-write tool. Start it with a '# Execution Record: ...' title, a '## Summary' "
        "section and an '## Actual Metrics' table ('| metric | value |' with numeric success_actual, "
        "entropy_actual, impact_actual, cost_actual and learning_value_actual rows; update the values as you go). "
        "Append to a '## Steps Executed' section as each plan step completes, then add a '## Tests Run' section "
        "with the verification commands and their results. Keep your final message to one short confirmation line."
    )
