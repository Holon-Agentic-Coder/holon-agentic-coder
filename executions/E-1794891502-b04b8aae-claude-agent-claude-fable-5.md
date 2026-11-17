# Execution Record: E-1794891502-b04b8aae-claude-agent-claude-fable-5

## Summary

Implements plan `P-1794891094-eede5d91-claude-agent-claude-fable-5` for intent `I-1794891078-s5rerun`: a
calibration-commit-aware head-move predicate and a bounded Stage 5 rerun in a new module
`flow/calibrate_rerun.py`, wired at the pipeline's Stage 5 block and the review loop's pre-posting gate,
with a marker-de-duplicated stale signal on the pull request and an honest flow report-back line.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Pin down head-move semantics and rerun seams (INFO_GATHERING)

Read-only reconnaissance; no code changed. Findings consumed verbatim by Steps 2-4:

**Predicate specification.** For a report with evaluated SHA `E` and a pull-request head `H` (both resolved
in the host worktree after a fetch of `origin/<execution_branch>`), the head has **moved** iff:

- `E` or `H` is unknown → unmoved (nothing to rerun against; the reason names the gap), and
- `H` equals `E` (prefix match either way, since a report may carry an abbreviated SHA) → unmoved, and
- `E` is not an ancestor of `H` (force push, history rewrite) → **moved**, and
- otherwise walk `E..H`: unmoved iff every commit is a calibration commit per
  `calibrate_rebuild._is_calibration_commit` (non-merge, subject prefix `chore(calibration): `, every
  touched path accepted by `is_calibration_path`); any merge commit or foreign commit → **moved**.

Stage 5's own commit shapes all satisfy the calibration-commit classifier: the report commit
(`chore(calibration): add calibration report for P-...`, touching `plans/P-..._calibration.md` and the
executions ledger), the reconciliation commit (`chore(calibration): record stage 5 reconciliation`,
touching `holon-knowledge/ledger/executions.jsonl`) and the promotion commit
(`chore(calibration): record stage 5 promoted knowledge and wisdom`, touching kb/wisdom paths). Merge
commits are rejected by the classifier (`len(parents) > 2`), so a conflict merge is always a move.

**Seams.** `run_calibrate_stage(context)` is the rerun entry point (idempotent via `sync_execution_ref` +
`prepare_calibrated_branch`/`rebuild_calibrated_branch`); it refreshes payload keys `calibrated_head`,
`calibration_report`, `final_head_ci`, `ci_verdict`. The report lives at `plans/{plan_id}_calibration.md`
on the execution branch (read via `calibration.read_git_file`; plan id via
`calibration.extract_branch_components`); its SHA parses with `calibration.parse_evaluated_commit_sha`.
The gh comment seams are `approval_invalidation.gh_comment_lister` / `gh_comment_poster` with a hidden
marker, and the PR number comes from `approval_invalidation.approval_pr_number(context)` in the pipeline
and from `pr_res["pr_number"]` in the review loop. Pipeline trigger sites: the generic stage loop after
`timed_execute_stage(CALIBRATE)` and the HALTED_FOR_HUMAN auto-calibrate block (`flow/pipeline.py`),
both before `after_stage`/`save_checkpoint` so the checkpoint records the refreshed result. Review-loop
site: after the `decide_post` posting block in `flow/review/loop.py`, keyed on `pr_res["pr_number"]`.

**Bounds.** `MAX_RERUN_ATTEMPTS = 2` (hard constant). Stale comment marker keyed on the
`(evaluated_sha, current_head)` pair: `<!-- holon-calibration-stale:<evaluated>:<head> -->`; cleared
comment marker `<!-- holon-calibration-refreshed:<head> -->`.

### Step 2: Build `flow/calibrate_rerun.py` (IMPLEMENTATION)

- New module `flow/calibrate_rerun.py`: `HeadMoveCheck` / `RerunSeams` dataclasses,
  `head_move_since_calibration` (the predicate per the Step 1 spec), `report_staleness` (reads the
  report at the branch tip via `read_git_file`, resolves the origin head after a tolerant fetch),
  `post_stale_signal` plus a private refreshed-signal poster (marker-de-duplicated, best-effort),
  `rerun_calibration` (bounded by `MAX_RERUN_ATTEMPTS = 2`, no-op on an unmoved head, sets
  `calibration_stale`/`stale_reason` when the head outruns both attempts) and
  `review_calibration_freshness` (the review loop's gate; stale posts the signal, and reruns only
  in-flow: `auto_calibrate` with a successful prior Stage 5 result).
- Promoted `calibrate_rebuild._is_calibration_commit` to the public `is_calibration_commit`
  (pure rename; both call sites updated, module size unchanged).
- Added `ci_reverify.rerun_note` so the reverify module stays the single owner of the head-moved
  wording in the refreshed `ci_verdict` report.
- Exported the new public names from `flow/__init__.py` (package-exports guard requires it).
- Predicate unit tests in `tests/test_calibrate_rerun.py`: equal heads, abbreviated SHA,
  calibration-only, mixed, calibration-subject-on-foreign-path, merge commit, force-push
  non-ancestor, unknown SHA.

## Tests Run

(pending)
