# Execution Record: E-1794891502-b04b8aae-claude-agent-claude-fable-5

## Summary

Implements plan `P-1794891094-eede5d91-claude-agent-claude-fable-5` for intent `I-1794891078-s5rerun`: a
calibration-commit-aware head-move predicate and a bounded Stage 5 rerun in a new module `flow/calibrate_rerun.py`,
wired at the pipeline's Stage 5 block and the review loop's pre-posting gate, with a marker-de-duplicated stale signal
on the pull request and an honest flow report-back line.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 10.5  |
| impact_actual         | 88.0  |
| cost_actual           | 42.0  |
| learning_value_actual | 5.0   |

`EV_actual = 1.0 * 88.0 + 0.5 * 5.0 - 0.3 * 10.5 - 42.0 = 45.35`. Entropy landed below the predicted `12.5`: the only
surprise outside the plan was the pipeline's `run()` hitting the `PLR0912` branch cap, resolved by owning the trigger in
a `_rerun_if_calibrate` hook (no inline suppression added).

## Steps Executed

### Step 1: Pin down head-move semantics and rerun seams (INFO_GATHERING)

Read-only reconnaissance; no code changed. Findings consumed verbatim by Steps 2-4:

**Predicate specification.** For a report with evaluated SHA `E` and a pull-request head `H` (both resolved in the host
worktree after a fetch of `origin/<execution_branch>`), the head has **moved** iff:

- `E` or `H` is unknown → unmoved (nothing to rerun against; the reason names the gap), and
- `H` equals `E` (prefix match either way, since a report may carry an abbreviated SHA) → unmoved, and
- `E` is not an ancestor of `H` (force push, history rewrite) → **moved**, and
- otherwise walk `E..H`: unmoved iff every commit is a calibration commit per `calibrate_rebuild._is_calibration_commit`
  (non-merge, subject prefix `chore(calibration): `, every touched path accepted by `is_calibration_path`); any merge
  commit or foreign commit → **moved**.

Stage 5's own commit shapes all satisfy the calibration-commit classifier: the report commit
(`chore(calibration): add calibration report for P-...`, touching `plans/P-..._calibration.md` and the executions
ledger), the reconciliation commit (`chore(calibration): record stage 5 reconciliation`, touching
`holon-knowledge/ledger/executions.jsonl`) and the promotion commit
(`chore(calibration): record stage 5 promoted knowledge and wisdom`, touching kb/wisdom paths). Merge commits are
rejected by the classifier (`len(parents) > 2`), so a conflict merge is always a move.

**Seams.** `run_calibrate_stage(context)` is the rerun entry point (idempotent via `sync_execution_ref` +
`prepare_calibrated_branch`/`rebuild_calibrated_branch`); it refreshes payload keys `calibrated_head`,
`calibration_report`, `final_head_ci`, `ci_verdict`. The report lives at `plans/{plan_id}_calibration.md` on the
execution branch (read via `calibration.read_git_file`; plan id via `calibration.extract_branch_components`); its SHA
parses with `calibration.parse_evaluated_commit_sha`. The gh comment seams are `approval_invalidation.gh_comment_lister`
/ `gh_comment_poster` with a hidden marker, and the PR number comes from
`approval_invalidation.approval_pr_number(context)` in the pipeline and from `pr_res["pr_number"]` in the review loop.
Pipeline trigger sites: the generic stage loop after `timed_execute_stage(CALIBRATE)` and the HALTED_FOR_HUMAN
auto-calibrate block (`flow/pipeline.py`), both before `after_stage`/`save_checkpoint` so the checkpoint records the
refreshed result. Review-loop site: after the `decide_post` posting block in `flow/review/loop.py`, keyed on
`pr_res["pr_number"]`.

**Bounds.** `MAX_RERUN_ATTEMPTS = 2` (hard constant). Stale comment marker keyed on the `(evaluated_sha, current_head)`
pair: `<!-- holon-calibration-stale:<evaluated>:<head> -->`; cleared comment marker
`<!-- holon-calibration-refreshed:<head> -->`.

