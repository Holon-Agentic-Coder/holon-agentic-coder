from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from sandbox_executor.agent_runner import AgentRunner, get_runner
from sandbox_executor.artefacts import ArtefactSection, ArtefactSpec
from sandbox_executor.budgets import Budgets, StageTimer, load_budgets
from sandbox_executor.redaction import redact_agent_secrets, redact_command_args
from sandbox_executor.review_failure import OnceSleeper, log_failed_turn, missing_report_reason, retry_wait
from sandbox_executor.review_request import (
    _PLACEHOLDER,
    ReviewRequest,
    _missing_inputs,
    _placeholder_values,
    build_scoped_diff,
    format_scoped_body,
)
from sandbox_executor.review_target import ReviewTarget
from sandbox_executor.scaffold import REVIEW_LOOP_TEMPLATE
from sandbox_executor.turns import AuthoringOutcome, TurnRunner, run_turn, supervise_authoring_turn

__all__ = [
    "DEFAULT_ENSEMBLE_SIZE",
    "ENSEMBLE_SIZE_HELP",
    "REVIEW_REPORT_SPEC",
    "REVIEW_STAGE",
    "SEVERITIES",
    "TRAILER_CLOSE",
    "TRAILER_OPEN",
    "ConsensusVerdict",
    "ParsedTrailer",
    "ReviewCounts",
    "ReviewFinding",
    "ReviewRequest",
    "ReviewerResult",
    "ReviewerStatus",
    "ReviewerVerdict",
    "TrailerError",
    "build_review_request",
    "build_reviewer_prompt",
    "decide_consensus",
    "format_agent_cmd_for_display",
    "parse_review_trailer",
    "render_review_trailer",
    "reviewer_report_rel",
    "run_ensemble",
    "run_reviewer",
]

logger = logging.getLogger(__name__)

DEFAULT_ENSEMBLE_SIZE = 3
ENSEMBLE_SIZE_HELP = (
    "Number of independent reviewer agent turns per iteration; approval needs every reviewer to approve with zero "
    "findings (default: 3)"
)
# why: one ensemble is one review iteration, so all of its reviewers share a single stage timer.
REVIEW_STAGE = "review"
SEVERITIES = ("critical", "important", "nit")
TRAILER_OPEN = "<!-- holon-review-trailer -->"
TRAILER_CLOSE = "<!-- /holon-review-trailer -->"
TEMPLATE_REL = "holon-config/prompts/review_loop.template.md"
REVIEW_ROOT_REL = "tmp/review"
REPORT_NAME = "report.md"
MAX_REASON_CHARS = 500
_AGENT_ARG_DISPLAY_LEN = 100


def format_agent_cmd_for_display(cmd: Sequence[str]) -> str:
    print_cmd = []
    for arg in redact_command_args(cmd):
        if len(arg) > _AGENT_ARG_DISPLAY_LEN:
            print_cmd.append(arg[:_AGENT_ARG_DISPLAY_LEN] + "...")
        else:
            print_cmd.append(arg)
    return " ".join(print_cmd)


_TRAILER_KEYS = frozenset({"verdict", *SEVERITIES, "findings"})
_FINDING_KEYS = frozenset({"severity", "file", "line", "summary"})

REVIEW_REPORT_SPEC = ArtefactSpec(
    name="review report",
    sections=(
        ArtefactSection(
            f"the closing review trailer block (`{TRAILER_OPEN}` ... `{TRAILER_CLOSE}`) as the final lines",
            r"^<!--\s*/holon-review-trailer\s*-->$",
        ),
    ),
    size_floor=80,
)


