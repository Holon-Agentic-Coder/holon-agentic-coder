# Execution Record: E-1793189685-claude-agent-claude-opus-5-5

## Summary

Implementation of plan `P-1793189449-claude-agent-claude-opus-5-5` for intent
`I-1793189433-review-loop-fail-closed-target-and-guards`: make the native Stage 4 review loop fail closed. All eight
steps were carried out on this execution branch; no reviewer dispatch, merge or auto-merge was added and no dependency
changed.

The host-side hang is explained: `_find_git_root()` never advanced `cur`, and a deadline-guarded test reproduced the
pure CPU spin before the one-line fix (root cause confirmed). The new `review_target.py` resolves branch, PR-number and
PR-URL targets to a head branch and head SHA and classifies the diff as `verified`, `empty` or `unverified`. In
`run_review_loop`, the empty-diff guard now runs for every non-dry target whatever `--auto-pr` is and whether or not
`gh` is installed. A failed or raising consensus post makes the loop `post_failed` (never approved). A dry run is never
approved (`not_measured`). The pending-CI re-probe is a bounded poll. The container entrypoint resolves the target
before any git operation, clones the head branch (never `--branch 90`), verifies `HEAD` against the head SHA, and on any
preparation failure writes one ACT-REV failure row (when a ledger exists), prints a FAILED summary and exits 1. The
targeted modules, `uv run task test` (2,526 passed), `uv run task lint` and `uv run task lint-docs` all pass.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 8.4   |
| impact_actual         | 80.0  |
| cost_actual           | 17.0  |
| learning_value_actual | 5.5   |

## Steps Executed

1. **Step 1 (`_find_git_root` hang): root cause confirmed.** `uv` was missing on the host and was installed with
   `pip install --user uv` (0.13.6), then `uv sync --locked`. Added two deadline-guarded tests to
   `apps/sandbox-executor/tests/test_cli.py` (daemon thread, `join(timeout=5)`, `.git` lookups confined to `tmp_path`).
   Before the fix both failed with "did not return within its deadline" and the run burned `user 9.46s` of CPU over
   `real 10.80s` (a pure spin, no child process: the observed symptom). Fix: `cur = parent` after the root check. After
   the fix: `2 passed in 0.06s`.
2. **Step 2 (`review_target.py`).** Added `apps/sandbox-executor/src/sandbox_executor/review_target.py`:
   `ReviewTargetError` (message redacted on construction), frozen `ReviewTarget` with `is_pr`, `is_pr_reference`,
   `github_repo_slug`, the `RunGh` seam with a bounded default (`timeout=60`, `check=False`, raises when `gh` is not on
   `PATH`), `resolve_review_target` (PR path: `gh pr view <t> --json number,headRefName,headRefOid,title,body`; branch
   path: reuses `flow._resolve_review_commit(..., fetch=True)` and expands to the full SHA), `DiffVerdict` and
   `verify_review_diff(repo_dir, target, base_branch="main")`. Execution branches delegate to
   `flow._execution_effective_diff`; other targets diff against `origin/<base>` (fetched when `origin` exists) with
   `changed_paths`; a raw PR string that was never resolved is `unverified`. Flow helpers are imported inside functions,
   so there is no circular import.
