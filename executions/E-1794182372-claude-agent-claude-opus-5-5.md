# Execution Record: E-1794182372-claude-agent-claude-opus-5-5

- **Plan ID:** P-1794182135-claude-agent-claude-opus-5-5
- **Intent ID:** I-1794182121-validate-target-worktree-is-git-worktree
- **Agent:** claude-agent/claude-opus-5-5
- **Started At:** 2026-11-08

## Summary

Adds fail-fast validation that the resolved `--target-worktree` of `holon flow` (after auto-provisioning, and the
`target_worktree` restored from a `--checkpoint` resume) is an attached, non-bare git working tree, plus an injectable
`PipelineEngine` guard wired by `run_flow_pipeline()`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.95  |
| entropy_actual        | 6.0   |
| impact_actual         | 70.0  |
| cost_actual           | 11.0  |
| learning_value_actual | 4.0   |

Rationale: all five intent goals and acceptance criteria are delivered and verified. `success_actual = 0.95`, not 1.0,
because of three small, documented deviations: the `--skip-push` check was reordered, one existing test fixture (not its
assertions) was changed, and the engine validator is an attribute instead of a constructor keyword. Entropy stayed under
the predicted `7.0`. The only surprises were the two pre-existing tests whose checkpoints recorded plain directories,
and the `PLR0913` probe. `EV_actual = 0.95 * 70.0 + 0.5 * 4.0 - 0.3 * 6.0 - 11.0 = 66.50 + 2.00 - 1.80 - 11.00 = 55.70`.

## Steps Executed

### Step 1: Shared target-worktree validator in `git_flow`

Added `target_worktree_problem(target_worktree, subject="Target worktree", hint="")` plus a private
`_target_worktree_reason()` built only on `is_bare_repository()` and `is_working_tree()`. The message comes from the
module-level `TARGET_WORKTREE_PROBLEM_TEMPLATE`, names the absolute path and reason, and points to
`git worktree add <path> <branch>` or a bare clone. `subject` and `hint` let the CLI word the checkpoint variant without
the helper knowing about checkpoints. Note: `uv` was not on `PATH` in the sandbox, so it was installed with
`pip install --user uv` and `uv sync` was run; `uv.lock` is unchanged.

### Step 2: Validate the resolved target after auto-provisioning in the flow CLI

In `cli/commands/flow.py`, `_ensure_flow_worktree()` now returns `bool`. Branch selection moved to
`_flow_target_branch()`, and provisioning (with `RuntimeError`/`ValueError` caught and logged, redacted) moved to
`_provision_flow_worktree()`. `_should_provision()` limits auto-provisioning to a bare clone or an absent CLI target, so
a plain non-git directory is never handed to `ensure_worktree()` and its cwd fallback. After that, the final path goes
through `target_worktree_problem()`. On a problem the command prints `Error: <message>`, calls `sys.exit(1)` and returns
`False`, and `run()` returns before the preflight gate.

Deviation: the `--skip-push` outside `--dry-run` rejection was moved out of `_check_dry_run_and_preflight()` into a new
`_check_skip_push()` that runs _before_ the worktree check. Without this, the existing
`test_flow_skip_push_rejected_outside_dry_run_for_any_resume` failed: its checkpoint records a plain temporary
directory, so the worktree error replaced the expected skip-push error. Argument-level errors now come first, and the
worktree check still runs before preflight and before any stage.

### Step 3: Validate the `target_worktree` restored from a checkpoint resume

`_effective_flow_target()` mirrors `_update_flow_context()`: an explicit `--target-worktree` other than `"."` wins,
otherwise a checkpoint's recorded `target_worktree` is used. A checkpoint-restored target is auto-provisioned only when
it is bare, and in that case only `context.target_worktree` is updated. An absent or plain recorded path exits 1. The
message starts `Checkpoint '<file>' target_worktree '<path>' ...` and ends with the hint to pass `--target-worktree`.

Deviation (fixture only): `test_flow_checkpoint_resumption_flag_overrides` recorded a plain temporary directory as the
checkpoint's `target_worktree`. Under acceptance criterion 2 that resume is now correctly rejected, so the fixture runs
`git init -q` on the directory first. None of the test's assertions changed. Steps 2 and 3 share one function, so they
were committed together.

### Step 4: Injectable worktree guard on `PipelineEngine`

`PipelineEngine` gained a `worktree_validator` attribute (default `None`) and a `_pre_run_guard_error()` helper that
returns the existing `SKIP_PUSH_DISPATCH_ERROR` first, then the validator's message for `context.target_worktree`. The
inline skip-push block in `run()` now calls the helper and keeps the same `_stage_failure()`, halt log and no-checkpoint
return, with no new `# noqa`. `run_flow_pipeline()` sets `engine.worktree_validator = target_worktree_problem`, imported
from `sandbox_executor.git_flow`. No entrypoint import was added.

Deviation: the validator is an attribute, not a constructor keyword. A probe confirmed that ruff's `PLR0913` counts
keyword-only parameters too (`6 > 5`), so the plan's documented class-attribute fallback was used. Direct
`PipelineEngine(...)` tests are unaffected.

### Step 5: Hermetic tests

Every fixture builds real repositories under `tmp_path` and pins the cwd to a non-git `tmp_path/outside` with
`monkeypatch.chdir`. No network, remote or Docker is used.

- `tests/test_cli.py` (local `flow_target_env` fixture, 10 tests): a plain directory and a deleted path are rejected
  with exit 1, the pipeline is not called, and no `flow-*` sibling is created. An attached worktree is accepted. A
  checkpoint recording a plain or deleted path is rejected with and without `--from-stage review` (4 parametrised
  cases), checked with the checkpoint-prefixed message. An explicit valid `--target-worktree` overrides a bad checkpoint
  target. A checkpoint recording a bare clone still provisions `flow-wt-check`.
- `tests/test_flow.py` (`TestTargetWorktreeValidation`, 11 tests): `target_worktree_problem()` returns `None` for a
  source repository, an attached worktree and a worktree subdirectory. It gives the expected reason, absolute path and
  `git worktree add` fix for a bare clone, a plain directory, a file and a missing path, and honours the caller's
  subject and hint. The engine guard fails the first stage with the message, runs no stage and writes no checkpoint. An
  engine without a validator keeps arbitrary targets. `run_flow_pipeline()` wires the validator (it rejects a plain
  directory and accepts an attached worktree). The CLI still provisions a bare target after validation. The existing
  `test_flow_cli_auto_creates_worktree_from_bare_repo` is unchanged.

### Step 6: Lint and test gates

All gates passed once each; see below. The intent has no timing target, so no repeated runs were made.

## Tests Run

| command                                                                                                                   | result                                 | pytest time |
| ------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- | ----------- |
| `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py -n auto --dist worksteal` | 755 passed, 0 failed (53 subtests)     | 5.23s       |
| `uv run task test` (full suite, once)                                                                                     | 3100 passed, 0 failed                  | 30.20s      |
| `uv run task lint`                                                                                                        | exit 0                                 | n/a         |
| `uv run task lint-docs`                                                                                                   | exit 0 (after prettier on this record) | n/a         |

Median of the single full-suite run: 30.20s. Before the full run, an intermediate run of the whole test directory with
`-x` at the Step 4 commit gave 3082 passed, 14 skipped in 46.63s (before the Step 5 tests were added).
