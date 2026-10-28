# Execution Record: E-1793223966-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793223729-claude-agent-claude-opus-5-5` for intent
`I-1793223711-review-loop-wire-real-ensemble`: wire the real reviewer ensemble into the Stage 4 review loop and remove
the fabricated consensus report. All eight steps were carried out on this execution branch. The routing probe chose the
pre-registered FALLBACK: the `review-loop` role container clones rather than mounts the workspace and its ledger rows
never reach the host, so host-side Stage 4 refuses to run reviewer agent turns outside a sandbox and the routing work is
recorded as a follow-up. `run_review_loop()` now decides every iteration with `run_ensemble()` and `decide_consensus()`
on the real diff, writes per-reviewer `ACT-REV` and per-finding `ACT-FIND` rows, writes no `ACT-RES` row and stops at
the first iteration that is not approved. The fabricated report was replaced by `review_report.py`, which renders only
real reviewer data. `uv run task lint`, `uv run task lint-docs` and `uv run task test` (2,659 passed) all pass;
`flow.py` went from 5107 to 5103 lines and `uv.lock` is unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 9.8   |
| impact_actual         | 85.0  |
| cost_actual           | 19.0  |
| learning_value_actual | 6.5   |

## Steps Executed

1. **Step 1 (routing probe): FALLBACK.** Evidence: `dispatch_role_agent` / `run_docker_container` accept any role and
   would launch the `review-loop` role (image from `image_for_agent`), and `holon review-loop` already builds the
   positional arguments (target, agent, model, `--max-iterations`, `--ensemble-size`, `--no-auto-pr`). But
   `run_docker_container` mounts only SSH, session, CA, proxy and (for the intent creator) the intent file: the host
   workspace is never mounted, and `entrypoint/review_loop.py` clones the head branch into the container's own workspace
   (`docker run --rm`). Neither the entrypoint nor `run_review_loop()` commits or pushes `executions.jsonl` (the
   documented known gap), so every `ACT-REV` / `ACT-FIND` row the container records is lost with the container. The host
   could read only the exit code; getting the per-reviewer evidence back needs a new transport (mount, push or artefact
   copy), which the decision rule rules out. Decision: FALLBACK, within the time box and with no Docker or gh call.
   **Follow-up:** route Stage 4 reviewer turns into the `review-loop` role container with a result transport for the
   ledger rows (for example the container committing and pushing its `executions.jsonl` rows to a review branch the host
   fetches), then let host Stage 4 interpret the container exit code.
2. **Step 2 (report module).** Added `apps/sandbox-executor/src/sandbox_executor/review_report.py` (214 lines):
   `resolve_report_title` (intent title, else `ReviewTarget.title`, else `PR #<n>`, else the branch),
   `render_reviewer_line` (index, agent, model, status, verdict, `critical/important/nit` counts; a FAILED reviewer
   shows `FAILED` and its redacted reason), `render_finding_lines`, the CI and mergeability lines from the probe data,
   the moved PR-link / target-commit helpers and
   `build_pr_review_consensus_report(repo_dir, target, consensus_data, reviewers=(), title=None)`. Untrusted reviewer
   text is redacted, collapsed to one line, stripped of backticks and angle brackets and bounded. Deleted from
   `flow.py`: the old report body, the persona matrix, the boiler-plate positive findings, the `hashlib.sha256` reviewer
   ids, the copied-vote loop, the `N-Agent` wording, the hard-coded fallback title and `import hashlib`. `flow.py`
   imports `build_pr_review_consensus_report` so existing imports work; `post_pr_review_consensus()` gained keyword-only
   `reviewers` and `title`. The `review_ensemble.py` docstring now describes the wiring.
3. **Step 3 (wiring).** `run_review_loop()` gained the keyword-only seam `run_ensemble_fn` (default: the module-level
   `run_ensemble`, looked up at call time). The target is resolved once before the loop (`_resolve_reviewed_target`; an
   error fails closed with status `failed`, no iteration, no post). New `review_ensemble.build_review_request` builds
   the request from `git diff <base>...<head_sha>` excluding `git.EMPTY_DIFF_EXCLUDES` (a failing diff raises, so the
   iteration fails). Each iteration probes mergeability and CI, runs the ensemble (`_ensemble_iteration`) with
   `context.agent`, `context.model` and `ensemble_size`, then
   `decide_consensus(..., mergeable=None if missing_ref else not is_dirty, ci_green=_ci_green(...))` (unknown CI is
   `None`, never green). Rows: the per-iteration `ACT-REV` evaluation row keeps its historical shape; one `ACT-REV` row
   per reviewer and one `ACT-FIND` row per reviewer finding and per `FIND-DIRTY` / `FIND-REF` / `FIND-CI` infrastructure
   finding, all in the historical key order with new keys appended before `created_at` (`_record_reviewer_rows`,
   `_record_infrastructure_findings`). The `ACT-RES` resolver block is deleted. The loop stops after the first
   non-APPROVED iteration; statuses map through `_loop_status` (`approved`, `failed`, `conflicted_dirty`,
   `not_measured`, `changes_requested`, `rejected`); the result and `consensus_data` carry `ensemble_verdict` and
   serialised `reviewers` (never report text). Dry runs never dispatch reviewers and stop after one iteration.
   `flow.py`: 5107 lines before, 5103 after.