3. **Step 3 (`flow.py` call sites).** `_resolve_review_commit` now calls `resolve_review_target` for PR references
   (debug log and `HEAD` fallback on `ReviewTargetError`, as before). In `run_review_loop`: the
   `if auto_pr and ... shutil.which("gh")` gate became an unconditional `_review_diff_verdict(...)` call for every
   non-dry run (PR references are resolved first; a resolution failure is `unverified`). `empty` returns
   `_refused_review_result(...)` (status `rejected`); `unverified` keeps the `pr_refusal_error` path, now with status
   `failed`. A simulated approval in a dry run becomes `approved: False`, status `not_measured`; dry-run rejections stay
   rejections. A failed or raising post still writes the one ACT-POST failure row, then forces `approved False`, status
   `post_failed`, `score 0.0`, adds "consensus post failed" to the halt reasons and sets `error`. The single
   `time.sleep(15)` pending-CI re-probe became `_wait_for_pending_ci(...)`, a deadline poll bounded by the remaining
   `review_iteration` budget at the `ci_poll_interval` cadence (default `CI_REGISTRATION_POLL_SECONDS`).
   `run_review_loop`, `_wait_for_ci_registration` and `_wait_for_pending_ci` take injectable `clock` and `sleep`
   (resolved at call time so existing `flow.time.*` patches still apply). `run_review_loop_cli` prints the redacted
   `error` as `Reason:` when not approved. In `run_review_stage`, a dry run whose simulated loop would have approved
   still halts for a human (so `holon flow --dry-run` keeps its shape), but its consensus is `not_measured` and the log
   says it was not approved.
   - Existing tests changed because they encoded the fail-open behaviour (declared test changes): in `test_flow.py`, a
     `_verified_diff()` patch was added to the loops that run on non-git `tmp_path` directories
     (`TestStage4LedgerProvenance._run_two_iteration_loop`, `test_unwritable_ledger_warns_and_does_not_raise`,
     `TestStageTargetsIgnoreOperatorHead._run_loop_capturing_consensus`, `TestStage4TestEvidence._loop`,
     `TestUnmeasuredTestsStopEarly._loop`); `test_dry_run_keeps_its_simulated_consensus_without_waiting` now expects
     `approved False` and `not_measured`; `test_unverified_diff_never_approves_despite_green_evidence` now expects
     status `failed` instead of `rejected`. In `test_review_loop.py`, the two empty-diff guard `_run` helpers now stub a
     successful post (`{"dual_posted": True}`) instead of `{}`, which is a failed post.
4. **Step 4 (container entrypoint).** `entrypoint/review_loop.py` now runs `_prepare_workspace` before anything else:
   `_resolve_target` (PR references through `resolve_review_target` with a `_gh_runner` that sets `GH_REPO` from
   `github_repo_slug(HOLON_REPO_URL)` when there is no checkout yet; `HEAD` and empty targets are refused),
   `_clone_or_fetch` (`git clone --branch <head_branch>` in a fresh workspace, otherwise
   `git fetch origin +refs/heads/<b>:refs/remotes/origin/<b>` and `git checkout -B <b> origin/<b>`, every return code
   checked through `_checked`) and `_verify_head` (HEAD must equal the head SHA; on a mismatch `git fetch origin <sha>`
   plus `git checkout --detach <sha>`, then a re-check). Deviation from the plan, noted here: a branch target's head SHA
   is read with `git rev-parse refs/remotes/origin/<b>` through the same `run_cmd` seam rather than a second
   `resolve_review_target` call, so the whole preparation is testable at one seam. Any `ReviewPreparationError` goes to
   `_fail_preparation`: one ACT-REV failure row (`action_origin` `pr_review`, labels `review`, `review-loop`,
   `preparation`, `error_context` via `_bounded_error_context`) through
   `try_append_action_record(..., preserve_shape=True)` when `holon-knowledge/ledger/` exists, otherwise stderr only;
   then a FAILED summary (or a `--json` object with `consensus.status` `failed`) and exit 1. A PR stays the loop target;
   the head branch is the execution branch and the key for the `intents.jsonl` and `plans.jsonl` lookups. The final
   summary prints the loop `error` as `Reason` when not approved. Existing entrypoint tests that create a fake `.git`
   directory now patch `_prepare_workspace` (declared test change: their fake checkout can no longer pass a real
   `git fetch`).
5. **Step 5 (bounded waits audit).**

   | loop or call                                      | bound before                                                     | change                                                               |
   | ------------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------- |
   | `_find_git_root` (`cli.py`)                       | none: `cur` never advanced (root cause)                          | fixed in Step 1; ends at the filesystem root                         |
   | `_wait_for_proxy` (`cli.py`)                      | monotonic deadline plus `PROXY_POLL_INTERVAL_SECONDS`            | none                                                                 |
   | `_wait_for_ci_registration`                       | monotonic deadline plus sleep                                    | injectable `clock` and `sleep`                                       |
   | pending-CI re-probe                               | one fixed `time.sleep(15)`                                       | `_wait_for_pending_ci`, deadline is the `review_iteration` remainder |
   | `check_pr_mergeability` retries                   | `range(3)` with `time.sleep(2)`, but `gh pr view` had no timeout | `timeout=60` added (timeout falls back to local git)                 |
   | `probe_ci_checks`                                 | `timeout=30.0`                                                   | none                                                                 |
   | `post_pr_review_consensus`, `create_or_update_pr` | `timeout=60` on every `gh` call                                  | none                                                                 |
   | `LedgerLock.__enter__`                            | monotonic timeout with exponential back-off sleep                | none                                                                 |
   | review iteration loop                             | `max_iterations` plus the per-iteration budget                   | none                                                                 |
   | `resolve_review_target` (new)                     | `timeout=60` on `gh pr view`                                     | new                                                                  |

