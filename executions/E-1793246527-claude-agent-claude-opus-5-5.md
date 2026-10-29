# Execution Record: E-1793246527-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793246201-claude-agent-claude-opus-5-5` for intent
`I-1793246184-execute-failure-keeps-partial-code-and-resumes-from-it`. All ten steps were carried out on this execution
branch, with no discard criterion taken and no file outside the touch set needed.

- On failure, the executor commits the agent's working-tree changes in a `partial work (failed)` commit ahead of the
  failure commit, and records `partial_head` on the failure summary row.
- It sets its git identity before the agent runs.
- A refused push is reported with a redacted, size-capped `format-patch` block on stderr.
- A Stage 3 flow resume continues from the newest failed branch that carries a `partial_head`.

The Step 5 hypothesis held: in a `--depth 1` clone (over `file://`), fetching the failed branch brings its commits down
to the shallow boundary, so the ancestry check succeeds without deepening.

`uv run task test` (2,706 passed), `uv run task lint` and `uv run task lint-docs` all pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.92  |
| entropy_actual        | 7.0   |
| impact_actual         | 90.0  |
| cost_actual           | 20.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. **Step 1 (baseline): done.** `uv` was not on the PATH, so it was installed with `pip install --user uv`.
   `uv run pytest apps/sandbox-executor/tests/test_executor.py apps/sandbox-executor/tests/test_flow.py` had 573 passed
   before any edit. The assertions the new contract changes were in
   `test_main_empty_diff_records_failed_and_skips_code_commit`,
   `test_main_empty_diff_guard_fails_closed_without_plan_tip`,
   `test_budget_failure_records_one_act_budget_and_exits_non_zero`, `test_breach_then_notes_only_resume_is_not_success`
   and `test_generic_exit_one_with_usable_notes_is_failure`. One more test also encoded the old push behaviour:
   `test_main_over_budget_whose_push_is_rejected_by_origin_fails`, which expected an unhandled `CalledProcessError`.
2. **Step 2 (identity): done.** Added `_configure_executor_identity` with the `EXECUTOR_GIT_EMAIL` / `EXECUTOR_GIT_NAME`
   constants. It runs straight after `git checkout -b`, again before the salvage commit, and before the final commit (to
   cover a recovery re-init). `sync_and_reconcile_pre_push` now calls it too.
3. **Step 3 (salvage): done.** Added `_salvage_partial_work` with `:(exclude)` pathspecs for `executions.jsonl` and the
   run's notes. The prettier block now runs before it. On a non-success status with a known diff base it commits
   `execute: <id> partial work (failed) for plan <plan>`, and `partial_head` is recorded on the summary row whenever
   HEAD differs from the diff base. When that commit fails, the files are named in a redacted warning and unstaged, so
   the failure commit still holds only the ledger and notes.
4. **Step 4 (refused push): done.** The push now runs with `check=False`. On a refusal `_report_unpushed_work` prints
   the branch, the local head and git's redacted reason, then the redacted `format-patch` between the
   `BEGIN/END UNPUSHED EXECUTION PATCH` markers. Above `UNPUSHED_PATCH_MAX_BYTES` (`256 * 1024`) it prints the diffstat
   and an omission line instead. It then exits 1, and `branch_recorded` is set only after a successful push.
5. **Step 5 (resume): done.** The executor reads an optional `argv[4]`. `_resolve_partial_head` checks the branch name
   shape and prefix, fetches the branch with an explicit refspec and reads its latest summary row through the flow
   helpers. It requires `status: failure` and a full-sha `partial_head`, checks the commit exists and checks ancestry
   (deepening once by 50 commits on a shallow clone). The new branch is cut from the partial head, and
   `continuation_note` is appended to `base_prompt`. Every refusal falls back to the plan tip and logs the reason.
6. **Step 6 (flow resume dispatch): done.** Added `_resumable_partial_branch` to `flow.py`. It walks the snapshot newest
   first and returns the first failed branch whose latest summary row has a `partial_head`.
   `run_execute_stage(context, resume_partial=False)` appends that branch as the fourth executor argument and logs it.
   `PipelineEngine.run` sets a non-checkpointed `_resume_partial_pending` flag when `from_stage` is `execute`.
   `execute_stage` consumes the flag on the first Execute stage only, so an Execute after a re-plan never resumes.
   `cli.py` and the checkpoint schema are untouched.
7. **Step 7 (executor tests): done.** Rewrote the five planned assertions with two helpers. `_unscoped_add_all` covers
   the success staging and `_salvage_add` covers the scoped salvage add. The failure tests now forbid the former and
   still forbid a `completed` commit. Updated `test_main_over_budget_whose_push_is_rejected_by_origin_fails` to the
   handled exit 1 and its reported reason. The three success-path assertions now use `_unscoped_add_all`. Added
   `TestExecutorPartialWorkSalvage`, which runs real git against a bare origin with a fake agent turn, and covers:
   - salvage on a budget breach and on exit 1 (edit, deletion, untracked file; ignored file excluded);
   - nothing to salvage;
   - agent commits with a clean tree (`partial_head` is the agent's commit, authored with the executor identity);
   - the success path;
   - identity and branch checked from inside the agent turn;
   - a refused push (markers, both patch subjects, fake `ghp_` token redacted), and a patch that `git am` applies;
   - an oversized patch falling back to the diffstat;
   - resume from a descending partial head (shallow clone, continuation note in the prompt);
   - resume falling back for a missing `partial_head`, a partial head off the moved plan tip, and a branch outside the
     plan.

   The mocked-harness identity-order check was replaced by the stronger real-git probe taken inside the agent turn.

8. **Step 8 (flow tests): done.** Added `TestStage3PartialResumeDispatch`:
   - an engine resume with `partial_head` dispatches four arguments;
   - a resume without one dispatches three;
   - a non-resumed `run_execute_stage` dispatches three;
   - non-failure rows are skipped.
9. **Step 9 (docs): done.** Replaced the "stages the partial notes, but not the code changes" sentence in
   `docs/budgets.md` and added `### Partial work on failure`. It covers the identity, the salvage commit,
   `partial_head`, the unpushed patch block with its cap and the merge-inflation limitation, recovery with `git am`, and
   resume semantics.
10. **Step 10 (validation): done.** The touch set is limited to `executor.py`, `flow.py`, `test_executor.py`,
    `test_flow.py`, `docs/budgets.md` and this notes file. No harness paths and no American spellings appear in the
    added lines.

Design notes:

- An empty-diff `failed` run also salvages its plans/notes-only changes as partial work, as the plan specifies. Flow
  resume ignores it because it requires `status: failure`.
- If the salvage commit itself fails, the executor prints a redacted warning naming the files and unstages them, so the
  failure commit still holds only the ledger and notes.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_executor.py apps/sandbox-executor/tests/test_flow.py` (baseline): 573
  passed.
- `uv run pytest apps/sandbox-executor/tests/test_executor.py -k PartialWorkSalvage`: 13 passed.
- `uv run pytest apps/sandbox-executor/tests/test_flow.py -k Stage3PartialResume`: 4 passed.
- `uv run task test`: 2,706 passed.
- `uv run task lint`: all checks passed, 83 files already formatted.
- `uv run task lint-docs`: all matched files use Prettier code style.
