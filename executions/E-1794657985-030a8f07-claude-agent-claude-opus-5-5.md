# Execution Record: E-1794657985-030a8f07-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794657809-c6ebdbd6-claude-agent-claude-opus-5-5` for intent `I-1794657793-mergemth`: a child pull
request is merged into its plan branch with a merge commit when the repository allows it, otherwise with a squash,
read from `gh api repos/{owner}/{repo}` before anything is written. A failed lookup or no allowed method refuses; the
existing guards are unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Confirm the merge-method facts and the test seams

- `gh api repos/{owner}/{repo}` returns the REST repository object; `allow_merge_commit`, `allow_squash_merge` and
  `allow_rebase_merge` are booleans present only for a token with push access, so a missing or non-boolean value is
  treated as not allowed. No real `gh` call was made.
- Files pinning the old flag: `tests/flow/fake_gh.py` (`_pr_merge` accepts only `--merge`),
  `tests/flow/github/test_merge.py` (`FORBIDDEN_FLAGS` includes `--squash`; exact `--merge` call asserted),
  `tests/flow/stages/test_children.py` line 79 (`--merge` and no `--squash` in the decomposed flow).
- `github/merge.py` is 299 lines against a 299 budget; every module under `flow/` needs a budget entry.
- `tests/flow/test_package_exports.py` requires every new public function to be in `flow.__all__`, so the two new
  public functions are re-exported from `flow/__init__.py` (its budget raised by the exact delta, as the budgets
  description records for earlier modules).

### Step 2: Add the merge-method lookup module

- Added `flow/github/merge_method.py` (64 lines): `choose_merge_flag` (pure, strict `is True`, merge commit preferred,
  rebase never consulted) and `lookup_merge_flag` (one `gh api repos/{owner}/{repo}` call through the injected runner;
  non-zero exit, empty output or invalid JSON refuses; stderr redacted). It imports nothing from `merge.py`.
- Budgets: `github/merge_method.py: 64` added at creation size; `__init__.py` raised from 425 to 428 for the re-export
  the package export test requires. `ruff check`, `ruff format --check` and the budget check pass.

### Step 3: Use the looked-up flag in `merge_child_pr`, before anything is written

- `merge_child_pr` calls `lookup_merge_flag(repo_dir, gh)` after the idempotent `MERGED` branch and before
  `_verified_head`, so a refusal (`no usable merge method for PR #<n>: ...`) writes nothing to origin. The
  `gh pr merge` call is `["pr", "merge", <n>, <flag>, "--match-head-commit", <head_sha>]`.
- `merge_refusal_reason`, `_guard`, `_verified_head`, `_await_ready`, `merge_into_branch` and `_merged_result` are
  unchanged (diff limited to the import, the lookup, the flag and docstrings).
- Deviation: `merge.py` could not stay at 299 lines without unrelated reformatting, so its budget was raised by the
  exact delta (`299 + 4 = 303`) with a sentence in the budgets description, as the plan allows.

### Step 4: Extend the fake `gh` and add hermetic tests

- `tests/flow/fake_gh.py`: `allow_merge_commit`, `allow_squash_merge`, `allow_rebase_merge` and `repo_settings_fail`
  settings, an `api repos/{owner}/{repo}` handler, and a `_pr_merge` that accepts exactly one allowed method, refuses
  `--rebase`, and performs a real single-parent squash on origin for `--squash`. Defaults keep the old behaviour.
- `tests/flow/github/test_merge.py`: `--squash` removed from `FORBIDDEN_FLAGS` and asserted per case; new tests for a
  merge commit when allowed (both allowed and merge-only), squash-only, neither allowed (including rebase-only and
  non-boolean settings), lookup failure (HTTP error, invalid JSON, a non-object, missing fields) with nothing pushed,
  and `main` and the default branch still refused on a squash-only repository.
- `tests/flow/github/test_merge_method.py` (new): pure tests of `choose_merge_flag` and stub-runner tests of
  `lookup_merge_flag` (exact argument list, fail-closed cases, redaction of a token in stderr).
- `tests/flow/stages/test_children.py`: a decomposed flow on a squash-only repository squashes both children and the
  second child is cut from the first squash commit.
- Affected tests: 226 passed in 3.48s.