### Step 2: Build `flow/calibrate_rerun.py` (IMPLEMENTATION)

- New module `flow/calibrate_rerun.py`: `HeadMoveCheck` / `RerunSeams` dataclasses, `head_move_since_calibration` (the
  predicate per the Step 1 spec), `report_staleness` (reads the report at the branch tip via `read_git_file`, resolves
  the origin head after a tolerant fetch), `post_stale_signal` plus a private refreshed-signal poster
  (marker-de-duplicated, best-effort), `rerun_calibration` (bounded by `MAX_RERUN_ATTEMPTS = 2`, no-op on an unmoved
  head, sets `calibration_stale`/`stale_reason` when the head outruns both attempts) and `review_calibration_freshness`
  (the review loop's gate; stale posts the signal, and reruns only in-flow: `auto_calibrate` with a successful prior
  Stage 5 result).
- Promoted `calibrate_rebuild._is_calibration_commit` to the public `is_calibration_commit` (pure rename; both call
  sites updated, module size unchanged).
- Added `ci_reverify.rerun_note` so the reverify module stays the single owner of the head-moved wording in the
  refreshed `ci_verdict` report.
- Exported the new public names from `flow/__init__.py` (package-exports guard requires it).
- Predicate unit tests in `tests/test_calibrate_rerun.py`: equal heads, abbreviated SHA, calibration-only, mixed,
  calibration-subject-on-foreign-path, merge commit, force-push non-ancestor, unknown SHA.

### Step 3: Stale signal (IMPLEMENTATION)

- The marker-de-duplicated PR comments and the `calibration_stale`/`stale_reason` payload flags landed inside
  `flow/calibrate_rerun.py` with Step 2 (one module owns the signal).
- `cli/commands/flow.py` `_calibrated_head_line` now renders three states: STALE (covers `<sha>`, the pull request head
  is `<sha>` — not ready to merge on this report), covering, and not refreshed. The flow's report-back never says
  "covers" while stale.

### Step 4: Wire the triggers (IMPLEMENTATION)

