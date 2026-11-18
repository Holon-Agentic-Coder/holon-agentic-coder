# Plan for I-1794182121-validate-target-worktree-is-git-worktree

- **Plan ID:** P-1794182135-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-08T23:55:36.437Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (light)
- **Reframed intent (if applicable):** Accept all five goals as written, with three precise adjustments found by reading
  the code: (a) put the single validation helper, `target_worktree_problem()`, in `sandbox_executor/git_flow.py` (the
  module that already owns `is_working_tree()` and `is_bare_repository()`, 218 lines) rather than growing `flow.py`
  (6161 lines), and have both the CLI and `PipelineEngine` call it. This adds one module to the declared scope. (b) A
  plain, existing, non-git directory is rejected _before_ `ensure_worktree()` is called, because
  `_resolve_base_git_dir()` walks up the parent directories and then falls back to the current working directory's
  repository. Without this early rejection a plain directory would be "successfully" provisioned from whatever
  repository the operator ran `holon flow` in, so the post-provisioning check could never reject it. Bare and absent
  targets keep the current auto-provisioning exactly as today. (c) The engine guard is an injectable validator on
  `PipelineEngine` (defaulting to off), which `run_flow_pipeline()` wires to `target_worktree_problem()`, so the 49
  existing direct `PipelineEngine(...)` unit tests that use non-git `repo_dir` values such as `/tmp/repo` keep passing
  unchanged.
- **Exploration stance:** conservative. This is a small fail-fast guard on a well-understood code path, and existing
  hermetic git fixtures already cover it. The only uncertainty worth exploring is the cwd fallback in
  `_resolve_base_git_dir()`, and the plan handles it deterministically, not by experiment.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches host-side git worktree provisioning
  (`holon-config/world/constraints.md` §1 Git Flow & Branch Constraints, `docs/safety.md` "Git is the safety boundary").
  The cwd fallback in `_resolve_base_git_dir()` means a careless test or code path could create worktrees or branches in
  the developer's real repository. Hermetic tests must therefore pin the cwd to a non-git temporary directory. No
  ledger, merge or governance surface is touched.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Reading the code already settled the open questions (where the checkpoint target bypasses
  validation, what `ensure_worktree()` raises, why the engine guard must be injectable). Every step is therefore
  EXPLOIT. The learning value comes from calibrating how accurately a small cross-module guard is predicted, not from
  exploratory steps.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.84  |
| entropy_pred        | 7.0   |
| impact_pred         | 70.0  |
| cost_pred           | 12.5  |
| learning_value_pred | 4.5   |
| ev_pred             | 46.45 |

## Plan Description & Strategy

Findings from reading the current code that shape the plan:

1. `cli/commands/flow.py::_ensure_flow_worktree()` only inspects `args.target_worktree` (default `"."`). On a
   `--checkpoint` resume without an explicit `--target-worktree`, `"."` is checked (usually the operator's repository,
   so the function returns early). Meanwhile `_update_flow_context()` keeps the checkpoint's recorded
   `context.target_worktree`, which is never validated. That is the exact hole behind the late Stage 4/5
   `fatal: this operation must be run in a work tree`.
2. `git_flow.ensure_worktree()` signals all failures as `RuntimeError` (through `_resolve_base_git_dir()` and
   `run_git(check=True)`) or `ValueError` (empty slug). These currently fall through to the generic
   `Error during flow execution: ...` handler, which gives no actionable guidance.
3. `_resolve_base_git_dir()` walks up from the target, then falls back to `git rev-parse` in `"."`. For a plain non-git
   directory it can therefore resolve the _current working directory's_ repository and create `<parent>/flow-<slug>`
   from it. That is a silent mis-provisioning, not a rejection.
4. `PipelineEngine.run()` already has a pre-stage guard (the `--skip-push` without `--dry-run` rejection) that fails the
   first stage without running it or writing a checkpoint. The new worktree guard should use the same mechanism. `run()`
   already carries `# noqa: C901, PLR0912, PLR0915`, so the guard goes in a new helper method and no new suppression is
   added.
