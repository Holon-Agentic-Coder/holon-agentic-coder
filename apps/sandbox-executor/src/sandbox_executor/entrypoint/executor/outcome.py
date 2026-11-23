"""The outcome of an executed plan: its classification, the checks on the agent's work, salvage and the ledger row.

The ledger writers, ``measure_executor_tests`` and ``converge_prettier`` are patch seams, read through the package
facade at call time.
"""

import json
import os
import sys
from typing import Any

import sandbox_executor.entrypoint.executor as executor_facade
from sandbox_executor.artefacts import ArtefactReport
from sandbox_executor.budgets import BudgetBreach
from sandbox_executor.entrypoint.changed_markdown import changed_markdown_files
from sandbox_executor.entrypoint.empty_diff_excerpt import report_empty_diff_output
from sandbox_executor.entrypoint.execution_workspace import format_skipped_scratch_message, unstage_sidecar_scratch
from sandbox_executor.entrypoint.executor.commands import _entrypoint_git_runner, _get_repo, is_action_logging_enabled
from sandbox_executor.entrypoint.executor.repair import _configure_executor_identity, _probe_git_repo, _repair_git_repo
from sandbox_executor.ledger import (
    ActionRecord,
    LedgerRewriteError,
    format_commit_trailers,
    generate_action_id,
    suppress_ledger_failures,
)
from sandbox_executor.redaction import redact_agent_secrets
from sandbox_executor.turns import AuthoringOutcome


def classify_execution_outcome(outcome: AuthoringOutcome) -> tuple[str, str, BudgetBreach | None, str]:
    """Execution status, summary, the budget breach to record (if any) and its reason for an authoring outcome.

    The *first* turn implements the plan; executor follow-ups (resume after a breach, re-prompt for missing sections)
    may only touch the execution notes. Therefore the outcome is decided from the first turn:

    - a breach of the implementation turn is a non-success, even when a notes-only resume made the notes usable: the
      code changes were cut off mid-implementation, so the breach is recorded and the partial code is salvaged into a
      ``partial work (failed)`` commit rather than recorded as a completed execution;
    - a breach confined to the notes-only re-prompt after a clean implementation turn never fails the execution: the
      code is complete, the breach is only logged and ``execution_notes_valid`` reports the notes as they are;
    - a non-zero first-turn exit counts as success only when the runner reported a structured truncation of its final
      message (``stopReason: length``) and the notes are complete; a generic exit 1 (auth or API errors)
      never does.
    """
    first = outcome.first
    report = outcome.report
    if first.breach is not None:
        # The resume (if any) breached too only when budget_failure is set; record the budget that fired last.
        breach = outcome.budget_failure or first.breach
        if outcome.follow_up == "resume" and outcome.budget_failure is not None:
            reason = "resume did not recover"
        else:
            reason = "the implementation turn did not finish (partial code and execution notes salvaged)"
        return "failure", f"Plan execution stopped: {breach.describe()}; {reason}", breach, reason
    if outcome.breach is not None:
        print(
            f"WARNING: the notes-only follow-up turn stopped ({outcome.breach.describe()}); the implementation turn "
            "finished, so the code changes are kept and the notes are reported as they are.",
            file=sys.stderr,
        )
    if first.returncode == 0:
        return "success", "Plan executed successfully", None, ""
    if first.truncated and report is not None and report.complete:
        print(
            f"Info: agent exited with code {first.returncode} (stop reason: {first.stop_reason}) after writing "
            "complete execution notes; treating the exit status as informational."
        )
        return "success", "Plan executed successfully", None, ""
    return "failure", f"Plan execution failed with exit code {first.returncode}", None, ""