class ReviewerVerdict(StrEnum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ReviewerStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConsensusVerdict(StrEnum):
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    FAILED = "FAILED"


class TrailerError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    path: str
    line: int | None
    summary: str


@dataclass(frozen=True)
class ReviewCounts:
    critical: int = 0
    important: int = 0
    nit: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.important + self.nit


@dataclass(frozen=True)
class ParsedTrailer:
    verdict: ReviewerVerdict
    counts: ReviewCounts
    findings: tuple[ReviewFinding, ...] = ()


def build_review_request(  # noqa: PLR0913
    target: ReviewTarget,
    base_ref: str,
    iteration: int,
    pr_title: str,
    pr_body: str,
    repo_dir: str,
    *,
    review_paths: Sequence[str] | None = None,
) -> ReviewRequest:
    if not target.head_sha:
        raise RuntimeError(f"'{target.head_branch}' has no resolved head commit to review")
    diff_text, scoped = build_scoped_diff(repo_dir, base_ref, target.head_sha, review_paths=review_paths)
    body = format_scoped_body(target.body or pr_body, scoped)
    return ReviewRequest(repo_dir, target.head_sha, base_ref, target.title or pr_title, body, diff_text, iteration)


@dataclass(frozen=True)
class ReviewerResult:
    index: int
    agent: str
    model: str
    status: ReviewerStatus
    verdict: ReviewerVerdict | None
    counts: ReviewCounts
    findings: tuple[ReviewFinding, ...]
    # why: the report file is removed once the ensemble moves on, so report_text keeps the report in memory.
    report_path: str
    report_text: str | None = None
    failure_reason: str | None = None
    retry_after_seconds: float | None = None

    @property
    def reason(self) -> str | None:
        return self.failure_reason

    @classmethod
    def completed(  # noqa: PLR0913
        cls, index: int, agent: str, model: str, report_path: str, report_text: str, parsed: ParsedTrailer
    ) -> ReviewerResult:
        return cls(
            index,
            agent,
            model,
            ReviewerStatus.COMPLETED,
            parsed.verdict,
            parsed.counts,
            parsed.findings,
            report_path,
            report_text,
        )

    @classmethod
    def failed(cls, index: int, agent: str, model: str, report_path: str, reason: str) -> ReviewerResult:
        return cls(
            index,
            agent,
            model,
            ReviewerStatus.FAILED,
            None,
            ReviewCounts(),
            (),
            report_path,
            failure_reason=_bounded_reason(reason),
        )


def _bounded_reason(reason: str) -> str:
    text = redact_agent_secrets(reason) or "unknown failure"
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 3] + "..."


def render_review_trailer(parsed: ParsedTrailer) -> str:
    payload = {
        "verdict": str(parsed.verdict),
        "critical": parsed.counts.critical,
        "important": parsed.counts.important,
        "nit": parsed.counts.nit,
        "findings": [
            {"severity": item.severity, "file": item.path, "line": item.line, "summary": item.summary}
            for item in parsed.findings
        ],
    }
    return f"{TRAILER_OPEN}\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n{TRAILER_CLOSE}"


def parse_review_trailer(text: str) -> ParsedTrailer:
    obj = _load_trailer_object(_extract_trailer_block(text))
    verdict = _parse_verdict(obj["verdict"])
    counts = _parse_counts(obj)
    findings = _parse_findings(obj["findings"])
    _check_consistency(verdict, counts, findings)
    return ParsedTrailer(verdict=verdict, counts=counts, findings=findings)


def _extract_trailer_block(text: str) -> str:
    lines = [line.strip() for line in (text or "").rstrip().splitlines()]
    if not lines or lines[-1] != TRAILER_CLOSE:
        raise TrailerError("the report does not end with the closing trailer marker")
    openings = [index for index, line in enumerate(lines) if line == TRAILER_OPEN]
    if not openings:
        raise TrailerError("the report has no opening trailer marker")
    inner = lines[openings[-1] + 1 : -1]
    if TRAILER_CLOSE in inner:
        raise TrailerError("the trailer block contains more than one closing marker")
    return _strip_json_fence("\n".join(inner).strip())


def _strip_json_fence(inner: str) -> str:
    lines = inner.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1] == "```":
        return "\n".join(lines[1:-1])
    return inner