6. **Step 6 (tests).** New `apps/sandbox-executor/tests/test_review_target.py` (38 cases): the `is_pr_reference` and
   `github_repo_slug` truth tables; PR number and PR URL resolving to `headRefName` and `headRefOid` with the exact
   `--json` field list; `ReviewTargetError` for non-zero exit, timeout, missing binary, invalid JSON, empty branch,
   empty SHA, the default runner without `gh`, and a redacted gh error; branch targets on local `tmp_path` repositories;
   and `verify_review_diff` verdicts (execution branch empty and verified, unresolved PR `unverified`, PR head equal to
   base `empty`, PR head with a change `verified`, missing PR head and missing base `unverified`, plain branches, no
   `gh` needed). Additions to `test_review_loop.py`: target `90` clones `--branch feature/x` and never `--branch 90`; an
   existing checkout fetches and checks out the branch; a failing clone exits 1, calls neither the loop nor posting and
   writes exactly one ACT-REV failure row (stderr only without a ledger); a failing resolution exits 1 before any git
   command (`--json` reports `failed`); a HEAD mismatch fetches the SHA, and a failing SHA fetch exits 1; `GH_REPO`
   carries no credentials; the guard runs with `auto_pr=False` with and without `gh`, and for a PR number; an
   unresolvable PR fails closed; returned and raised post failures give `post_failed` with one ACT-POST failure row, and
   `run_review_loop_cli` returns 1; a dry run is never approved; an approved loop through the real posting code issues
   no `pr merge` or `--auto` (and `gh_write_guard` stays empty); the pending-CI wait honours its deadline with an
   injected clock and sleep, and returns early once checks settle; the mergeability probe carries `timeout=60`.
   Additions to `test_cli.py`: the two `_find_git_root` deadline tests from Step 1 and `holon review-loop --local`
   exiting 1 when not approved.
7. **Step 7 (docs and help).** Updated the review-loop section of `apps/sandbox-executor/README.md` (resolution, clone,
   empty-diff guard, posting, dry runs, exit code, bounded waits, and the no-ledger limitation), the empty-execution and
   unmeasured-tests limits in `docs/git_flow.md`, the review iteration paragraph in `docs/budgets.md`, and the Stage 4
   row of the ledger table in `docs/ledger_schema.md`. Help strings changed for the `review-loop` subparser and
   `--auto-pr` in `cli.py` and for `--auto-pr` in the entrypoint. Formatted with `npx prettier --write` on the touched
   markdown files only.
8. **Step 8 (validation).** See Tests Run. A grep of the added lines for `pr merge`, `--auto` and `enable-auto-merge`
   finds only the `--auto-pr` flag in docs and the no-merge test assertion; no harness paths, bean ids or review ids
   were added.

## Tests Run

| command                                                                                                                                                                                            | result                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `uv run pytest apps/sandbox-executor/tests/test_cli.py -k git_root -p no:xdist` (before the fix)                                                                                                   | 2 failed (deadline exceeded), `user 9.46s` over `real 10.80s`         |
| `uv run pytest apps/sandbox-executor/tests/test_cli.py -k git_root -p no:xdist` (after the fix)                                                                                                    | 2 passed in 0.06 s                                                    |
| `uv run pytest apps/sandbox-executor/tests/test_review_target.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py` | 777 passed, 53 subtests passed (before the last two tests were added) |
| `uv run task test` (once)                                                                                                                                                                          | 2526 passed, 8 warnings in 29.58 s                                    |
| `uv run task lint`                                                                                                                                                                                 | passed (`ruff check` and `ruff format --check`, lock unchanged)       |
| `uv run task lint-docs`                                                                                                                                                                            | passed                                                                |