def finalise_execution_notes(notes_path: str, report: ArtefactReport | None) -> bool:
    """Validate the execution notes the agent wrote and return True when they are usable.

    Validation is report-only for the executor: missing sections are logged and recorded in the ledger summary, but
    never discard the code changes the agent made. When the agent wrote no notes, the ledger summary records
    ``execution_notes_valid: false`` with the size of the implementation turn's output (``agent_output_bytes`` and
    ``agent_output_truncated``), not the output text; the executor deliberately does not synthesise an ``executions/``
    file itself, because the ledger summary remains the authoritative execution record.
    """
    if report is not None and report.exists:
        if not report.usable or report.missing_sections:
            print(f"WARNING: {report.describe()}", file=sys.stderr)
        return report.usable
    print(
        f"WARNING: agent did not write executions/{os.path.basename(notes_path)}; the execution ledger summary "
        "records execution_notes_valid=false and only the size of the agent output (agent_output_bytes), not its text.",
        file=sys.stderr,
    )
    return False


def _salvage_pathspec(notes_rel: str) -> list[str]:
    """Pathspec for everything in the working tree except the executions ledger and this run's notes file."""
    return ["--", ".", f":(exclude){executor_facade.EXECUTIONS_LEDGER_REL}", f":(exclude){notes_rel}"]


def _salvage_partial_work(
    repo_dir: str, exec_id: str, plan_branch: str, notes_rel: str, diff_base: str | None
) -> str | None:
    """Commit the agent's working-tree changes on a failed execution and return the partial head, if any.

    Tracked edits and deletions and untracked files that ``.gitignore`` does not exclude are committed in a commit of
    their own, ahead of the failure commit that carries the notes and the ledger row. Nothing is amended, squashed or
    reset. Returns the sha of HEAD when it differs from ``diff_base`` (the salvage commit or the agent's own last
    commit), else None: there was nothing to salvage.
    """
    repo = _get_repo(repo_dir)
    repo.add(_salvage_pathspec(notes_rel), all_files=True, check=False)
    if skipped := unstage_sidecar_scratch(repo):
        print(format_skipped_scratch_message(skipped), file=sys.stderr)
    staged = repo.diff(cached=True, quiet=True)
    if staged.returncode != 0:
        _configure_executor_identity(repo_dir)
        commit = repo.commit(f"execute: {exec_id} partial work (failed) for plan {plan_branch}", check=False)
        if commit.returncode != 0:
            left = repo.diff("--name-only", cached=True).stdout or ""
            print(
                "Warning: could not commit the partial work; these files are left uncommitted: "
                f"{redact_agent_secrets(', '.join(left.split()))}; reason: "
                f"{redact_agent_secrets((commit.stderr or commit.stdout or '').strip())}",
                file=sys.stderr,
            )
            # Unstage them, so the failure commit still carries only the notes and the ledger row.
            repo.reset(None, *_salvage_pathspec(notes_rel))
    head = repo.rev_parse("HEAD")
    sha = (head.stdout or "").strip() if head.returncode == 0 else ""
    return sha if sha and sha != diff_base else None


