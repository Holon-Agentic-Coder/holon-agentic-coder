# Execution Record: E-1794125099-claude-agent-claude-fable-5

## Summary

Implementing plan `P-1794124844-claude-agent-claude-fable-5` for intent
`I-1794124652-post-consensus-review-once-per-pr-exit`: rewrite `post_pr_review_consensus()` in
`apps/sandbox-executor/src/sandbox_executor/flow.py` as a single-post fallback ladder (`gh pr review --approve` → 422
fallback to `gh pr review --comment` → last-resort `gh pr comment` only on review failure) and update the hermetic unit
tests in `apps/sandbox-executor/tests/test_flow.py` and `apps/sandbox-executor/tests/test_review_loop.py` to assert
exactly-once posting.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 5.0   |
| impact_actual         | 70.0  |
| cost_actual           | 10.0  |
| learning_value_actual | 3.0   |

## Steps Executed

### Step 1: Baseline verification and consumer enumeration

- Confirmed the dual-post implementation in `post_pr_review_consensus()`
  (`apps/sandbox-executor/src/sandbox_executor/flow.py` lines 1498–1684): unconditional "Action 1" `gh pr comment`
  followed by "Action 2" `gh pr review`, with the 422 self-approval predicate (`"422"` /
  `"Can not approve your own pull request"` / `"PullRequestReviewAuthorError"`).
- Confirmed exactly two production consumers of the return dictionary: `_posting_failure_context()` (flow.py:3363,
  iterates `discussion_comment` and `official_review`) and the ACT-POST block (`posted` expression at flow.py:4739
  reading `dual_posted` / `official_review.success`). A repo-wide grep found no third consumer.