def _load_trailer_object(inner: str) -> dict[str, Any]:
    try:
        obj = json.loads(inner, object_pairs_hook=_reject_repeated_keys)
    except TrailerError:
        raise
    except json.JSONDecodeError as error:
        raise TrailerError(f"the trailer is not valid JSON: {error.msg}") from error
    # why: json raises these for an oversized integer or a too-deeply nested value.
    except (ValueError, RecursionError) as error:
        raise TrailerError(f"the trailer cannot be decoded ({type(error).__name__})") from error
    if not isinstance(obj, dict):
        raise TrailerError("the trailer must be one JSON object")
    if set(obj) != _TRAILER_KEYS:
        missing = sorted(_TRAILER_KEYS - set(obj))
        unknown = sorted(set(obj) - _TRAILER_KEYS)
        raise TrailerError(f"trailer keys are wrong (missing: {missing}, unknown: {unknown})")
    return obj


# why: plain JSON decoding silently keeps the last value of a repeated key.
def _reject_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise TrailerError(f"the trailer repeats the key {key!r}")
        obj[key] = value
    return obj


def _parse_verdict(value: Any) -> ReviewerVerdict:
    if not isinstance(value, str) or value not in tuple(ReviewerVerdict):
        raise TrailerError(f"unknown verdict {value!r}")
    return ReviewerVerdict(value)


# why: bool is an int subclass, so `True` must not pass as a count.
def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_counts(obj: Mapping[str, Any]) -> ReviewCounts:
    for name in SEVERITIES:
        if not _is_count(obj[name]):
            raise TrailerError(f"count {name!r} must be a non-negative integer, got {obj[name]!r}")
    return ReviewCounts(critical=obj["critical"], important=obj["important"], nit=obj["nit"])


def _parse_findings(raw: Any) -> tuple[ReviewFinding, ...]:
    if not isinstance(raw, list):
        raise TrailerError("findings must be a list")
    return tuple(_parse_finding(item) for item in raw)


def _parse_finding(item: Any) -> ReviewFinding:
    if not isinstance(item, dict) or set(item) != _FINDING_KEYS:
        raise TrailerError(f"each finding must be an object with exactly the keys {sorted(_FINDING_KEYS)}")
    if item["severity"] not in SEVERITIES:
        raise TrailerError(f"unknown finding severity {item['severity']!r}")
    if not all(isinstance(item[key], str) and item[key].strip() for key in ("file", "summary")):
        raise TrailerError("a finding needs a non-empty file and summary")
    if item["line"] is not None and not _is_count(item["line"]):
        raise TrailerError(f"finding line must be a non-negative integer or null, got {item['line']!r}")
    return ReviewFinding(severity=item["severity"], path=item["file"], line=item["line"], summary=item["summary"])


def _check_consistency(verdict: ReviewerVerdict, counts: ReviewCounts, findings: tuple[ReviewFinding, ...]) -> None:
    for name in SEVERITIES:
        tally = sum(1 for item in findings if item.severity == name)
        if getattr(counts, name) != tally:
            raise TrailerError(f"count {name!r} is {getattr(counts, name)} but {tally} such findings are listed")
    if verdict is ReviewerVerdict.APPROVED and counts.total:
        raise TrailerError("an APPROVED verdict must report zero findings")


def load_review_template(repo_dir: str) -> str:
    path = os.path.join(repo_dir, TEMPLATE_REL)
    if not os.path.isfile(path):
        return REVIEW_LOOP_TEMPLATE
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def build_reviewer_prompt(template_text: str, request: ReviewRequest, report_rel: str) -> str:
    values = _placeholder_values(request)
    body = _PLACEHOLDER.sub(lambda match: values[match.group(1)], template_text)
    return f"{body.rstrip()}\n\n{_missing_inputs(template_text, request)}{reviewer_contract(report_rel)}"