5. Existing CLI tests patch `sys.exit` with a mock, so every new exit path must follow the module's existing idiom of
   `sys.exit(1)` followed by an explicit `return` of a falsy value.

Strategy: one shared, side-effect-free validator in `git_flow.py`, then three call sites (CLI post-provisioning, CLI
checkpoint resume, engine guard), then hermetic tests, then the lint and test gates. The work is executed as a single
plan on one execution branch: total entropy (7.0) is well inside the 15.0 budget and the steps are tightly coupled
through one helper.

Touch set (module granularity):

- `apps/sandbox-executor/src/sandbox_executor/git_flow.py`: new `target_worktree_problem()` plus the actionable message
  builder (this module is not in the intent's scope list; it is added and justified above).
- `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`: resolution of the effective target, a guarded
  provisioning call, and a fail-fast exit.
- `apps/sandbox-executor/src/sandbox_executor/flow.py`: injectable `worktree_validator` on `PipelineEngine`, a
  `_pre_run_guard_error()` helper and the wiring in `run_flow_pipeline()`.
- `apps/sandbox-executor/tests/test_cli.py` and `apps/sandbox-executor/tests/test_flow.py`: new hermetic tests (test
  changes are declared here, as required by `holon-config/world/ruleset.md` §3).

---

## Step 1: Add a shared target-worktree validator to git_flow

- **Sub‑intent recommendation:** NO
- **Reasoning:** A pure function of about 30 lines built on two existing helpers. It is low risk and reused by the three
  later steps, but too small to justify its own branch and evaluation cycle.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the CLI and the engine one authoritative answer to "is this path an attached, non-bare git
  working tree, and if not, why not and how do I fix it?", without duplicating git invocations.
- **Git branch:**
  `I-1794182121-validate-target-worktree-is-git-worktree/P-1794182135-claude-agent-claude-opus-5-5/E-{timestamp}-validate-target-worktree/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `git_flow.py`, add a public `target_worktree_problem(target_worktree: str) -> str | None` with a full docstring
   (args, returns, no side effects other than read-only `git rev-parse`). It returns `None` when the path is valid,
   otherwise one actionable message string.
2. Decision order, reusing the existing helpers only:
   - empty, or `not os.path.exists()`: reason "does not exist (it may have been deleted)";
   - `not os.path.isdir()`: reason "is not a directory";
   - `is_bare_repository()`: reason "is a bare repository with no working tree";
   - `not is_working_tree()`: reason "is not inside a git working tree".
3. Build the message from a module-level template constant so the CLI and the engine word it identically. It contains
   the absolute path (`os.path.abspath`, target-native and never rewritten), the reason, the sentence "holon flow needs
   --target-worktree to be an attached, non-bare git worktree because Stages 4 and 5 run host git operations in it", and
   the fix: "create one with 'git worktree add <path> <branch>' from your repository, or pass a bare clone as
   --target-worktree to auto-provision one". Use Australian English throughout.
4. Add `target_worktree_problem` to `__all__` and to the module docstring's list of utilities.
5. Do not change `is_working_tree()`, `is_bare_repository()`, `ensure_worktree()` or `_resolve_base_git_dir()`.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every call site depends on it, but p_success is high)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings); intent constraint 5 (no new dependencies,
  target-native paths); `holon-config/world/constraints.md` §1.
- **Potential failure modes for this step:**
  - The message drifts from what the tests assert.
  - A subdirectory of a worktree is wrongly rejected.
- **Guardrails and early‑abort checks:** `is_working_tree()` already uses `--is-inside-work-tree`, so subdirectories
  pass. Tests assert on stable substrings (`git worktree add`, the reason phrase), not on the whole message.

### Success & Discard Criteria

- **Success:** The function returns `None` for an attached worktree and a distinct actionable message for each of the
  missing, not-a-directory, bare and plain-directory cases; ruff passes.
- **Discard:** It needs anything beyond the two existing helpers and the standard library (for example a new git
  invocation pattern), or cost exceeds `1.5 * 1.5 = 2.25`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 13.01 |

### Step Metrics Rationale

This is a small pure function over proven helpers, so p_success is high and entropy low.
`EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.5 = 14.25 + 0.50 - 0.24 - 1.5 = 13.01`.

---

## Step 2: Validate the resolved target after auto-provisioning in the flow CLI

- **Sub‑intent recommendation:** NO
- **Reasoning:** This step carries most of the behavioural change and the cwd-fallback hazard. It stays contained in one
  module, though, and its result is fully observable through hermetic CLI tests.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `holon flow` exit non-zero with the actionable message, before the dry-run or preflight gate and
  before any stage, when the final target is not an attached worktree. Keep auto-provisioning for bare and absent
  targets unchanged.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Extract the existing target-branch selection in `_ensure_flow_worktree()` into a small
   `_flow_target_branch(intent_data, context) -> str` helper (same logic, default `origin/main`). This keeps the
   cyclomatic complexity of `_ensure_flow_worktree()` within `C901 <= 10` once the new branches are added.
2. Change `_ensure_flow_worktree()` to return `bool` (True when the target is usable) and update its docstring. Logic
   for an explicit or default CLI target:
   - already an attached, non-bare worktree: return True (unchanged fast path);
   - the path exists, is a directory, and is neither bare nor inside a work tree (a plain non-git directory): skip
     provisioning, because `_resolve_base_git_dir()` would otherwise borrow the cwd repository, and fall through to the
     failure exit;
   - otherwise (bare or absent, the existing auto-provisioning cases): call `ensure_worktree()` as today. Catch
     `RuntimeError` and `ValueError`, log the redacted cause with `logger.warning`, and fall through to validation of
     the original target. On success, set `args.target_worktree` and `context.target_worktree` as today.
3. After that branch, call `target_worktree_problem()` on the final resolved path. If it returns a message, print
   `Error: <message>` to stderr, call `sys.exit(1)` and return False (the existing mock-safe idiom).
4. In `run()`, replace the bare `_ensure_flow_worktree(...)` call with `if not _ensure_flow_worktree(...): return None`.
   Ordering stays: after checkpoint and intent resolution, before `_check_dry_run_and_preflight()`, so no preflight
   probe or stage runs against an invalid target.
5. Keep every function within `C901 <= 10`, `PLR0912 <= 12` and `PLR0913 <= 5`, with no new `# noqa`. If needed, split
   the plain-directory test into a `_is_plain_directory(path)` predicate built on the git_flow helpers.
6. Add no new imports from `entrypoint/` modules. The command only imports the new `target_worktree_problem` from
   `sandbox_executor.git_flow`.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (it delivers acceptance criteria 1, 3 and 4)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §1 (branch isolation: never provision from an unrelated
  repository); `docs/safety.md` §1 "Git is the safety boundary"; the repository complexity limits.
- **Potential failure modes for this step:**
  - The existing `test_flow_cli_auto_creates_worktree_from_bare_repo` regresses.
  - Existing CLI tests that run with the default `"."` target start failing because their cwd is not a worktree (today
    they would already fail inside `ensure_worktree()`, so the risk is low).
  - A patched `sys.exit` lets execution continue.
- **Guardrails and early‑abort checks:** Run `uv run pytest apps/sandbox-executor/tests/test_cli.py -k flow` and
  `uv run pytest apps/sandbox-executor/tests/test_flow.py -k Worktree` right after the change. Stop if any pre-existing
  test needs its assertions changed.

### Success & Discard Criteria

- **Success:** Plain and absent-unprovisionable targets exit 1 with the message and `run_flow_pipeline` is never called.
  A bare target still provisions `flow-<slug>`. An attached worktree passes straight through.
- **Discard:** Keeping bare auto-provisioning green would require changing `ensure_worktree()` semantics, or any
  existing test assertion must be altered.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.0   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 17.90 |

### Step Metrics Rationale

This step changes control flow with a subtle interaction (the cwd fallback) and has to respect the complexity limits, so
it carries the highest entropy of the plan.
`EV = 0.85 * 25.0 + 0.5 * 1.5 - 0.3 * 2.0 - 3.5 = 21.25 + 0.75 - 0.60 - 3.5 = 17.90`.

---

## Step 3: Validate the target_worktree restored from a checkpoint resume

- **Sub‑intent recommendation:** NO
- **Reasoning:** A narrow change to how the effective target is chosen, built on Step 2's function. It is cheap and
  closes the main gap the intent describes.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make a `--checkpoint` (with or without `--from-stage`) resume fail fast when the checkpoint's
  recorded `target_worktree` is deleted, plain or otherwise not a worktree, instead of the default `"."` being checked
  in its place.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `_effective_flow_target(args, context) -> tuple[str, bool]`. It returns `(path, from_checkpoint)` and mirrors
   `_update_flow_context()`'s precedence: an explicit `--target-worktree` other than `"."` wins (with `False`);
   otherwise, when a checkpoint context exists, use `context.target_worktree or "."` (with `True`); otherwise use
   `args.target_worktree or "."` (with `False`).
2. In `_ensure_flow_worktree()`, use that path instead of reading `args.target_worktree` directly.
3. For a checkpoint-restored target, keep auto-provisioning only for the bare case, consistent with "do not change bare
   auto-provisioning". An absent or plain recorded path is _not_ re-provisioned: silently creating a fresh worktree
   would hide that the run's working state was removed. Validate it and exit 1 instead.
4. For the checkpoint source, prefix the message with `Checkpoint '<checkpoint path>' records target_worktree that` and
   add the hint "or pass --target-worktree to resume against a different attached worktree". Pass the prefix and hint
   from the CLI so that `target_worktree_problem()` stays generic.
5. When provisioning does happen for a checkpoint-restored bare target, write the result back to
   `context.target_worktree` (as today) and leave `args.target_worktree` untouched, so `_update_flow_context()` does not
   overwrite the restored value with `"."`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (acceptance criterion 2 only; Step 4's engine guard is a second line of defence)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1; `holon-config/world/constraints.md` §3 (checkpoints are not ledgers: no
  ledger file is read or written here).
- **Potential failure modes for this step:**
  - An old checkpoint whose `target_worktree` is `"."`, combined with a cwd that is a worktree, must still resume (it
    will, because `"."` validates).
  - An explicit `--target-worktree` given alongside `--checkpoint` must still override.
- **Guardrails and early‑abort checks:** Cover both precedence cases in the Step 5 tests. Stop if `FlowContext`
  serialisation would need to change (it must not).

### Success & Discard Criteria

- **Success:** A checkpoint recording a deleted or plain path exits 1 with the checkpoint-prefixed message before
  preflight. A checkpoint recording a valid worktree resumes unchanged.
- **Discard:** The change requires editing `load_checkpoint`/`save_checkpoint` or the checkpoint schema.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 10.89 |

### Step Metrics Rationale

The logic is small, but its precedence rules interact with `_update_flow_context()`, which is the main risk.
`EV = 0.85 * 15.0 + 0.5 * 1.0 - 0.3 * 1.2 - 2.0 = 12.75 + 0.50 - 0.36 - 2.0 = 10.89`.

---

## Step 4: Add an injectable worktree guard to PipelineEngine before the first stage

- **Sub‑intent recommendation:** NO
- **Reasoning:** A defence-in-depth guard that reuses the existing pre-stage failure mechanism. Injecting it keeps the
  49 existing engine tests stable, so the risk stays low.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make any production `run_flow_pipeline()` call refuse to start a stage against an invalid
  `context.target_worktree`, independent of the CLI path.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add an optional keyword parameter `worktree_validator: Callable[[str], str | None] | None = None` to
   `PipelineEngine.__init__`, store it, and document it in the class docstring. The default `None` keeps all direct
   engine tests unchanged. That brings `__init__` to 6 parameters including `self`, so check `PLR0913` (which counts
   `self`): if the limit of 5 is exceeded, make `budgets`, `clock` and `worktree_validator` keyword-only after a `*`
   marker. If ruff still counts them, drop the constructor parameter and set a class attribute `worktree_validator` from
   `run_flow_pipeline()` instead. No `# noqa` either way.
2. Add `_pre_run_guard_error(self) -> str | None`. It returns `SKIP_PUSH_DISPATCH_ERROR` when `skip_push` is set without
   `dry_run` (moving the existing condition), otherwise the validator's message for `self.context.target_worktree` when
   a validator is set, otherwise `None`.
3. In `run()`, replace the inline skip-push block with one call to `_pre_run_guard_error()`. On a message, record it
   through the existing `_stage_failure(context, first_stage, timestamp, message)`, log
   `Pipeline halted due to failure in stage '<stage>'.` and return without `save_checkpoint()`, so no stage runs and no
   checkpoint is written (the skip-push behaviour and message stay identical). This leaves the statement count of
   `run()` the same or lower.
4. In `run_flow_pipeline()`, construct the engine with `worktree_validator=target_worktree_problem`, imported from
   `sandbox_executor.git_flow`. `git_flow` imports only `sandbox_executor.git`, so this creates no cycle and adds no
   imports from entrypoints.
5. Leave the stage handlers (`run_review_stage` and the others) untouched.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Code placement rules (no entrypoint imports into the host flow engine, no new `# noqa`);
  `docs/safety.md` §4 (fail before blast radius).
- **Potential failure modes for this step:**
  - The skip-push guard tests regress if the message or ordering changes.
  - The new parameter is rejected by `PLR0913`.
- **Guardrails and early‑abort checks:** Run
  `uv run pytest apps/sandbox-executor/tests/test_flow.py -k "skip_push or Engine"` right after the refactor, then
  `uv run ruff check` on the module.

### Success & Discard Criteria

- **Success:** An engine with the validator and a plain-directory target returns a FAILED first-stage result containing
  the actionable message, no stage handler is called and no checkpoint file exists. The skip-push tests stay green.
- **Discard:** The guard cannot be added without raising complexity counts that would need suppressions.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.94  |

### Step Metrics Rationale

The guard reuses a proven pattern. Its impact is modest because the CLI already covers the main path; it mostly protects
programmatic callers and child pipelines.
`EV = 0.88 * 10.0 + 0.5 * 1.0 - 0.3 * 1.2 - 2.0 = 8.80 + 0.50 - 0.36 - 2.0 = 6.94`.

---

## Step 5: Add hermetic tests for rejection, bare auto-provisioning and acceptance

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the acceptance evidence and are tightly coupled to Steps 2 to 4. They belong on the same
  branch.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove each acceptance criterion hermetically, with real temporary git repositories and no network,
  remote or Docker access.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Hermetic setup: each test that exercises rejection calls `monkeypatch.chdir(tmp_path / "outside")`, a non-git
   directory, so the cwd fallback in `_resolve_base_git_dir()` can never resolve the developer's real repository. Build
   repositories in `tmp_path` with `git init`, `git checkout -B main`, one commit and `git clone --bare`, mirroring the
   existing `TestFlowWorktreeHermetic.hermetic_repos` fixture. In `test_cli.py`, add a small local fixture (or reuse a
   helper from `tests/hermetic_fixtures.py` if one fits); do not modify the shared fixture modules.
2. `test_cli.py`, driving `cli_main()` with patched `sys.argv`, `sys.exit` and `sandbox_executor.cli.run_flow_pipeline`
   in `--dry-run --json` mode, following the existing flow CLI tests:
   - plain non-git directory as `--target-worktree`: `sys.exit(1)`, pipeline not called, stderr contains
     `git worktree add` and "not inside a git working tree", and no `flow-<slug>` directory is created next to it;
   - deleted path (created then removed): exit 1, pipeline not called, stderr mentions "does not exist";
   - checkpoint resume: save a checkpoint (via `save_checkpoint` with a `FlowContext` whose `target_worktree` is a plain
     directory, and a second one with a deleted path), run `holon flow --checkpoint <file> --dry-run`, and expect exit
     1, pipeline not called and the checkpoint-prefixed message; repeat once with `--from-stage review`;
   - checkpoint with an explicit valid `--target-worktree`: accepted, and the context carries the explicit path;
   - valid attached worktree (`git worktree add` from the hermetic source repository): pipeline called once, and
     `context.target_worktree` equals the given path.
3. `test_flow.py`:
   - unit tests for `target_worktree_problem()` covering a valid worktree (`None`), a worktree subdirectory (`None`), a
     bare repository, a plain directory, a file path and a missing path, each with the expected reason substring;
   - an engine guard test: `PipelineEngine(ctx, worktree_validator=target_worktree_problem)` with a plain-directory
     `target_worktree` and patched stage runners. Assert that the first-stage result is FAILED with the message, that no
     stage runner was called and that no checkpoint was written;
   - a test that `run_flow_pipeline()` wires the validator (patch `PipelineEngine` or assert the failure on a plain
     directory);
   - keep the existing `test_flow_cli_auto_creates_worktree_from_bare_repo` unchanged and add one assertion-only
     companion test proving the bare path still provisions after the new validation (criterion 3).
4. Do not edit or weaken any existing test assertion. All tests must be deterministic and parallel-safe under
   `-n auto --dist worksteal` (unique `tmp_path` roots only).

### Dependencies & Criticality

- **Depends on:** Steps 2, 3 and 4
- **Is Bottleneck:** YES (criterion 5 and the evidence for all the others)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes declared in the plan; tests live in the matching
  `tests/` module); `apps/sandbox-executor/docs/hermetic_testing.md`; the autouse `gh_write_guard` and credential
  isolation fixtures in `conftest.py`.
