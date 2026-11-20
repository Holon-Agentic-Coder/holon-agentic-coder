# Plan Refresh After an Admission Wait

A flow refused at Stage 3 admission waits until the overlap clears, but its plan branch was cut from the target branch
before the overlapping change merged. Executing from that tip would write code against files that no longer exist on the
target and end in a Stage 4 merge conflict with exactly the change it waited for. So Stage 3 refreshes the plan branch
after admission and before the execution branch is cut and the executor is dispatched (`flow/plan_refresh.py`):

- **Trigger.** The checkpoint's admission object is `admitted` with `waited_since` set (the earlier refusal's
  `blocked_since`) and no `refreshed_to` whose `refreshed_branch` is the current plan branch
  ([Admission state](touch_set.md#admission-state)). A re-plan (after a `touch_set_violation` or an over-budget plan)
  cuts a new plan branch from the stale intent branch, so that branch is refreshed again. A flow admitted on its first
  check has no `waited_since` and its plan branch is never touched; neither is an `--allow-overlap` admission that never
  waited.
- **Merge, never rebase.** The host fetches `origin/<target>` and the plan branch and merges the target into the plan
  branch with a merge commit (`execute: refresh plan <plan_id> onto origin/<target> after the admission wait`) in a
  temporary worktree, then pushes the plan branch without force and fast-forwards the local plan branch. The operator's
  checkout is not touched, no history is rewritten and nothing is written to the target branch. When the plan branch
  already contains the target, no merge commit is made.
- **Ledger conflicts are mechanical.** Conflicts confined to `holon-knowledge/ledger/*.jsonl` are resolved by keeping
  the merge-base rows as a byte-identical prefix and interleaving the appended rows by `created_at`.
- **Any other conflict stops Stage 3 before dispatch.** The merge is aborted, nothing is pushed and no executor runs.
  The failure names the conflicting files (payload `plan_refresh_conflicts`, `dispatched: false`). The admission object
  keeps `waited_since` without `refreshed_to`, so after merging `origin/<target>` into the plan branch by hand (merge,
  do not rebase) a resume retries the refresh, which is then a no-op, and dispatches.
- **Recorded once.** A refresh appends one `plan_refresh` row to the plan branch's `executions.jsonl` (`entry_type` and
  `kind` `"plan_refresh"`, `plan_id`, `plan_branch`, `intent_id`, `target_branch`, `refreshed_to` (the target commit
  merged in), `merge_commit` (`null` when none was needed), `waited_since`, `recorded_by: "flow"`, `created_at`), which
  every execution branch cut afterwards inherits, then records `refreshed_to` and `refreshed_branch` in the checkpoint's
  admission object. The Stage 3 payload carries it as `plan_refreshed_to`. A re-run finds the row for the same commit
  and adds none.
- **Resumed partial work survives.** A refresh merge on the plan branch's first-parent history counts as engine-owned
  for a resumed execution ([Where a Resumed Execution Starts](git_flow.md#132b-where-a-resumed-execution-starts)): a
  two-parent commit with the refresh subject whose tree, outside the ledgers, is exactly the merge of its parents. The
  check does not verify that the second parent is target-branch history, so it refuses a hand-made commit that copies
  the subject but edits the merge result, not a clean hand-made merge of another branch under that subject.
- **Dry run.** A dry run only logs that the plan branch would be refreshed.

A refresh can bring a semantic (non-textual) break from the target into the plan; the execution then works against the
current target, and tests and review catch it as they catch any break on the target.