_REVIEWER_PROHIBITIONS = (
    "Write no file other than the report path. Make no GitHub write of any kind: no `gh pr review`, "
    "`gh pr comment`, `gh pr edit` or `gh api` call, and no merge or auto-merge. Append to no ledger under "
    "`holon-knowledge/ledger/`. Spawn no subagents, and write nothing under `tmp/review/` except the report path."
)


def reviewer_contract(report_rel: str) -> str:
    clean = ParsedTrailer(ReviewerVerdict.APPROVED, ReviewCounts())
    finding = ReviewFinding("nit", "path/to/file.py", 12, "Short description of the issue and the fix.")
    changes = ParsedTrailer(ReviewerVerdict.CHANGES_REQUESTED, ReviewCounts(nit=1), (finding,))
    return (
        "REVIEWER TURN CONTRACT (mandatory): review only the change diff and change description above. "
        f"Write your full review report with your file-write tool to `{report_rel}`. The report must end with "
        "the review trailer block as its final lines: one JSON object between the two marker lines, with exactly "
        "the keys verdict (APPROVED or CHANGES_REQUESTED), critical, important and nit (non-negative integers) "
        "and findings (a list of objects with exactly the keys severity (critical, important or nit), file, line "
        "(an integer or null) and summary). Each count must equal the number of findings with that severity, and "
        "APPROVED requires every count to be zero. No key may appear twice in any object. "
        "For this turn the block supersedes the line-based on-disk trailer described above. Do not modify code, "
        "commit, push, run `gh pr merge` or enable auto-merge, and do not read any other file under "
        f"`{REVIEW_ROOT_REL}/`. {_REVIEWER_PROHIBITIONS} This contract overrides any conflicting instruction in "
        f"the loop template above.\n\nFinal lines of a clean report:\n\n{render_review_trailer(clean)}\n\n"
        f"Final lines of a report with one finding:\n\n{render_review_trailer(changes)}\n\n"
        "When the report is complete, reply with one short confirmation line."
    )


# why: the index becomes part of a scratch path, so anything but a non-negative int could steer it.
def _require_index(value: Any, name: str) -> int:
    if not _is_count(value):
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _iteration_dir_rel(iteration: int) -> str:
    return f"{REVIEW_ROOT_REL}/iter-{_require_index(iteration, 'iteration')}"


def reviewer_report_rel(iteration: int, index: int) -> str:
    return f"{_iteration_dir_rel(iteration)}/reviewer-{_require_index(index, 'index')}/{REPORT_NAME}"


def _review_root(repo_dir: str) -> str:
    expected = os.path.join(os.path.realpath(repo_dir), REVIEW_ROOT_REL)
    root = os.path.realpath(os.path.join(repo_dir, REVIEW_ROOT_REL))
    if root != expected:
        raise ValueError(
            f"review scratch root {REVIEW_ROOT_REL}/ does not resolve to {REVIEW_ROOT_REL}/ under the repository"
            f" directory (a symlinked tmp or {REVIEW_ROOT_REL} is the likely cause)"
        )
    return root


def _contained_path(repo_dir: str, rel: str) -> str:
    root = _review_root(repo_dir)
    path = os.path.realpath(os.path.join(repo_dir, rel))
    if not path.startswith(root + os.sep):
        raise ValueError(f"review scratch path {rel!r} escapes {REVIEW_ROOT_REL}/")
    return path


def _remove_scratch(repo_dir: str, rel: str) -> None:
    path = _contained_path(repo_dir, rel)
    if os.path.lexists(path):
        shutil.rmtree(path)


def _prepare_scratch(repo_dir: str, rel: str) -> str:
    _remove_scratch(repo_dir, rel)
    path = _contained_path(repo_dir, rel)
    os.makedirs(path)
    return path


def _write_turn_inputs(scratch: str, prompt: str, request: ReviewRequest) -> tuple[str, str]:
    prompt_file = os.path.join(scratch, "prompt.md")
    intent_file = os.path.join(scratch, "request.json")
    with open(prompt_file, "w", encoding="utf-8") as handle:
        handle.write(prompt)
    with open(intent_file, "w", encoding="utf-8") as handle:
        json.dump({"head_sha": request.head_sha, "base_ref": request.base_ref, "iteration": request.iteration}, handle)
    return prompt_file, intent_file


