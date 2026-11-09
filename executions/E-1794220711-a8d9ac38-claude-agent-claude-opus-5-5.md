# Execution Record: E-1794220711-a8d9ac38-claude-agent-claude-opus-5-5

## Summary

Execution of plan `P-1794220535-37b66e75-claude-agent-claude-opus-5-5` for intent
`I-1794220515-run-ci-on-pull-requests-into-any-branch`: run the CI workflows for pull requests into any base branch so
stacked sub-intent pull requests receive checks, guard the rule with a test, and pin Stage 4 check and mergeability
resolution for a stacked pull request.

In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Remove the base-branch filter from the pull_request trigger

- Deleted only the `branches:` key and its `- main` item under `pull_request:` in `docker-build.yml`, `make.yml`,
  `test-hygiene.yml`, `test-integration.yml`, `test-stress.yml` and `test-unit.yml`.
- `git diff --stat .github/workflows`: six files changed, `12` deletions, no insertions (`-2/+0` per file).
  `push: branches: [main]`, `types`, `workflow_dispatch`, `permissions` and `concurrency` are byte-for-byte unchanged;
  `docker-build.yml` gained no `concurrency`; `build-images.yml` untouched.

### Step 2: Standard-library guard test for workflow triggers

- New `apps/sandbox-executor/tests/test_workflow_triggers.py` (only `pathlib`, `re`, `pytest`; no marker, hermetic).
  It locates `.github/workflows` by walking up from the test file, asserts the file list is non-empty, and reads the
  top-level `on:` block with a small indentation-aware reader (block and flow lists, `on: pull_request`,
  `on: [push, pull_request]`, quoted `"on":`, YAML 1.1 `true:`, comments).
- Guards: no `branches`/`branches-ignore` under `pull_request` or `pull_request_target`; when `push` has `branches`, it
  is exactly `main`. Seven in-memory snippet cases pin the reader itself.
- Fail-first check: with the six pre-change workflow files restored from `HEAD~1`, the run gave `6 failed, 16 passed`,
  failing exactly the six files' `pull_request` guard; after restoring Step 1, `22 passed in 0.02s`.

### Step 3: Confirm and pin Stage 4 check and mergeability resolution for a stacked pull request

- Audit (grep for `main` in `flow/github/ci.py`, `flow/github/pr.py`, `flow/review/*.py`, `flow/stages/review.py`):
  the planning audit holds. `flow/review/loop.py:215` reads `target_branch` from `context.intent_data` and threads it
  into `classify_review_diff`, `create_or_update_pr` (`--base`), `check_pr_mergeability` and the ensemble's base ref.
  The `"main"` literals in `flow/github/pr.py:48,423` are parameter defaults the loop always overrides; `ci.py` probes
  `gh pr checks <head>` and never names a base. The `"main"` fallbacks for a missing `target_branch` key
  (`loop.py:215`, `stages/review.py:90,185,213`) apply only when the intent records no target, which decomposition
  always sets. No source change was needed, so the contingency path was not taken and the flow module budgets are
  unchanged.
- New `apps/sandbox-executor/tests/test_review_stacked_pr.py` (4 tests):
  - Test A drives `run_review_loop` end to end with a fake `gh` (`subprocess.run` dispatcher; git calls still run
    for real), `shutil.which` reporting `gh`, a fake clock and sleep. `gh pr view` first reports no PR, so
    `gh pr create --base I-1700000000-parent/_` runs; `gh pr checks` fails (not registered), then reports one pending
    check, then all passing. Asserts the `--base`, the empty-diff guard base and the reviewers' base ref are the parent
    intent branch, no `gh` argument is `main`, at least three check probes, approval with `github_cli` CI, and the
    Stage 4 summary row in the temporary ledger carries the checks.
  - Test B builds a real git repository with only the parent intent branch and a stacked child (no `main`), `gh`
    absent: `MERGEABLE`/`CLEAN`, then after a conflicting parent commit `CONFLICTING`/`DIRTY`.
  - Test C (parametrised on `main` and the parent branch) shows `probe_ci_checks` returns the same summary whatever
    the pull request's base.
- Mutation check: temporarily passing `target_branch="main"` to `create_or_update_pr` in `loop.py` made Test A fail;
  the source was restored byte-for-byte. Result: `4 passed in 0.08s`.

### Step 4: Document that pull requests into any branch run the CI checks

- `.github/workflows/README.md`: the five workflow entries now read "on pull requests into any base branch",
  `docker-build.yml` gained the same trigger statement, and a new "Pull requests into any branch" subsection explains
  stacked sub-intent pull requests, the former `test results not measured` halt, why `push` stays on `main` (no commit
  checked twice), that the timing gate still compares against `main` baselines, and the guard test.
- `docs/git_flow.md`: the Stage 4 "Unmeasured tests are never a pass" paragraph now states that stacked sub-intent
  pull requests receive the same checks because every CI workflow runs for pull requests into any base branch.
- Grep of `docs/` for other "CI runs only on pull requests into `main`" statements found none. Both files formatted
  with `npx prettier --write`.
