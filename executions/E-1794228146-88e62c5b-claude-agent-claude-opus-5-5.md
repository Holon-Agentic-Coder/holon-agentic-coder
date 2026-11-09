# Execution Record: E-1794228146-88e62c5b-claude-agent-claude-opus-5-5

## Summary

Outcome: all ten plan steps completed and the validation gate passes.

Implements plan `P-1794227872-152246bc-claude-agent-claude-opus-5-5`: nest decomposed children under the plan branch,
merge each child PR into it with a guarded merge commit, integrate the plan branch into the parent execution branch so
the normal Stage 4 and Stage 5 raise and review the combined PR, and resume decomposed runs without re-dispatching the
parent executor or a child.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 13.6  |
| impact_actual         | 88    |
| cost_actual           | 34    |
| learning_value_actual | 6.5   |

## Steps Executed

### Step 1: Contracts, budgets and Stage 4 behaviour confirmed (read-only)

- Baseline: `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal` gave 504 passed in `5.15s`.
- Headroom: `pipeline.py` 394/394, `flow/__init__.py` 362/362, `stages/decomposition.py` 242/242. Deleting the
  decomposed Stage 4/5 skip block (8 lines) and `_parent_was_decomposed` (6 lines) frees 14 lines in `pipeline.py`, so a
  resume hook of about 5 lines fits without raising its budget.
- Stage 4 (`stages/review.py`) raises and reviews the PR from `context.execution_branch` into
  `intent_data["target_branch"]` with no `main` special case, so a combined decomposed PR needs no Stage 4 change.
- Stage 1 cuts the child intent branch from `origin/{target_branch}` (`entrypoint/intent_creator.py` clones and checks
  out `origin/{target_branch}`; the test fake `FakeRoleAgents._intent` does the same), so recording the plan branch as
  the child's `target_branch` cuts each child from the plan branch tip as it stands on origin.
- Checkpoints of a child (`parent_intent_id` set) are keyed on the child's bare branch in `save_checkpoint`, so each
  child already has its own checkpoint file.
- `git_flow.repository_default_branch` falls back to `main`, and `probe_ci_checks` falls back to a passing `local_tests`
  result when `gh` is missing; neither is fail-closed, so the merge guard uses its own `gh` lookups through an
  injectable runner.
- Unplanned constraint: `tests/flow/test_move_proof.py` pins the AST digest of every flow-package definition against
  `tests/flow/fixtures/flow_py_definitions.json` and fails on any new definition ("extra"), any changed body and any
  removed definition. Resolution: the fixture gains an `added_definitions` map (each new definition with its module,
  this execution and the reason) and a `removed_definitions` map; changed bodies are listed in the existing
  `amended_definitions` map with refreshed digests; the move-proof test excludes declared additions from "extra" and
  checks that each declared addition exists. The late-binding plumbing key moves from `_decomposition.PipelineEngine` to
  `_children.PipelineEngine` in `tests/flow/move_proof.py`.
- Tests that pin the old behaviour and must be updated in Step 8: `tests/flow/stages/test_decomposition.py` (child
  `target_branch` equals the intent branch, the "parent intent branch"/"Pending (not yet automated)" summary, the
  decomposed Stage 4/5 skip, `_parent_was_decomposed`, end-to-end runs with `skip_review=True`),
  `tests/flow/test_package_exports.py` (late binding on `decomposition`), `tests/test_cli.py` (decomposed summary
  wording) and `tests/flow/fixtures/flow_py_definitions.json` with `tests/flow/move_proof.py`.
- Abort criteria not met: Stage 4 targets any branch and the hook fits the freed lines, so the plan continues.

### Step 2: `flow/ledger_interleave.py` for ledger-only merge conflicts

