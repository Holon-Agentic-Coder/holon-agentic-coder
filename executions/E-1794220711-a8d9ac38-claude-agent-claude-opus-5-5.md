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