- `flow/pipeline.py`: both Stage 5 sites (the generic stage loop and the HALTED_FOR_HUMAN auto-calibrate block) pass the
  completed result through `rerun_calibration(context, result, run_calibrate_stage)` before
  `after_stage`/`save_checkpoint`, so the checkpoint records the refreshed `calibrated_head`. An unmoved head (the
  common case: Stage 5's own commits are discounted) is a no-op; a failed rerun propagates through the unchanged FAILED
  path.
- `flow/review/loop.py`: after the `decide_post` posting block, one call to
  `review_calibration_freshness(context, pr_number)`; the record lands in the loop result as `calibration_freshness`. A
  standalone review loop posts the stale signal only (no prior Stage 5 result in the context), per the plan's narrowed
  standalone trigger.
- `github/ci_reverify.py` owns the rerun wording via `rerun_note` (landed in Step 2); no change to `stages/calibrate.py`
  was needed — the rerun wrapper sets the staleness payload keys itself.

### Step 5: Hermetic pytest coverage (TEST)

Extended `tests/test_calibrate_rerun.py` on the `test_calibrate_sync.py` harness (bare origin, host worktree, pushing
clone; a realistic fake `run_calibrate` committing the report with `- **Evaluated Commit SHA:**` plus an ACT-CAL ledger
row in Stage 5's own commit shape), covering all four acceptance behaviours:

1. Move → rerun: a resolver-style push after calibration makes `rerun_calibration` run the stage a second time; the
   rewritten report's evaluated SHA equals the new origin head, exactly one new ACT-CAL row per run, old ledger content
   a strict prefix of the new (rows appended, never rewritten), and the `ci_verdict` report names the rerun.
2. Unmoved → no-op: same result object back, zero stage calls, `for-each-ref` and the ledger byte-identical.
3. No loop: after a rerun, only calibration commits follow the evaluated SHA, the predicate reports unmoved and no third
   run happens; a head that outruns both attempts stops at `MAX_RERUN_ATTEMPTS` with `calibration_stale` set and the
   STALE summary line rendered.
4. Stale-signal lifecycle: with recording fakes, the stale comment posts once per `(evaluated, head)` pair (deduped on
   repeat checks and on a still-stale second attempt), the refreshed comment supersedes it after the rerun, and
   `_calibrated_head_line` renders all three states.

Plus: review-gate tests (in-flow rerun, standalone signal-only, silent without report/PR), double-trigger benignity
(both sites on one move → exactly one rerun), a pipeline wiring test (the Stage 5 result passes through
`rerun_calibration` and the refreshed result reaches `context.stage_results`), and a loop wiring test in
`tests/test_review_loop.py` (`calibration_freshness` in the loop result; the gate gets the PR number). No existing
assertion was modified; test changes are additive, as declared in the plan.

### Step 6: Module budgets, lint, docs lint and single full-suite validation (CONFIG)

- `flow_module_budgets.json`: added `calibrate_rerun.py` at creation size (285); raised `pipeline.py` `418 -> 431` (the
  `_rerun_if_calibrate` hook, extracted so `run()` stays within `PLR0912`), `review/loop.py` `907 -> 913` (gate call
  site), `github/ci_reverify.py` `91 -> 97` (`rerun_note`), `__init__.py` `466 -> 486` (new exports);
  `calibrate_rebuild.py` unchanged at 123 (pure rename). Description note records each reason and count.
- Lint fix-ups: `__all__` re-sorted (ruff `RUF022` autofix), one long test line wrapped, unused unpacked test variables
  renamed. No inline `noqa` added anywhere in the diff.
- Scope reconciliation: every changed file is in the plan's Touch Set. Listed-but-untouched (narrower than planned, no
  excursion): `stages/calibrate.py` (the rerun wrapper owns the staleness payload keys), `review/posting.py`,
  `calibration.py` (its helpers used as-is), `tests/test_calibrate_sync.py` (harness reused via import),
  `tests/test_calibration.py` (predicate tables live in `tests/test_calibrate_rerun.py`),
  `tests/flow/test_package_exports.py` (all new public names are exported, so no guard change). `uv.lock` untouched.

## Tests Run

No repetitions: nothing in this intent measures performance or timing, so the affected set ran once per verification
point and the full suite once (test-run discipline). All counts and times are pytest-reported from real runs.

| command                                                                                                                                                     | result                             | time     |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | -------- |
| `uv run pytest apps/sandbox-executor/tests/test_calibrate_rerun.py ...exports/rebuild/reverify` (step 2)                                                    | 151 passed, 0 failed               | `1.28s`  |
| `uv run pytest .../flow/test_pipeline.py .../test_review_loop.py .../test_calibrate_sync.py` (step 4)                                                       | 177 passed, 0 failed               | `3.63s`  |
| `uv run pytest` affected set: `test_calibrate_rerun test_calibrate_sync test_calibrate_rebuild test_calibration test_review_loop test_ci_reverify` (step 5) | 246 passed (+8 subtests), 0 failed | `4.89s`  |
| same affected set after Step 6 lint fix-ups                                                                                                                 | 246 passed (+8 subtests), 0 failed | `4.74s`  |
| `uv run task test` (full suite, once, `-n auto --dist worksteal`)                                                                                           | 4288 passed, 1 skipped, 0 failed   | `17.93s` |
| `uv run task lint` (ruff + format + flow module budgets: 62 modules within budget)                                                                          | pass                               | —        |
| `uv run task lint-docs` (prettier `--check "**/*.md"`)                                                                                                      | exit 0                             | —        |

Median full-suite time: `17.93s` (single run, as required). Median affected-set time: `4.82s` (of the two step 5/6
runs). `uv run task lint-docs` warns on 40 historical markdown files (pre-existing on the base commit, none touched by
this diff); the execution record itself was Prettier-formatted before committing and is not among them.