4. **Step 4 (FALLBACK guard and preflight).** `reviewers_sandboxed()` reuses `agent_defaults.harness_detection_disabled`
   (`SANDBOX_GUARD_VARS`: `HOLON_ROLE`, `HOLON_IN_SANDBOX`; the `USER=holon` heuristic is not trusted). Outside a
   sandbox and not a dry run, `run_review_loop()` returns `failed` with the error "Refusing to run reviewer agent turns
   on the host: Stage 4 reviewers run only inside the sandbox (use 'holon review-loop' without --local, or run inside
   the review-loop container)" before any PR, iteration, reviewer row or post; `run_review_stage()` surfaces it as
   "Autonomous review halted: ...". The `review-loop` container sets `HOLON_ROLE`, so the guard passes there.
   `cli._AGENT_FLOW_STAGES` now includes `review` with a rewritten comment, so a flow resumed at review preflights the
   runner; `flow_dispatches_agent()` and the preflight call site needed no further change.
5. **Step 5 (posting, exit codes, CLI).** Posting happens only when at least one reviewer completed and no
   infrastructure step failed; otherwise the reason is logged and no `ACT-POST` row is written. `review_loop_approved()`
   (0 only for `ensemble_verdict == APPROVED` and an approved consensus) drives both `run_review_loop_cli()` and the
   container entrypoint; both print one summary line per reviewer via `review_summary_lines()`. `ENSEMBLE_SIZE_HELP` in
   `review_ensemble.py` holds the exact help text used by `holon review-loop`, the entrypoint and the new
   `holon flow --ensemble-size` (no equivalent existed; validated `>= 1`; threaded through the new
   `FlowContext.review_ensemble_size` field, default 3, checkpointed additively via `.get` with no schema version bump,
   inherited by child contexts). `run_review_stage()` passes it to the loop and reports reviewer objections as
   "Autonomous review requested changes: ...". `grep` confirms no `gh pr merge`, `--auto` or auto-merge call.
6. **Step 6 (tests, fakes only).** New `apps/sandbox-executor/tests/review_fakes.py` (`FakeEnsemble`, `clean_reviewer`,
   `objecting_reviewer`, `failed_reviewer`, `install_fake_reviewers`). `test_flow.py` and `test_review_loop.py` gained
   an autouse fixture that sets `HOLON_IN_SANDBOX=1` and replaces target resolution, the base ref, the diff and the
   ensemble with clean fakes, so the existing tests keep exercising mergeability and CI with three clean reviewers;
   tests of the new behaviour override it. New tests in `test_review_loop.py`: three clean reviewers plus mergeable and
   green CI approve (three `ACT-REV` reviewer rows, CLI exit 0, per-reviewer summary lines); zero reviewers, a failed
   reviewer, an unparseable reviewer, a CHANGES_REQUESTED reviewer and an APPROVED-with-a-nit reviewer each block, stop
   after iteration 1, record `ACT-REV` / `ACT-FIND` rows in a temporary `executions.jsonl`, never write `ACT-RES`, post
   only when a reviewer completed and give CLI exit 1; dirty, failing and unknown CI never approve; host refusal with
   both sandbox variables cleared (no ensemble call, no runner, no rows, no post); Stage 4 on the host fails closed and
   never calls `context.dispatcher` or the ensemble; Stage 4 threads `review_ensemble_size`; unresolvable target and
   failing diff fail without posting; dry runs never dispatch; the entrypoint exits 1 without an APPROVED ensemble
   verdict; `build_review_request` on a real temporary git repository excludes `plans/` and raises on a bad base or a
   missing head SHA; exact `--ensemble-size` help on `holon flow` and `holon review-loop` and the `>= 1` validation. New
   `test_review_report.py`: real agent and model per reviewer, no commit-derived 8-hex ids, never the former fallback
   title, `ReviewTarget.title` when the intent has none, then `PR #<n>` or the branch, failed reviewer with a redacted
   reason, untrusted finding text sanitised, unmeasured CI never shown as a pass.