- Enumerated directly affected tests: six unit tests in `tests/test_flow.py` (~1732–1859), five in
  `tests/test_review_loop.py` (~300–406), plus one additional in-scope test not listed in the plan:
  `test_reviewer_data_block_reaches_the_body_passed_to_gh` (test_flow.py:5875), which drives the real function through
  `run_review_loop` and asserts `len(bodies) == 2` — it must also be adapted to single posting (still within the
  intent's declared scope file `apps/sandbox-executor/tests/test_flow.py`).
- Baseline run (from repo root):
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py -n auto --dist worksteal`
  → `438 passed in 5.22s`.

### Step 2: Rewrite `post_pr_review_consensus()` as a single-post fallback ladder

- Deleted the unconditional "Action 1: Top-Level Discussion Comment" block; the
  `gh pr review <target> {--approve|--comment} --body <report>` submission is now the primary and normally only post
  (timeout 60, `cwd=repo_dir`, `gh_env`, `check=False` unchanged).
- Kept the `approved` derivation (ensemble verdict plus completed-reviewer guard) and the 422 self-approval predicate
  byte-identical; the 422 fallback to `gh pr review --comment` still sets `self_approval_fallback = True` and
  `flag = "--comment"`.
- Added the last-resort `gh pr comment` that runs only when the review ladder ends without a successful submission
  (non-422 `--approve` failure, failed `--comment` submission, failed 422 retry, or a subprocess exception); it is
  recorded under the preserved `discussion_comment` key with `attempted: True` only in that branch.
- Preserved every return key; `dual_posted` now means "the report was posted exactly once"
  (`review_success or comment_success`), keeping the ACT-POST `posted` expression at flow.py:4739 and
  `_posting_failure_context()` untouched and truthful.
- Rewrote the docstring in Australian English for single-post semantics; updated the `diagnostics` strings
  (`Review consensus posted once to GitHub.` / `Review posting failed: review=..., comment=...` / gh-missing message now
  says "posting").
- `uv run ruff check` confirms the existing `noqa: PLR0912, PLR0913, PLR0915` codes are all still exercised (RUF100 is
  enabled and reports no unused suppressions); formatting clean.

### Step 3: Update hermetic unit tests to assert exactly-once posting

- `tests/test_flow.py`:
  - `test_post_pr_review_consensus_dual_posting` renamed to `test_post_pr_review_consensus_single_post`: asserts
    `call_count == 1`, the only call is `gh pr review 42 --approve`, `dual_posted` True, `discussion_comment.attempted`
    False.
  - `test_post_pr_review_consensus_self_approval_422_fallback`: side effects now [422-failing approve, succeeding review
    comment]; asserts `call_count == 2`, both calls are `gh pr review`, `self_approval_fallback` True, flag `--comment`,
    no `gh pr comment`.
  - `test_post_pr_review_consensus_comment_failure_proceeds_to_review` repurposed as
    `test_post_pr_review_consensus_review_failure_falls_back_to_comment`: failed review `--comment` submission followed
    by a succeeding last-resort `gh pr comment`; asserts review-first ordering, `discussion_comment.attempted`/`success`
    True, `dual_posted` True.
  - `test_post_pr_review_consensus_non_422_failure_no_fallback`: now exercises a genuine `--approve` non-422 failure
    (APPROVED verdict plus completed reviewer); asserts no `gh pr review --comment` retry, `self_approval_fallback`
    False, and the last-resort `gh pr comment` running second.
  - `test_post_pr_review_consensus_when_gh_missing_returns_diagnostics` unchanged (still green).
  - `test_post_pr_review_consensus_error_output_surfaces_diagnostic_context`: side effects now [failing review, failing
    last-resort comment]; exit-code and stderr surfacing assertions kept; diagnostics assertion updated to
    `Review posting failed`.
  - `test_reviewer_data_block_reaches_the_body_passed_to_gh` (data seam, drives the real function): now asserts exactly
    one posted body via `gh pr review ... --comment`.
  - `test_act_post_records_returned_failure_diagnostics`: stub fixture diagnostics aligned to the new
    `Review posting failed: review=..., comment=...` phrasing.
  - Class docstring updated from "dual-posting" to "single-post"; ledger wording tests (`Review consensus dual-posted`,
    label `dual-post`) deliberately untouched.
- `tests/test_review_loop.py`:
  - `test_post_pr_review_consensus_detects_gh_and_executes_commands`: `call_count == 1`, review call first,
    `discussion_comment.attempted` False.
  - The two never-approve guard tests: review call moved from `call_args_list[1]` to `call_args_list[0]`; assertions
    otherwise unchanged.
  - `test_post_pr_review_consensus_diagnostics_when_gh_missing` unchanged in substance.
  - `test_post_pr_review_consensus_diagnostics_when_gh_fails_nonzero`: side effects now [failing review, failing
    last-resort comment]; `dual_posted` False and diagnostics wording updated to `Review posting failed`.
- Tests patching the function with `return_value={"dual_posted": True}` left untouched (preserved key keeps them
  forward-compatible).
- Targeted run:
  `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py -n auto --dist worksteal`
  → `438 passed in 5.04s`.

### Step 4: Validation — targeted tests, full suite once, lint gates

- Targeted tests green (see Step 3 run), full suite green once via `uv run task test`, `uv run task lint` clean.
- `uv run task lint-docs` initially flagged only this execution record; reformatted it with
  `npx prettier --write executions/E-1794125099-claude-agent-claude-fable-5.md`, after which `uv run task lint-docs`
  exits zero. No source or test file needed lint fixes.

### Step 5: Commit and execution artefact

- Verified the diff touches only `apps/sandbox-executor/src/sandbox_executor/flow.py`,
  `apps/sandbox-executor/tests/test_flow.py`, `apps/sandbox-executor/tests/test_review_loop.py` and this execution
  record; no ledger file under `holon-knowledge/ledger/` was modified.
- Work committed per step on the execution branch; no push, rebase, amend or branch switch. Merging stays with the human
  maintainer (safety.md Invariant 4).
- Predicted versus actual: the plan predicted `p_success_pred = 0.90`, `entropy_pred = 5.8`, `cost_pred = 12.0`; the
  execution landed first-try green (`success_actual = 1.0`) with slightly lower entropy (`entropy_actual = 5.0`, the
  function shrank rather than grew) and cost (`cost_actual = 10.0`, no re-run loops were needed). One scope note for
  calibration: the plan's test enumeration missed `test_reviewer_data_block_reaches_the_body_passed_to_gh`
  (test_flow.py:5875), which exercises the real function and asserted two posted bodies; it sits inside the intent's
  declared scope file and was adapted in Step 3.

## Tests Run

All runs from the repository root. This intent has no performance or timing target, so each verification ran once
(targeted first, then the full suite once), per the test-run discipline.

| command                                                                                                                                                  | result                                                | time     |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | -------- |
| `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py -n auto --dist worksteal` (baseline, pre-change) | `438 passed`                                          | `5.22s`  |
| `uv run pytest apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_loop.py -n auto --dist worksteal` (post-change)          | `438 passed`                                          | `5.04s`  |
| `uv run task test` (full suite, once)                                                                                                                    | `3047 passed, 8 warnings`                             | `30.06s` |
| `uv run task lint`                                                                                                                                       | `All checks passed!` (exit 0)                         | n/a      |
| `uv run task lint-docs`                                                                                                                                  | `All matched files use Prettier code style!` (exit 0) | n/a      |

Median pytest-reported time across the three pytest runs: `5.22s` (targeted runs `5.22s`, `5.04s`; full suite `30.06s`).