- `interleave_ledger_rows(base, ours, theirs)`: refuses (with `ConflictResolutionError` from `ledger_reconcile`) a side
  that does not start with the merge-base rows; keeps the base rows as a byte-identical prefix; merges the two appended
  tails like a merge step, so each side keeps its own order (a deviation from a full stable sort, which could reorder
  rows within one side's tail), the earlier `created_at` goes first, ours wins a tie, and a row without a parsable
  `created_at` waits for the other side's timed rows; a row already kept is never repeated.
- `resolve_ledger_only_conflicts(repo_dir)`: returns False when nothing conflicts, when any unmerged path is outside
  `holon-knowledge/ledger/*.jsonl` or when a ledger conflict is not a pure append; otherwise writes the interleaved rows
  from index stages 1, 2 and 3 and stages them.
- Tests: `tests/flow/test_ledger_interleave.py` (10 tests, including a real two-branch repository with a conflicting
  `executions.jsonl`, and a non-ledger conflict left unresolved for the caller).
- Move proof: `tests/flow/test_move_proof.py` now reads an `added_definitions` map from the fixture (excluded from
  "extra"; a new test checks each declared addition is defined once in its declared module and carries a reason); this
  module's seven definitions are declared there. Budget entry `ledger_interleave.py: 112`.
- `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal`: 515 passed in `5.05s`; `ruff check`,
  `ruff format --check` and the budget script pass.

### Step 3: `flow/github/merge.py` with the guarded `merge_child_pr` and the pure refusal check

- `merge_refusal_reason(base, expected, default)`: pure and fail-closed. It normalises `refs/heads/`, `refs/remotes/`
  and `origin/` prefixes and refuses a failed or blank default-branch lookup, an empty base or expected base, `main`,
  `master` or the default branch on either side, and any base other than the expected plan branch.
- `lookup_default_branch` reads `gh repo view --json defaultBranchRef` and returns None on any failure (no `main`
  fallback). `run_gh` is the single `gh` seam (`gh_runner(runner)` resolves it at call time); a missing binary or a
  timeout is a failed process, never a raise.
- `merge_child_pr(repo_dir, pr_number, plan_branch, runner=None)` returns a `MergeResult` (`merged`, `merge_commit`,
  `reason`, `already_merged`): an already merged PR into the plan branch is an idempotent success; otherwise the refusal
  check runs, `merge_into_branch` brings `origin/{plan}` into the child head when it is not an ancestor (in a temporary
  worktree, ledger-only conflicts interleaved, anything else aborted and refused), a bounded wait
  (`MERGE_SETTLE_SECONDS`) requires `headRefOid` to match, `mergeable == MERGEABLE` and green checks (no checks is
  `unmeasured`, never green), the PR is re-read and the refusal check runs again, then only
  `gh pr merge <n> --merge --match-head-commit <sha>` runs and the PR must report `MERGED`.
- Deviation: CI is read with `gh pr checks` through the injected runner instead of `probe_ci_checks`, because
  `probe_ci_checks` calls `subprocess` directly and falls back to a passing `local_tests` result without `gh`, which is
  not fail-closed (the plan's discard clause allows keeping the logic in `merge.py`).
- `merge_into_branch` is public so Stage 3 integration (Step 6) reuses it for the plan-branch-into-execution merge.
- Tests: new `tests/flow/fake_gh.py` (`FakeGitHub`: PRs over a real bare origin; `gh pr merge --merge` performs a real
  `git merge --no-ff`; every call recorded) and `tests/flow/github/test_merge.py` (30 tests): base `main` refused with
  no `pr merge` call and `main` unchanged; `master`, the default branch, an unexpected base and a failed lookup refused
  before any git write; red, pending and missing CI, `CONFLICTING` and `UNKNOWN` refused; the merge is a two-parent
  commit made only with `--merge --match-head-commit`; idempotent re-merge; a stale child gets the plan branch merged in
  first; non-ledger conflicts refused; ledger-only conflicts interleaved.
- Move proof: 24 definitions declared in `added_definitions`; budget entry `github/merge.py: 271`.
- `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal`: 545 passed in `5.34s`; lint and budgets
  pass.

### Step 4: `flow/child_progress.py` for per-child progress and origin reconciliation

- Progress entries (`index`, `slug`, `intent_branch`, `execution_branch`, `pr_number`, `pr_url`, `status`,
  `merge_commit`) live under `children_progress` in the parent Stage 3 payload; `initial_progress(items, plan_branch)`
  builds `pending` entries named `{plan}--{n}-{slug}/_`.
- Deviation (import direction): `_child_intent_branch` and `_sub_intent_depth` moved unchanged from
  `stages/decomposition.py` into `child_progress.py` (same move-proof digests). `decomposition` will delegate to
  `children`, which needs both names, so keeping them in `decomposition` would create a
  `decomposition -> children -> decomposition` import cycle. `decomposition` and `flow/__init__.py` now import them from
  `child_progress`.
- `next_child_action(entry, checkpoint_exists)` is pure: `skip` (merged), `merge` (`review_done` with a PR), `resume`
  (an execution branch, a PR, the intent branch on origin, a checkpoint, a failed origin lookup or any status past
  `pending`) and `dispatch` only when nothing exists anywhere.
- `reconcile_with_origin(repo_dir, progress, runner)` discovers each unmerged child's execution branches under its
  intent branch on origin and its PR via `gh pr list --head <exec> --state all`; origin wins over the checkpoint and
  each disagreement is returned as a log note; a merged PR promotes the entry to `merged`; a failed fetch or `gh` lookup
  sets `origin_lookup_failed` (so `resume`, never `dispatch`).
- `child_checkpoint_path` matches `save_checkpoint`'s child keying (asserted by a test); `has_recorded_decomposition`
  needs both `decomposed` and a non-empty `children_progress`.
- Tests: `tests/flow/test_child_progress.py` (20 tests: the full decision table, reconciliation against a real bare
  origin with `FakeGitHub`, origin overriding a stale checkpoint, failed lookups, distinct checkpoint paths).
- Move proof: 15 definitions declared; budget entry `child_progress.py: 193`.
- `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal`: 565 passed in `5.87s`; lint and budgets
  pass.

### Steps 5, 6 and 7: child loop, integration and the resume hook

These three steps were implemented and committed together: `decomposition.py` delegates to `children.run_children`,
which calls `integration.integrate_children`, and `pipeline.py` imports `children.resume_children`; any one of them
alone leaves the package unimportable or the suite red.

- Step 5, `flow/stages/children.py` (286 lines): `_PR_URL_RE`, `_child_review_pr` and `_child_context` moved unchanged
  from `decomposition.py`. `run_children` drives each `children_progress` entry through `next_child_action`: `skip`;
  `dispatch` or `resume` (`_run_one_child`, which loads the child's own checkpoint and runs
  `PipelineEngine(child, from_stage=checkpoint.current_stage)`, refusing a child that has an execution branch or PR but
  no checkpoint); then `_merge_ready_child` calls `merge_child_pr`. A child is ready only when Stage 4 halted with
  consensus approval and Stage 5 succeeded or was skipped, and it has a PR number. Every state change saves the parent
  checkpoint with Stage 3 `running`. The first failure marks the entry `failed` and fails Stage 3 naming the child.
  Child intents record `target_branch = parent_intent_id = plan_branch` and are named `{plan}--{n}-{slug}`.
- `stages/decomposition.py` (242 to 124 lines) keeps `_sub_intent_depth_error`, the summary formatting and
  `_run_decomposed_plan` (signature unchanged): it validates the items, records the plan branch (from the context, else
  the decision record), applies the depth limit, fails clearly when no plan branch is known (never falls back to the
  intent branch), builds the initial progress and delegates. The summary lists each child PR as merged into the plan
  branch and names the combined PR that Stage 4 raises; failed runs count how many ran and merged and omit the hand-off.
- Step 6, `flow/stages/integration.py` (96 lines): `integrate_children` merges `origin/{plan}` into the parent execution
  branch with `merge_into_branch` (merge commit, temporary worktree, ledger-only conflicts interleaved), appends a
  superseding `decomposed` summary row with `integrated: true`, `plan_branch` and `children` (slug, intent branch,
  execution branch, PR number and URL, merge commit) through `_append_superseding_execution_row`, and returns a
  successful Stage 3 with `context.execution_branch` set to the parent execution branch. Both actions are idempotent (no
  merge when the plan branch is already an ancestor; no row when the latest row is already `integrated`).
  `_append_superseding_execution_row`'s commit message now names the row's status (same line count).
- Step 7, `flow/pipeline.py` (394 to 387 lines): the decomposed Stage 4/5 skip block and `_parent_was_decomposed` are
  deleted; `execute_stage` returns `resume_children(context, previous)` when the checkpointed Stage 3 satisfies
  `has_recorded_decomposition(previous, context.plan_branch)` (decomposed, non-empty progress and the same plan branch,
  so a re-plan never resumes an earlier plan's children). The post-hoc budget exemption for decomposed results is kept.
  `ruff --fix` narrowed `PipelineEngine.run`'s suppression from `C901, PLR0912, PLR0915` to `C901, PLR0915` (the branch
  count fell); `tests/test_complexity_gate.py` records that narrowing (a ratchet down, count 184 to 180).
- `flow/__init__.py` (362 to 359 lines): re-exports the moved names from their new modules and binds
  `_children.PipelineEngine` late instead of `_decomposition.PipelineEngine`.
- Budgets: new `stages/children.py: 286`, `stages/integration.py: 96`; lowered `stages/decomposition.py: 124`,
  `pipeline.py: 387`, `__init__.py: 359`, `child_progress.py: 190` (trimmed after the `plan_branch` guard so its budget
  never rose). No budget was raised.
- Move proof: 15 new definitions declared; `PipelineEngine`, `PipelineEngine.execute_stage`, `PipelineEngine.run`,
  `_append_superseding_execution_row`, `_decomposition_child_lines`, `format_decomposition_summary` and
  `_run_decomposed_plan` amended with reasons; `PipelineEngine._parent_was_decomposed` listed in `removed_definitions`;
  the plumbing key is now `_children.PipelineEngine`.
- Tests updated: `tests/flow/stages/test_decomposition.py` (end-to-end runs now use `decomposed_flow`, so children halt
  at consensus, open PRs in a `FakeGitHub` and really merge into the plan branch on the fixture origin; child targets
  are the plan branch; the stubbed helper also stubs the merge, integration and checkpoint; new tests for a missing plan
  branch, a child not ready to merge, a refused merge and the resume hook's plan-branch guard; the Stage 4/5 skip tests
  are replaced by a test that Stage 4 and Stage 5 now follow their flags), `tests/flow/helpers.py` (the budget fake
  executor also writes a per-plan file, so a child cut after a sibling merged still has an effective diff),
  `tests/flow/test_package_exports.py`, `tests/flow/move_proof.py`, `tests/flow/test_move_proof.py` and
  `tests/test_cli.py`. New `tests/flow/decomposition_fakes.py`.
- `uv run pytest apps/sandbox-executor/tests -n auto --dist worksteal`: 3434 passed, 21 skipped in `42.55s`; ruff and
  the budget script pass.

### Step 8: hermetic end-to-end tests for goal 8 b, c, d, e and f

- Fixtures: the existing `flow_repos` bare origin and host clone with `BudgetRoleAgents` (fake intent creator, planner
  and executor containers pushing real branches), plus `tests/flow/decomposition_fakes.py`: `ChildReviews` replaces
  `run_review_stage` in `sandbox_executor.flow.pipeline` (opens or reuses a PR in `FakeGitHub` from the execution branch
  into the intent's `target_branch`, or an overridden base per slug, and halts at consensus; optionally fails one review
  once, keeping its PR in the payload like the real stage), and `decomposed_flow` patches it in together with
  `sandbox_executor.flow.github.merge.run_gh`. Deviation: the plan proposed patching the child `PipelineEngine`; running
  the real child pipelines with fake containers and a fake Stage 4 exercises Stage 1 cutting from
  `origin/{target_branch}` for real, so it was preferred.
- `tests/flow/stages/test_children.py`: (b) each child intent branch starts with `{plan}--` and its PR base is the plan
  branch; child 1 is cut from the original plan tip; (c) child 2 is cut from child 1's merge commit and its execution
  branch holds child 1's per-plan file; (d) the plan branch gains exactly one two-parent merge commit per child with the
  child head as second parent, and only `--merge` merges ran; a child whose PR base is `main` is refused, `gh pr merge`
  is never called, `main` and the plan branch are unchanged, the second child never starts and Stage 3 fails naming the
  child; (f) after child 2's review fails, a resume from the parent checkpoint dispatches no container at all (not the
  parent executor, not any child role), resumes child 2 from its own checkpoint at Stage 4 reusing its PR (the only new
  PR is the parent's combined PR into `main`), leaves child 1's checkpoint byte-identical and merges child 1 once only;
  a resume after a crash between a child's review and its recorded merge reconciles the merged PR from origin and merges
  nothing twice.
- `tests/flow/stages/test_integration.py`: (e) the parent execution branch holds the parent plan file, both child plan
  files, both children's execution rows, the executor's `decomposed` row and one superseding `integrated` row listing
  the children with PR numbers and merge commits; the plan branch is its ancestor; Stage 4 raised the combined PR with
  base `main` and the pipeline halted for the human; integration is idempotent on a resume; a failed merge and a missing
  execution branch fail Stage 3 with the reason.
- Updated in Steps 5 to 7: `tests/flow/stages/test_decomposition.py`, `tests/test_cli.py`,
  `tests/flow/test_package_exports.py`.
- `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal`: 575 passed in `6.03s` (before the last
  formatting-only edit); `ruff check` and `ruff format --check` pass.

### Step 9: documentation

- `docs/architecture.md`: the interim notes in "Strict rebase discipline" and "Principle: merges flow up to the deciding
  node" now describe the implemented behaviour; module table rows added for `flow/ledger_interleave.py`,
  `flow/child_progress.py`, `flow/github/merge.py`, `flow/stages/children.py` and `flow/stages/integration.py`; the
  `decomposition.py` row and the late-binding note now name `children.py`; the move-proof paragraph documents the
  `amended_definitions`, `added_definitions` and `removed_definitions` maps.
- `docs/git_flow.md`: the Stage 3 decomposition description is rewritten (nesting and `{plan}--{n}-{slug}` naming, one
  child after another with each merged before the next, the guarded merge-commit merge, the combined PR from the parent
  execution branch with the human stop at `main`, resume rules and ledger interleaving); the interim notes in sections
  3.1, 4.1, 4.2 and the summary table's key insight are replaced; the CI note names the plan branch as the base of
  stacked PRs.
- `docs/safety.md`: interim sentences removed; new "Sub-intent merge rules" section (merge commit only, the fail-closed
  base check and default-branch lookup, readiness, bringing the plan branch in, the ledger interleave guarantee and how
  a refusal is reported).
- Beyond the plan's three files, the same interim sentence (now false) was removed from `docs/agent_design.md`,
  `docs/core_concepts.md`, `docs/examples.md`, `docs/planner/planning_architecture_specification.md` and
  `.github/workflows/README.md`, and the `tests/test_workflow_triggers.py` docstring now names the plan branch.
  `docs/ledger_schema.md` still carries two such notes (lines 134 and 731) because the intent forbids editing it; they
  need a follow-up.
- `grep` for "not yet automated" and "Until the implementation follows" finds nothing outside `docs/ledger_schema.md`;
  the two remaining "parent intent branch" mentions in `docs/git_flow.md` say sub-intents do not merge into it.
- `npx prettier --check "**/*.md"` passes (the touched files were re-wrapped with `prettier --write`).

### Step 10: budgets registered and the gate validated

- `flow_module_budgets.json`: new entries at delivered size (`ledger_interleave.py: 112`, `github/merge.py: 271`,
  `child_progress.py: 190`, `stages/children.py: 286`, `stages/integration.py: 96`); lowered `stages/decomposition.py`
  (242 to 124), `pipeline.py` (394 to 387) and `__init__.py` (362 to 359). No budget was raised and no `noqa` was added
  to source; one existing suppression narrowed.
- A ruff `E501` in the `tests/test_workflow_triggers.py` docstring (re-worded in Step 9) was found by `uv run task lint`
  after the full suite run and fixed by re-wrapping the docstring; lint and that file's tests were re-run.

## Tests Run

No repetition: the intent sets no performance target, so each command ran once on the final code (the plan's N is 1).

| command                                                                                   | result                                                       | pytest time |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------- |
| `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal` (final)         | 575 passed, 0 failed                                         | `5.99s`     |
| `uv run task test` (`-n auto --dist worksteal`, once)                                     | 3440 passed, 0 failed, 8 warnings                            | `29.16s`    |
| `uv run task lint` (`uv lock --check`, ruff check, ruff format check, budgets)            | passed after the docstring re-wrap; 33 modules within budget | n/a         |
| `uv run task lint-docs` (`npx prettier --check "**/*.md"`)                                | passed                                                       | n/a         |
| `uv run pytest apps/sandbox-executor/tests/test_workflow_triggers.py` (after the re-wrap) | 23 passed                                                    | `0.03s`     |

Median of the single full-suite run: `29.16s`. Intermediate runs recorded in the steps above: flow tests 504 passed
(`5.15s`, baseline), 515 (`5.05s`), 545 (`5.34s`), 565 (`5.87s`), 575 (`6.03s`); full suite after Steps 5 to 7: 3434
passed, 21 skipped (`42.55s`, run with `pytest apps/sandbox-executor/tests` rather than the task, so integration and
stress markers were collected and skipped).

## Residual risks and follow-ups

- The real GitHub merge path (branch protection, required reviews, merge queues, the time GitHub takes to recompute
  `mergeable` after the child head moves) is exercised only against `FakeGitHub`; every unexpected answer refuses the
  merge and fails Stage 3 with the reason, and `MERGE_SETTLE_SECONDS` bounds the wait at 15 minutes.
- Intermediate child-loop checkpoints are written with `save_checkpoint(context)` to the default checkpoint path; a run
  started with an explicit `--checkpoint` path gets the final Stage 3 result there through the pipeline, but the in-loop
  progress snapshots go to the default path.
- `docs/ledger_schema.md` (lines 134 and 731) still says the flow records the parent intent branch as a child's target;
  the intent forbids editing it, so it needs a follow-up.
- Stage 5 calibration of a decomposed parent now runs on the combined execution branch; its metrics come from the
  parent's ledger rows, which describe a decomposition rather than an implementation.