class CheckWorkSteps:
    """The `ExecutionRun` steps that check the agent's finished work and salvage a failed run's code.

    Relies on these `ExecutionRun` attributes: ``notes_path``, ``notes_rel``, ``notes_report``, ``repo_dir``,
    ``repo``, ``exec_branch``, ``plan_branch``, ``plan_tip``, ``exec_id``, ``intent_data``, ``executions_ledger``,
    ``action_origin``, ``agent_output_text`` and ``agent_output_bytes``; sets ``notes_valid``, ``exec_status``,
    ``failure_reason``, ``summary``, ``test_fields`` and ``salvaged_head``.
    """

    def check_work(self) -> None:
        """Check the agent's finished work before anything is staged.

        Validates the execution notes, requires a usable git repository (raises when it cannot be repaired), applies the
        empty-diff guard (which can turn a success into a failure), measures the project's tests and converges prettier
        on changed markdown.
        """
        self.notes_valid = finalise_execution_notes(self.notes_path, self.notes_report)
        self._require_healthy_repo()
        self._guard_empty_diff()
        # Measure the project's tests on the agent's finished work, before anything is staged or committed.
        self.test_fields = executor_facade.measure_executor_tests(self.repo_dir, self.repo, self.exec_status)
        self._converge_markdown()

    def _require_healthy_repo(self) -> None:
        """Repair benign git damage the agent left, or raise: the mounted worktree's git directory is never rebuilt."""
        is_healthy, probe_err = _probe_git_repo(self.repo_dir)
        if is_healthy:
            return
        repaired = _repair_git_repo(
            self.repo_dir, probe_err, default_branch=self.exec_branch, plan_branch=self.plan_branch
        )
        if not repaired:
            # Its .git pointer is read-only and the git directory is the host's: never re-initialise it.
            raise RuntimeError(
                f"the mounted worktree's git repository is unusable after the agent ran ({probe_err}); its "
                "files are kept in the host worktree"
            )

    def _guard_empty_diff(self) -> None:
        """Fail a successful run whose diff against the plan tip is empty or cannot be computed.

        Empty-diff guard (shared with holon flow and the review loop): an agent run that changes nothing outside
        holon-knowledge/ledger/, plans/ and executions/ is never recorded as success, and whatever it did change is
        committed only as failed partial work. Fail closed when the diff cannot be computed.
        """
        if self.exec_status != "success":
            return
        from sandbox_executor.git import (
            EMPTY_DIFF_FAILURE_REASON,
            EMPTY_DIFF_STATUS,
            empty_diff_summary,
            has_effective_diff,
        )

        diff_base = self.plan_tip
        try:
            if not diff_base:
                raise RuntimeError(f"tip of {self.plan_branch} is unknown")
            effective = has_effective_diff(self.repo_dir, diff_base, git_runner=_entrypoint_git_runner(self.repo_dir))
        except Exception as e:
            self.exec_status = EMPTY_DIFF_STATUS
            self.failure_reason = "diff_unverified"
            self.summary = (
                f"Could not verify the execution diff against {self.plan_branch}; treating it as a failure: "
                f"{redact_agent_secrets(str(e))}"
            )
        else:
            if not effective:
                self.exec_status = EMPTY_DIFF_STATUS
                self.failure_reason = EMPTY_DIFF_FAILURE_REASON
                self.summary = empty_diff_summary(self.plan_branch)
        if self.failure_reason:
            print(f"Error: {self.summary}", file=sys.stderr)
            report_empty_diff_output(self.failure_reason, self.agent_output_text, self.agent_output_bytes)

    def _converge_markdown(self) -> None:
        """Format modified and untracked markdown files with prettier; a formatting failure is only a warning."""
        try:
            if md_files := changed_markdown_files(self.repo):
                executor_facade.converge_prettier(md_files, repo_dir=self.repo_dir)
                if is_action_logging_enabled(self.intent_data):
                    with suppress_ledger_failures():
                        executor_facade.append_action_record(
                            self.executions_ledger,
                            ActionRecord(
                                action_id=generate_action_id(),
                                execution_id=self.exec_id,
                                event_type="doc_update",
                                action=f"Formatted {len(md_files)} markdown files with prettier",
                                category="doc",
                                labels=["formatting", "prettier"],
                                target=", ".join(md_files[:3]),
                                outcome="success",
                                action_origin=self.action_origin,
                            ),
                        )
        except LedgerRewriteError:
            raise
        except Exception as e:
            print(f"Warning: Prettier formatting failed: {redact_agent_secrets(str(e))}", file=sys.stderr)

    def salvage(self) -> None:
        """Commit a failed run's code changes as ``partial work (failed)``, recording the partial head.

        Salvage on failure, in two commits: first the agent's code changes ("partial work (failed)"), then the notes and
        the ledger row ("failed for plan"), so a failed execution never loses its code.
        """
        if self.exec_status != "success" and self.plan_tip:
            self.salvaged_head = _salvage_partial_work(
                self.repo_dir, self.exec_id, self.plan_branch, self.notes_rel, self.plan_tip
            )