def _read_report(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


RunnerFactory = Callable[[str], AgentRunner]


# why: SystemExit from the runner factory or credential validation must fail only this reviewer.
def run_reviewer(  # noqa: PLR0913
    request: ReviewRequest,
    index: int,
    agent: str,
    model: str,
    *,
    template_text: str | None = None,
    turn_runner: TurnRunner = run_turn,
    runner_factory: RunnerFactory = get_runner,
    budgets: Budgets | None = None,
    stage_timer: StageTimer | None = None,
) -> ReviewerResult:
    report_rel = reviewer_report_rel(request.iteration, index)
    budgets = budgets or load_budgets(os.environ)
    timer = stage_timer or StageTimer(REVIEW_STAGE, budgets.stage_seconds(REVIEW_STAGE))
    template = template_text if template_text is not None else load_review_template(request.repo_dir)
    try:
        outcome = _supervise_reviewer_turn(
            request, report_rel, agent, model, template, turn_runner, runner_factory, budgets, timer
        )
    except (Exception, SystemExit) as error:
        reason = f"reviewer turn raised {type(error).__name__}: {error}"
        return _logged(ReviewerResult.failed(index, agent, model, report_rel, reason))
    return _logged(_judged_report(outcome, request.repo_dir, report_rel, index, agent, model))


def _supervise_reviewer_turn(  # noqa: PLR0913
    request: ReviewRequest,
    report_rel: str,
    agent: str,
    model: str,
    template: str,
    turn_runner: TurnRunner,
    runner_factory: RunnerFactory,
    budgets: Budgets,
    timer: StageTimer,
) -> AuthoringOutcome:
    scratch = _prepare_scratch(request.repo_dir, os.path.dirname(report_rel))
    prompt = build_reviewer_prompt(template, request, report_rel)
    prompt_file, intent_file = _write_turn_inputs(scratch, prompt, request)
    runner: AgentRunner | None = None
    try:
        runner = runner_factory(agent)
        build_cmd = _command_builder(runner, model, prompt_file, intent_file)
        outcome = supervise_authoring_turn(
            build_cmd,
            prompt,
            artefact_path=os.path.join(scratch, REPORT_NAME),
            artefact_rel=report_rel,
            spec=REVIEW_REPORT_SPEC,
            stage=REVIEW_STAGE,
            turn_budgets=budgets.turn,
            stage_timer=timer,
            run=turn_runner,
            modify_code=False,
            context=prompt,
            cwd=request.repo_dir,
            stdin_file=prompt_file if getattr(runner, "uses_stdin", False) else None,
            prompt_file=prompt_file,
        )
        _log_turn_completion(outcome)
        return outcome
    finally:
        if runner is not None:
            runner.cleanup_ephemeral_credentials()


def _command_builder(runner: AgentRunner, model: str, prompt_file: str, intent_file: str) -> Callable[[str], list[str]]:
    def build_cmd(prompt_text: str) -> list[str]:
        cmd = runner.build_cmd(model, prompt_file, intent_file, prompt_text)
        formatted = format_agent_cmd_for_display(cmd)
        logger.info("Running command: %s", formatted)
        print(f"Running command: {formatted}")
        return cmd

    return build_cmd


def _log_turn_completion(outcome: AuthoringOutcome) -> None:
    if not outcome.turns:
        return
    result = outcome.last
    report = outcome.report
    if report is not None and report.usable:
        if result.returncode != 0 or result.truncated:
            logger.info(
                "Info: reviewer agent exited with code %d (stop reason: %s) after writing a valid review report; "
                "treating the exit status as informational.",
                result.returncode,
                result.stop_reason or "unknown",
            )
    elif result.returncode != 0:
        log_failed_turn(logger, result)


def _missing_report_reason(outcome: AuthoringOutcome) -> str:
    return missing_report_reason(outcome.last) if outcome.turns else "the reviewer wrote no report"


def _turn_failure(outcome: AuthoringOutcome, *, report_missing: bool = False) -> str | None:
    # why: a breach on any turn fails the reviewer even with a parseable report: a cut-short turn is no finished review.
    breach = outcome.breach
    if breach is not None:
        return f"review turn cut short: {breach.describe()}"
    if report_missing:
        return _missing_report_reason(outcome)
    return None


def _judged_report(  # noqa: PLR0913
    outcome: AuthoringOutcome, repo_dir: str, report_rel: str, index: int, agent: str, model: str
) -> ReviewerResult:
    try:
        return _result_from_outcome(outcome, repo_dir, report_rel, index, agent, model)
    # why: an unexpected error judging one report fails only that reviewer; KeyboardInterrupt and SystemExit propagate.
    except Exception as error:
        reason = f"judging the review report raised {type(error).__name__}: {error}"
        return ReviewerResult.failed(index, agent, model, report_rel, reason)


def _result_from_outcome(  # noqa: PLR0913
    outcome: AuthoringOutcome, repo_dir: str, report_rel: str, index: int, agent: str, model: str
) -> ReviewerResult:
    failure = _turn_failure(outcome)
    if failure is not None:
        return ReviewerResult.failed(index, agent, model, report_rel, failure)
    text = _read_report(os.path.join(repo_dir, report_rel))
    if text is None:
        reason = _turn_failure(outcome, report_missing=True) or "the reviewer wrote no report"
        wait = retry_wait(outcome.last, os.environ) if outcome.breach is None and outcome.turns else None
        return replace(ReviewerResult.failed(index, agent, model, report_rel, reason), retry_after_seconds=wait)
    try:
        parsed = parse_review_trailer(text)
    except TrailerError as error:
        return ReviewerResult.failed(index, agent, model, report_rel, f"invalid review trailer: {error}")
    return ReviewerResult.completed(index, agent, model, report_rel, text, parsed)


# why: only index, agent, status and counts are logged, never prompt or report content.
def _logged(result: ReviewerResult) -> ReviewerResult:
    logger.info(
        "Reviewer %d (%s) %s: critical %d, important %d, nit %d",
        result.index,
        result.agent,
        result.status,
        result.counts.critical,
        result.counts.important,
        result.counts.nit,
    )
    return result


# why: reviewers run one after another and earlier scratch is removed first, so no reviewer sees another's output;
# why: a reviewer that cannot be isolated is recorded FAILED, so the consensus fails closed.
def run_ensemble(
    request: ReviewRequest,
    *,
    agent: str,
    model: str,
    size: int = DEFAULT_ENSEMBLE_SIZE,
    sleep: Callable[[float], None] = time.sleep,
    **seams: Any,
) -> list[ReviewerResult]:
    if size < 1:
        return []
    seams = _shared_seams(request, seams)
    iteration_rel = _iteration_dir_rel(request.iteration)
    clear_error = _clear_iteration(request.repo_dir, iteration_rel)
    if clear_error is not None:
        return [_undispatched(request, index, agent, model, clear_error) for index in range(1, size + 1)]
    results: list[ReviewerResult] = []
    once = OnceSleeper(sleep)
    for index in range(1, size + 1):
        isolation_error = _isolate(request.repo_dir, results, consequence="stopping the ensemble", level=logging.ERROR)
        if isolation_error is not None:
            results.append(_undispatched(request, index, agent, model, isolation_error))
            continue
        run = functools.partial(run_reviewer, request, index, agent, model, **seams)
        result = run()
        if (wait := result.retry_after_seconds) is not None and once.try_wait(wait):
            logger.warning("Reviewer %d hit a quota; waiting %.0f s for the reset before one retry.", index, wait)
            seams["stage_timer"].credit(wait)
            _remove_scratch(request.repo_dir, os.path.dirname(result.report_path))
            result = run()
        results.append(result)
    _remove_leftovers(request.repo_dir, results, iteration_rel)
    return results


def _shared_seams(request: ReviewRequest, seams: Mapping[str, Any]) -> dict[str, Any]:
    shared = dict(seams)
    budgets = shared.get("budgets") or load_budgets(os.environ)
    shared["budgets"] = budgets
    if shared.get("stage_timer") is None:
        shared["stage_timer"] = StageTimer(REVIEW_STAGE, budgets.stage_seconds(REVIEW_STAGE))
    if shared.get("template_text") is None:
        shared["template_text"] = load_review_template(request.repo_dir)
    return shared


def _clear_iteration(repo_dir: str, iteration_rel: str) -> str | None:
    try:
        _remove_scratch(repo_dir, iteration_rel)
    except (OSError, ValueError) as error:
        logger.error(
            "Could not clear %s before the ensemble (%s); no reviewer is dispatched.",
            iteration_rel,
            type(error).__name__,
        )
        return f"isolation from an earlier run could not be guaranteed: {error}"
    return None


def _undispatched(request: ReviewRequest, index: int, agent: str, model: str, reason: str) -> ReviewerResult:
    report_rel = reviewer_report_rel(request.iteration, index)
    return _logged(ReviewerResult.failed(index, agent, model, report_rel, reason))


# why: a leftover that cannot be removed is logged and left in place; the results already stand.
def _remove_leftovers(repo_dir: str, results: Sequence[ReviewerResult], iteration_rel: str) -> None:
    try:
        _remove_scratch(repo_dir, iteration_rel)
    except (OSError, ValueError) as error:
        logger.warning(
            "Could not remove %s after the ensemble (%s); earlier reports may remain.",
            iteration_rel,
            type(error).__name__,
        )
    _isolate(repo_dir, results, consequence="it may remain", level=logging.WARNING)


def _isolate(repo_dir: str, earlier: Sequence[ReviewerResult], *, consequence: str, level: int) -> str | None:
    for result in earlier:
        scratch_rel = os.path.dirname(result.report_path)
        try:
            _remove_scratch(repo_dir, scratch_rel)
        except (OSError, ValueError) as error:
            logger.log(level, "Could not remove reviewer %d's scratch directory; %s.", result.index, consequence)
            return f"isolation from earlier reviewers could not be guaranteed: {error}"
        if os.path.lexists(os.path.join(repo_dir, result.report_path)):
            return "isolation from earlier reviewers could not be guaranteed: an earlier report is still present"
    return None


# why: unanimity with no majority vote: a single nit blocks, and unknown mergeability or CI is never clean.
def decide_consensus(
    results: Sequence[ReviewerResult], configured_size: int, mergeable: bool | None, ci_green: bool | None
) -> ConsensusVerdict:
    if _ensemble_failed(results, configured_size):
        return ConsensusVerdict.FAILED
    if _any_reviewer_objects(results) or mergeable is not True or ci_green is not True:
        return ConsensusVerdict.CHANGES_REQUESTED
    return ConsensusVerdict.APPROVED


# why: past this check at least one reviewer completed, so APPROVED never comes from zero completed reviewers.
def _ensemble_failed(results: Sequence[ReviewerResult], configured_size: int) -> bool:
    if configured_size < 1 or not results or len(results) != configured_size:
        return True
    return any(result.status is not ReviewerStatus.COMPLETED for result in results)


def _any_reviewer_objects(results: Sequence[ReviewerResult]) -> bool:
    return any(
        result.verdict is not ReviewerVerdict.APPROVED or result.counts.total or result.findings for result in results
    )


def serialize_reviewer_result(result: ReviewerResult) -> dict[str, Any]:
    findings_list = [
        {"severity": f.severity, "file": f.path, "line": f.line, "summary": f.summary} for f in result.findings
    ]
    return {
        "index": result.index,
        "agent": result.agent,
        "model": result.model,
        "status": str(result.status),
        "verdict": str(result.verdict) if result.verdict else None,
        "counts": {
            "critical": result.counts.critical,
            "important": result.counts.important,
            "nit": result.counts.nit,
        },
        "findings": findings_list,
        "report_path": result.report_path,
        "failure_reason": result.failure_reason,
    }


def _deserialize_finding(item: Mapping[str, Any]) -> ReviewFinding:
    sev = str(item.get("severity", "")).lower()
    if sev not in ("critical", "important", "nit"):
        raise ValueError(f"Invalid finding severity: {sev}")
    file_path = str(item.get("file") or item.get("path") or "").strip()
    if not file_path:
        raise ValueError("Finding missing target file path")
    raw_line = item.get("line")
    line = int(raw_line) if raw_line is not None and not isinstance(raw_line, bool) else None
    summary = str(item.get("summary") or "").strip()
    return ReviewFinding(severity=sev, path=file_path, line=line, summary=summary)


def _deserialize_counts(data: Mapping[str, Any]) -> ReviewCounts:
    def _parse_cnt(val: Any) -> int:
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return 0
        return max(0, int(val))

    return ReviewCounts(
        critical=_parse_cnt(data.get("critical")),
        important=_parse_cnt(data.get("important")),
        nit=_parse_cnt(data.get("nit")),
    )


def deserialize_reviewer_result(data: Mapping[str, Any]) -> ReviewerResult:
    raw_status = str(data.get("status", "")).upper()
    try:
        status = ReviewerStatus(raw_status)
    except ValueError as exc:
        raise ValueError(f"Invalid reviewer status: {raw_status}") from exc

    raw_verdict = data.get("verdict")
    verdict: ReviewerVerdict | None = None
    if raw_verdict:
        try:
            verdict = ReviewerVerdict(str(raw_verdict).upper())
        except ValueError as exc:
            raise ValueError(f"Invalid reviewer verdict: {raw_verdict}") from exc

    findings = tuple(_deserialize_finding(f) for f in data.get("findings", []))
    counts = _deserialize_counts(data.get("counts") or {})

    index = int(data.get("index", 1))
    agent = str(data.get("agent") or "unknown")
    model = str(data.get("model") or "unknown")
    report_path = str(data.get("report_path") or "")
    failure_reason = str(data.get("failure_reason") or "") or None

    if status is ReviewerStatus.COMPLETED:
        if verdict is None:
            raise ValueError("Completed reviewer result missing verdict")
        parsed = ParsedTrailer(verdict=verdict, counts=counts, findings=findings)
        return ReviewerResult.completed(index, agent, model, report_path, "", parsed)

    return ReviewerResult.failed(index, agent, model, report_path, failure_reason or "unknown failure")


def serialize_review_outcome(
    reviewers: Sequence[ReviewerResult],
    verdict: ConsensusVerdict | None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ensemble_verdict": str(verdict) if verdict else None,
        "reviewers": [serialize_reviewer_result(r) for r in reviewers],
        "error": error,
    }


def deserialize_review_outcome(
    data: Mapping[str, Any],
) -> tuple[list[ReviewerResult], ConsensusVerdict | None, str | None]:
    error = data.get("error")
    raw_verdict = data.get("ensemble_verdict")
    verdict: ConsensusVerdict | None = None
    if raw_verdict:
        try:
            verdict = ConsensusVerdict(str(raw_verdict).upper())
        except ValueError:
            verdict = ConsensusVerdict.FAILED

    reviewers: list[ReviewerResult] = []
    for item in data.get("reviewers", []):
        try:
            reviewers.append(deserialize_reviewer_result(item))
        except (ValueError, TypeError, KeyError) as exc:
            return [], ConsensusVerdict.FAILED, f"Malformed reviewer result in transport: {exc}"

    return reviewers, verdict, str(error) if error else None