7. **Step 7 (docs).** Updated `docs/architecture.md`, `docs/git_flow.md`, `docs/agents.md`, `docs/budgets.md`,
   `docs/ledger_schema.md` (per-reviewer `ACT-REV` keys, per-finding `ACT-FIND`, no `ACT-RES` for new runs, no
   `ACT-POST` when no reviewer completed, host refusal, the known gap tied to the routing follow-up),
   `docs/sandbox/execute_plan.md`, `README.md` (flow diagram and the new `--ensemble-size` row) and
   `apps/sandbox-executor/README.md` (renamed section `Reviewer ensemble`, links updated). Historical audit records,
   `plans/` and earlier `executions/` were not touched. The only remaining "resolver" hits describe its absence or are
   unrelated (the entropy-budget resolver, manual fix-commit trailers).
8. **Step 8 (verification).** See Tests Run. `grep` finds no `hashlib`, `sha256`, "Audit Remediation", "-Agent
   Independent" or "Agent Ensemble" in `flow.py`, `review_report.py` or `review_ensemble.py`; the only merge mentions
   are the pre-existing reviewer prompt prohibitions. `flow.py`: 5107 lines before, 5103 after.

### Changed existing assertions

- `test_flow.py::test_review_loop_granular_ledger_records`: was approved after two iterations with two evaluation rows,
  one "findings detected" row and one `ACT-RES` resolver row; now not approved, `conflicted_dirty`, `CHANGES_REQUESTED`,
  one iteration, one evaluation row, three reviewer rows, one `FIND-DIRTY-001` "finding detected" row and no `ACT-RES`
  row (posting assertions unchanged).
- `test_flow.py::TestStage4LedgerProvenance::test_review_loop_appends_act_rows_to_target_repository`: was approved with
  prefixes `ACT-REV, ACT-FIND, ACT-RES, ACT-REV, ACT-POST`; now not approved with
  `ACT-REV, ACT-REV, ACT-REV, ACT-REV, ACT-FIND, ACT-POST` (the helper is renamed `_run_dirty_then_clean_loop`).
- `test_flow.py::test_review_rows_are_byte_compatible_with_historical_shapes`: per-reviewer rows may carry the
  historical keys plus `REVIEWER_ROW_KEYS`; the set of seen prefixes no longer includes `ACT-RES`. The historical key
  fixtures are kept because historical ledger lines still exist.
- `test_flow.py::test_consensus_report_renders_mergeability_note`: ``**Finding `FIND-REF-001`**`` (fabricated per-id
  remediation) replaced by `**Mergeability:** <note>` and the absence of "Actionable Remediations".
- `test_flow.py::test_review_loop_max_iterations_exhaustion`: `total_iterations` 3 became 1 (no resolver).
- `test_flow.py::TestUnmeasuredTestsStopEarly` (two tests): "exactly one `ACT-REV` row" became one evaluation row plus
  three reviewer rows; the row ceiling 6 became 8 (three reviewer rows and `ACT-POST`).
- `test_flow.py::test_child_context_inherits_or_resets_every_field`: `review_ensemble_size` added to the inherited
  values.
- `test_preflight.py::test_resume_probes_only_when_an_agent_stage_remains`: `("review", False)` became
  `("review", True)`.
- Mocked approved loop results in `test_review_loop.py` (three), `test_cli.py` (one) and `test_flow.py` (one) gained
  `"ensemble_verdict": "APPROVED"`, since exit 0 now needs it; their assertions are unchanged.

No fail-closed assertion was removed.

### Deviations

- ROUTE was not implemented (FALLBACK): under `holon flow` on a host, Stage 4 fails closed until the routing follow-up
  in Step 1 lands; operators run `holon review-loop <execution_branch>` instead.
- The per-iteration `ACT-REV` evaluation row was kept (unchanged shape) alongside the new per-reviewer rows, so the
  mergeability, CI and trailer evidence stay in one row per iteration.
- `flow.py` shrank by 4 lines rather than the expected roughly 200, because the wiring helpers stayed next to
  `run_review_loop()`; the net count still does not increase.
- `holon review-loop --json` and `--local` on a host now refuse, because they run reviewer turns on the host.

## Tests Run

- `uv run pytest -n auto apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_review_ensemble.py`
  before any change: 823 passed.
- Affected modules after the change (`test_review_loop.py`, `test_flow.py`, `test_cli.py`, `test_preflight.py`,
  `test_review_report.py`): all passed (1020 in the four existing modules, 65 in `test_review_loop.py` alone, 11 in
  `test_review_report.py`). An intermediate run exposed a flaky real `git` call in the new base-ref lookup under a
  patched `time.sleep`; it was isolated behind `_review_base_ref` and faked.
- `uv run task lint`: exit 0 (`uv lock --check`, `uv.lock` unchanged, ruff check and format clean).
- `uv run task lint-docs`: exit 0.
- `uv run task test` (once): 2,659 passed, 8 warnings (pre-existing `fork()` deprecation warnings in
  `test_ledger_lock.py`).