class OutcomeSteps:
    """The `ExecutionRun` step that records an executed plan's summary row and chooses its commit message.

    Relies on these `ExecutionRun` attributes: ``stage_timer``, ``exec_id``, ``plan_branch``, ``args``, ``runner``,
    ``exec_status``, ``summary``, ``timestamp_str``, the ``agent_output_*`` fields, ``notes_rel``, ``notes_valid``,
    ``test_fields``, ``failure_reason``, ``salvaged_head``, the action provenance fields, ``ledger_dir``,
    ``executions_ledger`` and ``intent_data``; sets ``exec_entry`` and ``commit_msg``.
    """

    def record_ledger(self) -> None:
        """Append the execution's summary row to ``executions.jsonl`` and choose the commit message.

        A write failure other than `LedgerRewriteError` is only a warning. Also appends the ``git_mutation`` action
        record when action logging is on.
        """
        self.exec_entry = self._summary_row()
        try:
            os.makedirs(self.ledger_dir, exist_ok=True)
            with executor_facade.atomic_ledger_append(self.executions_ledger) as ef:
                ef.write(json.dumps(self.exec_entry) + "\n")
        except LedgerRewriteError:
            raise
        except Exception as e:
            print(f"Warning: Failed to write execution ledger row: {redact_agent_secrets(str(e))}", file=sys.stderr)

        self.commit_msg = self._execution_commit_message()
        self._record_commit_action()

    def _summary_row(self) -> dict[str, Any]:
        """The execution's ``executions.jsonl`` summary row."""
        stage_timer = self.stage_timer
        exec_entry = {
            "entry_type": "summary",
            "execution_id": self.exec_id,
            "plan_branch": self.plan_branch,
            "agent": self.args.agent_name,
            "agent_version": self.runner.get_version(),
            "model": self.args.model_name,
            "status": self.exec_status,
            "summary": self.summary,
            "created_at": self.timestamp_str,
            "agent_output_truncated": self.agent_output_truncated,
            "agent_output_bytes": self.agent_output_bytes,
            "ledger_revision": 1,
            "execution_notes": self.notes_rel,
            "execution_notes_valid": self.notes_valid,
            "elapsed_seconds": round(stage_timer.elapsed(), 1),
            **stage_timer.ledger_fields(),
        }
        exec_entry.update(self.test_fields)
        if self.failure_reason:
            exec_entry["failure_reason"] = self.failure_reason
        if self.salvaged_head:
            exec_entry["partial_head"] = self.salvaged_head
        return exec_entry

    def _execution_commit_message(self) -> str:
        """The ``completed`` or ``failed`` commit message, with provenance trailers on a review-driven execution."""
        if self.exec_status == "success":
            commit_msg = f"execute: {self.exec_id} completed for plan {self.plan_branch}"
        else:
            # The failure commit (ledger row and notes, after the partial-work commit) never reads as completed.
            commit_msg = f"execute: {self.exec_id} failed for plan {self.plan_branch}"
        trailers = format_commit_trailers(
            action_origin=self.action_origin,
            review_severity=self.review_severity,
            review_iteration=self.review_iteration,
            review_finding_ids=self.review_finding_ids,
        )
        if (
            self.action_origin != "initial_implementation"
            or self.review_severity
            or self.review_iteration
            or self.review_finding_ids
        ):
            commit_msg = f"{commit_msg}\n\n{trailers}"
        return commit_msg

    def _record_commit_action(self) -> None:
        """Append the ``git_mutation`` action record of the execution commit, when action logging is on."""
        if not is_action_logging_enabled(self.intent_data):
            return
        with suppress_ledger_failures():
            executor_facade.append_action_record(
                self.executions_ledger,
                ActionRecord(
                    action_id=generate_action_id(),
                    execution_id=self.exec_id,
                    event_type="git_mutation",
                    action=f"Committed execution changes: {self.commit_msg.splitlines()[0]}",
                    category="code",
                    labels=["commit", "git"],
                    target="HEAD",
                    outcome="success",
                    action_origin=self.action_origin,
                    review_severity=self.review_severity,
                    review_iteration=self.review_iteration,
                    review_finding_ids=self.review_finding_ids,
                ),
            )