- **Potential failure modes for this step:**
  - A test leaks into the real repository through the cwd fallback.
  - The checkpoint helper API differs from what is assumed.
  - A mocked `sys.exit` lets code continue into the pipeline.
- **Guardrails and early‑abort checks:** Assert that `run_flow_pipeline` was not called in every rejection test. Assert
  that no `flow-*` sibling directory was created. Run the affected files first with
  `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py`.

### Success & Discard Criteria

- **Success:** All new tests pass and the full suite stays green.
- **Discard:** Any test needs network, Docker or the real repository's state, or cost exceeds `1.5 * 2.5 = 3.75`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.3   |
| impact_pred         | 5.0   |
| cost_pred           | 2.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 1.61  |

### Step Metrics Rationale

Real git fixtures add some setup brittleness. The impact counted here is the incremental value of the tests themselves;
the behaviour's value is counted in Steps 2 to 4.
`EV = 0.85 * 5.0 + 0.5 * 0.5 - 0.3 * 1.3 - 2.5 = 4.25 + 0.25 - 0.39 - 2.5 = 1.61`.

---

## Step 6: Run the lint and test gates once

- **Sub‑intent recommendation:** NO
- **Reasoning:** A mandatory verification gate with no design content.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Confirm the branch meets the repository gates before review.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_flow.py`.
2. Run the full suite once with `uv run task test`. The intent has no timing target, so there are no repeated runs.
3. Run `uv run task lint` (ruff check, ruff format, `uv lock --check`; there must be no `uv.lock` diff because no
   dependency is added) and `uv run task lint-docs` (prettier on the Markdown, including this plan).
4. Report the pass and fail counts and the pytest-reported time from the full run in the execution report.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent constraint 5; Test-Run Discipline; `holon-config/world/constraints.md` §3 (exactly three
  append-only ledgers; this plan writes no ledger lines itself).
- **Potential failure modes for this step:** Formatting drift; an unrelated flaky test.
- **Guardrails and early‑abort checks:** Fix formatting with `ruff format` only within the touched modules. Report any
  unrelated failure rather than editing out-of-scope files.

### Success & Discard Criteria

- **Success:** All four commands exit 0.
- **Discard:** A failure outside the touch set: stop and report it instead of widening scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 0.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | -0.90 |

### Step Metrics Rationale

This is a pure gate: it adds no direct impact, but it is required for the plan to count as successful.
`EV = 0.90 * 0.0 + 0.5 * 0.5 - 0.3 * 0.5 - 1.0 = 0.00 + 0.25 - 0.15 - 1.0 = -0.90`.

---

### Strategy Rationale

The intent is well-founded and has a positive EV, so it is accepted with a light reframe: the shared helper goes in
`git_flow.py`, plain directories are rejected before provisioning, and the engine guard is injectable. Each adjustment
comes from a concrete code fact. The checkpoint path bypasses validation because `_ensure_flow_worktree()` reads
`args.target_worktree`. `_resolve_base_git_dir()` falls back to the cwd repository, so a plain directory would otherwise
be mis-provisioned rather than rejected. And 49 existing tests build `PipelineEngine` around non-git paths.

Aggregation:

- `entropy_pred` is the sum of the step entropies: `0.8 + 2.0 + 1.2 + 1.2 + 1.3 + 0.5 = 7.0`, within the 15.0 budget.
- `cost_pred` is the sum of the step costs: `1.5 + 3.5 + 2.0 + 2.0 + 2.5 + 1.0 = 12.5`.
- `p_success_pred` comes from the bottleneck chain (Steps 1, 2 and 5). A strict product,
  `0.95 * 0.85 * 0.85 * 0.85 * 0.88 * 0.90 = 0.46`, would be far too pessimistic, because the step failures are strongly
  correlated and recoverable within one execution, where a failed test is fixed and re-run. The plan figure, 0.84, is a
  qualitative judgement anchored just below the weakest bottleneck step (Step 2, 0.85).
- `impact_pred` is a whole-intent judgement (70.0). It removes a class of aborts that can come hours into a run, which
  is high operational value for a small change. It is not the sum of the step impacts, which count only incremental
  contributions.
- `learning_value_pred = 4.5`: the calibration signal on a cross-module guard, plus documentation of the cwd-fallback
  hazard (a candidate KB failure-mode entry for Stage 5).
- `EV = 0.84 * 70.0 + 0.5 * 4.5 - 0.3 * 7.0 - 12.5 = 58.80 + 2.25 - 2.10 - 12.5 = 46.45`.

Strong assumptions:

- Existing CLI flow tests run with a cwd that is a git worktree. They already depend on this, because today's
  `ensure_worktree()` would raise otherwise.
- `save_checkpoint` and `load_checkpoint` round-trip `target_worktree` unchanged, as the `from_dict` code at `flow.py`
  line 445 shows.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: branch isolation and rebase discipline. Work happens only on this plan's
    execution branch, and merging to `main` stays with the human maintainer.
  - `holon-config/world/constraints.md` §3: the ledgers are append-only. No ledger is touched, and exactly the three
    ledgers remain.
  - `holon-config/world/ruleset.md` §2 and §3: typing, docstrings, `uv` only, and test changes declared up front.
  - `docs/safety.md` §1, §4 and §5: git as the safety boundary, entropy as a safety signal, human review as the final
    gate.
  - The repository complexity limits (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`) with no new
    `# noqa`, and no new imports from entrypoints into `flow.py`.
- **Potential violations or edge cases:**
  - The cwd fallback in `_resolve_base_git_dir()` could create worktrees or branches in the real repository during tests
    or for plain-directory inputs.
  - Adding `git_flow.py` goes beyond the intent's declared scope.
  - A `PLR0913` breach from the new engine parameter.
  - The skip-push guard regresses during the `run()` refactor.
- **Mitigations built into the plan:**
  - Plain directories are rejected before provisioning.
  - Every rejection test pins the cwd to a non-git `tmp_path` directory and asserts that no `flow-*` sibling exists.
  - The validator is placed in its owning module, with the reason documented.
  - The engine parameter is keyword-only, with a class-attribute fallback.
  - The skip-push message and ordering are preserved and covered by the existing tests.
- **Residual risk accepted (and why):** Narrowing auto-provisioning to "bare or absent" changes behaviour for operators
  who passed a plain directory and relied on the cwd fallback. That behaviour was accidental and unsafe, it borrowed an
  unrelated repository, and the new error tells them exactly how to create a worktree. The risk is accepted.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.0
- **Budget Compliance:** The strategy fits within the budget. The work runs as a single plan on one execution branch,
  with no sub-intent list.
