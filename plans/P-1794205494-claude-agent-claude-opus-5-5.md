# Plan for I-1794205479-flow-runs-in-dedicated-worktree-not-default-branch

- **Plan ID:** P-1794205494-860c0862-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-09T06:24:55.484Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. Small clarifications are folded into the steps: (1) "default branch" for the
  flow is the intent's `target_branch` with any `origin/` prefix removed (the CLI currently defaults it to
  `origin/main`, so `main` and `origin/main` must compare equal); (2) for the direct entry points (`holon review`,
  `holon review-loop`, `holon calibrate`), which have no intent, it is the branch `refs/remotes/origin/HEAD` points at,
  falling back to `main`; (3) `review` and `pr-review-loop` are argparse aliases of `review-loop`, so one guard covers
  all three names.
- **Exploration stance:** balanced (mostly exploit). The redirect, the checkpoint refusal and the CLI guards are
  well-specified extensions of existing helpers (`derive_worktree_layout`, `ensure_worktree`,
  `target_worktree_problem`). The single exploratory step tests whether an engine-level guard can be added inside the
  flow package without growing a module past its line budget.
- **Safety priority level:** elevated
- **Priority Justification:** The change governs which working tree host git operations (checkout, commit, push) run in.
  `holon-config/world/constraints.md` §1 ("Agents are prohibited from checking out or committing directly to `main`")
  and `docs/safety.md` "Git is the safety boundary" / "Human review is the final gate" are directly involved: the bug
  being fixed is a live breach of the first rule. It is not critical because nothing here touches ledgers' history,
  credentials or sandbox containment, and every behaviour is covered by hermetic fixtures.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps is BALANCED/EXPLORATORY: Step 4)
- **Justification:** The core behaviour is deterministic and fully specified, so exploitation dominates. Step 4 is a
  cheap, bounded probe of a defence-in-depth guard in `flow/pipeline.py`, which sits exactly at its 394-line budget; if
  it cannot be line-neutral it is discarded without affecting acceptance.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.72  |
| entropy_pred        | 9.5   |
| impact_pred         | 85.0  |
| cost_pred           | 25.5  |
| learning_value_pred | 4.0   |
| ev_pred             | 34.85 |

Derivation: `EV = 0.72 * 85.0 + 0.5 * 4.0 - 0.3 * 9.5 - 25.5 = 61.2 + 2.0 - 2.85 - 25.5 = 34.85`.

## Plan Description & Strategy

The fix lives entirely at the CLI and git-helper layer, outside the line-budgeted `flow/` package, so the budget gate is
respected by construction:

1. `git_flow.py` gains the domain primitives: read the checked-out branch of a worktree, decide whether it is the
   default-branch worktree, provision (or reuse) the `flow-<slug>` sibling off `origin/<target_branch>` even when the
   given target is already a working tree, and build actionable error messages.
2. The worktree-resolution helpers currently in `cli/commands/flow.py` (482 lines, already over the ~400 guidance) move
   to a focused new module `cli/commands/_flow_worktree.py`, which also hosts the redirect and the checkpoint refusal.
   `flow.py` shrinks and only wires the result into the run summary.
3. `cli/commands/review.py` (review, review-loop, pr-review-loop) and `cli/commands/calibrate.py` call one shared guard
   before doing any work.
4. Hermetic fixture repositories (a bare `origin`, a main worktree on `main`, an `origin/main` that is one commit ahead
   of local `main`) prove acceptance criteria (a)–(f), and existing tests that implicitly run against a `main` checkout
   are pinned so that the suite is environment-independent (CI usually checks out `main`).

Because every stage, the default checkpoint path (`get_default_checkpoint_path(context.target_worktree, slug)`) and the
calibration/ledger commits all derive their location from `context.target_worktree`, redirecting that single value
before the context is built is sufficient for "every stage, the checkpoint and the run summary"; Step 1 verifies this
assumption before code is written.

---

## Step 1: Confirm every write path derives from context.target_worktree and list environment-sensitive tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only investigation, minutes of effort; it de-risks Steps 3, 5 and 7 by confirming that one
  redirected value covers all stages and by finding tests that would start provisioning worktrees on a `main` checkout.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Establish, with file and line evidence, that stages 1–5, the checkpoint and the calibration/ledger
  commits use `context.target_worktree` (or `args.target_worktree`) only, and list tests whose behaviour depends on the
  branch of the process's working directory.
- **Git branch:** current execution branch (no commits from this step)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep `flow/stages/*.py`, `flow/pipeline.py`, `flow/checkpoint.py`, `flow/review/*.py`, `calibration.py` and
  `ledger.py` for `target_worktree`, `repo_dir`, `os.getcwd()` and `cwd="."`; note any stage that falls back to the
  process cwd instead of the context value (such a fallback would bypass the redirect).
- Confirm `get_default_checkpoint_path` joins under `context.target_worktree` and whether `.holon/` is gitignored, so a
  user-supplied `--checkpoint` path inside the main worktree does not dirty `git status` (record as an edge case only).
- Read how `PipelineEngine.worktree_validator` is invoked (arguments, return type) for Step 4.
- List tests in `tests/test_cli.py`, `tests/test_cli_options.py`, `tests/test_preflight.py`,
  `tests/test_review_loop.py`, `tests/test_calibration.py` and `tests/flow/` that invoke `holon flow`, `holon review*`
  or `holon calibrate` through `cli_main()` with the default `--target-worktree .` while cwd is the real repository or a
  fixture repository on `main`. Each one would be redirected or refused after the change.
- Check `tests/flow/test_patch_targets.py` and any test that patches `sandbox_executor.cli.commands.flow.<helper>` (for
  example `ensure_worktree`, `is_working_tree`) so that the move in Step 3 keeps those patch targets valid.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes must be declared in the plan); the
  `Code Placement & Sizing` rules in the planner prompt.
- **Potential failure modes for this step:** missing a cwd fallback in a stage; underestimating the number of tests that
  need pinning.
- **Guardrails and early‑abort checks:** if a stage writes via the process cwd rather than the context, add that stage
  to Step 3's scope (passing the redirected path explicitly) before continuing; if that stage is in `flow/` and cannot
  be changed line-neutrally, stop and re-plan.

### Success & Discard Criteria

- **Success:** a written list (in the execution notes) of write sites and of tests to pin, each with a file:line.
- **Discard:** none; this step cannot fail destructively. If it takes more than 1.5x its cost, proceed with the known
  sites and rely on Step 8's full-suite run to catch stragglers.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 9.85  |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 5.0 - 0.3 * 0.5 - 2.0 = 9.5 + 2.5 - 0.15 - 2.0 = 9.85`. Pure reading, so success is near
certain and entropy is minimal; learning value is high because it fixes the assumptions every later step relies on.

---

## Step 2: Add default-branch detection and sibling provisioning primitives to git_flow.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** One module (268 lines, not in the flow budget file), a handful of small typed functions reusing
  `derive_worktree_layout` and the body of `ensure_worktree`; low risk and fast to test.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide reusable, read-mostly helpers that answer "is this the default-branch worktree?" and "give me
  the flow-<slug> worktree branched off origin/<target_branch>, even though the given target is a working tree", plus
  actionable error text.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `checked_out_branch(worktree)`: runs `git symbolic-ref --short -q HEAD` with `run_git(..., check=False)`; returns
  the branch name or None (detached HEAD, non-repo).
- Add `normalise_base_branch(target_branch)`: strips a leading `origin/` (and `refs/heads/`), defaulting to `main` when
  empty, so `origin/main` and `main` compare equal.
- Add `repository_default_branch(worktree)`: reads `git symbolic-ref --short -q refs/remotes/origin/HEAD`, strips
  `origin/`, falls back to `main`. Used by the direct entry points, which have no intent.
- Add `is_default_branch_worktree(worktree, target_branch)`: True only when `is_working_tree(worktree)` and
  `checked_out_branch(worktree) == normalise_base_branch(target_branch)`.
- Refactor the tail of `ensure_worktree` (prune, derive layout, reuse-if-present, `worktree add` new or attach to an
  existing branch) into a private
  `_attach_or_create_flow_worktree(base_git_dir, worktree_path, branch_name, start_point_candidate)`; `ensure_worktree`
  keeps its public signature and behaviour exactly.
- Add `ensure_flow_worktree_off_default(default_worktree, slug, target_branch)`: derives the sibling with
  `derive_worktree_layout(default_worktree, slug)`; prunes; reuses an existing attached `flow-<slug>` worktree (logging
  "reused"); otherwise creates it with start point `origin/<base>` first, via `_resolve_start_point`'s candidate order
  (prefix the list with `origin/<base>`, keeping the existing fallbacks) and `--no-track`. It refuses (raises
  `RuntimeError` with an actionable message) if the derived path exists but is not a working tree, or if the reused
  worktree itself has the default branch checked out, so the redirect can never resolve back to a default-branch tree.
- Add `default_branch_worktree_problem(worktree, base_branch, flow_worktree=None, command="holon flow")`: returns None
  when the worktree is not on the default branch, otherwise an Australian-English message naming the absolute path, the
  branch, why it is refused (the main worktree must stay identical to `origin/<base>`), and the fix: use the named
  `flow-<slug>` worktree when known, or `git worktree add <path> -b <branch> origin/<base>` and pass that path.
- Update the module docstring list and `__all__`; full docstrings and type hints on every public function (`ruleset.md`
  §2). Keep every function within `C901 <= 10`, `PLR0913 <= 5`; target total size at or under ~360 lines.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (Steps 3 and 5 call these helpers)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (never commit to `main`); safety.md "Git is the safety boundary"; "keep all
  paths target-native" from the intent.
- **Potential failure modes for this step:** `origin/<base>` missing in a local-only repository (no remote); a stale
  `flow-<slug>` branch already checked out in a deleted worktree; behaviour drift in `ensure_worktree`.
- **Guardrails and early‑abort checks:** start-point fallback keeps the existing candidates so a repository without
  `origin` still works (logged at warning level, since the intent asks for `origin/<base>`); `prune_worktrees` runs
  before reuse checks to self-heal stale registrations; existing `ensure_worktree` tests in
  `tests/test_flow_target_worktree.py` and `tests/test_git.py` must stay green unchanged.

### Success & Discard Criteria

- **Success:** new helpers have unit tests (Step 7) that pass; `ruff check` clean with no `# noqa`; all pre-existing
  git_flow tests pass unchanged.
- **Discard:** if refactoring `ensure_worktree` changes any pre-existing test outcome, revert the refactor and implement
  `ensure_flow_worktree_off_default` as a separate function that duplicates no more than the `worktree add` call.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 19.05 |

### Step Metrics Rationale

`EV = 0.88 * 25.0 + 0.5 * 3.0 - 0.3 * 1.5 - 4.0 = 22.0 + 1.5 - 0.45 - 4.0 = 19.05`. Contained, well-understood git
plumbing; the main uncertainty is the start-point fallback for repositories without an `origin` remote.

---

## Step 3: Move flow worktree resolution into `cli/commands/_flow_worktree.py` and add the redirect and checkpoint refusal

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core behavioural change and the highest-risk step, but it is tightly coupled to Steps 2 and
  7 and small enough (one new module plus a shrinking `flow.py`) to run in the same execution; splitting it would add
  branch overhead without reducing risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** When the resolved CLI target is an attached working tree on the intent's base branch, run the whole
  flow in the `flow-<slug>` sibling; refuse checkpoint resumes whose recorded target is a default-branch worktree;
  report the effective worktree in the run summary.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `apps/sandbox-executor/src/sandbox_executor/cli/commands/_flow_worktree.py` (leading underscore, like
  `_role.py`, so the command auto-registration in `cli/commands/__init__.py` does not treat it as a subcommand; verify
  that rule in Step 1). Move `_CHECKPOINT_TARGET_HINT`, `_extract_flow_slug`, `_flow_target_branch`,
  `_effective_flow_target`, `_nearest_existing_parent`, `_should_provision`, `_provision_flow_worktree` and
  `_ensure_flow_worktree` there unchanged, exposing `ensure_flow_worktree(args, context, intent_data) -> bool` as its
  entry point. `flow.py` imports it; keep thin aliases in `flow.py` only if Step 1 found tests patching the old names,
  otherwise update those patch targets (declared test change).
- New `_redirect_default_branch_target(args, context, intent_data, target_worktree) -> str | None`, called inside
  `ensure_flow_worktree` after `_effective_flow_target` and before the existing `is_working_tree` early return:
  - Only for a CLI target (`from_checkpoint` False) that `is_default_branch_worktree(target, _flow_target_branch(...))`.
  - Calls `ensure_flow_worktree_off_default(target, slug, base)`; on success sets `args.target_worktree` and, when
    resuming with an explicit target, `context.target_worktree` to the flow worktree, and logs at info level: "Target
    worktree '<main>' has the base branch '<base>' checked out; it selects the repository and base branch only. Running
    the flow in '<flow-path>' (branch flow-<slug>, from origin/<base>)."
  - On failure prints an actionable `Error:` (redacted with `cli.redact_cli_error`) and exits 1 before any stage runs;
    it never falls back to the main worktree.
- New `_refuse_default_branch_checkpoint(args, context, intent_data, target_worktree) -> bool`: when the target came
  from the checkpoint (`from_checkpoint` True) and `is_default_branch_worktree` holds, print
  `default_branch_worktree_problem(...)` with subject "Checkpoint '<path>' target_worktree" naming the derived
  `flow-<slug>` path (via `derive_worktree_layout`, read-only, nothing created) and suggesting
  `--target-worktree <flow-path>`; exit 1. It runs before preflight, before `_build_flow_context` and before
  `run_flow_pipeline`, so no stage runs. An explicit `--target-worktree` that is itself the main worktree is redirected
  as in the CLI case (explicit overrides the checkpoint, matching `_effective_flow_target`).
- Unchanged behaviour: a working tree on any other branch is returned as is; bare and absent targets keep the existing
  auto-provisioning path; non-git paths keep the existing actionable errors.
- New checkpoints automatically record the flow worktree because `_build_flow_context` reads `args.target_worktree` and
  `_update_flow_context` copies an explicit target; additionally make `_build_flow_context` prefer the redirected path
  when resuming (set `context.target_worktree` in the redirect itself, so the `!= "."` condition in
  `_update_flow_context` is never relied upon for a redirect from the default `.`).
- Run summary: in `flow.py` add the effective `target_worktree` to the printed summary header and to the JSON
  `result_dict` (`result_dict.setdefault("target_worktree", context.target_worktree)`), so callers can see where the run
  happened.
- Update the `--target-worktree` help for `holon flow` via the existing `help=` parameter of
  `add_target_worktree_option`: "Repository and base branch to run against (default: current directory). If it has the
  intent's target branch checked out (normally main), the run happens in a dedicated flow-<slug> sibling worktree
  branched off origin/<target_branch>; the given worktree is never modified."
- Size targets: `flow.py` drops from 482 to roughly 330 lines; `_flow_worktree.py` about 230 lines; every function
  within the ruff complexity limits, no `# noqa`. No imports from `entrypoint/` into `flow/`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (acceptance criteria 1–3 depend on it)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1; safety.md "Human review is the final gate" (merging stays with the human
  maintainer, untouched here); intent constraint "existing auto-provisioning of bare or absent targets is unchanged".
- **Potential failure modes for this step:** redirect applied after the context was already built (stages would still
  see the main worktree); a test patch target broken by the move; `.` resolving to the real repository during tests;
  dry-run runs now provision a worktree (consistent with existing bare-target provisioning, but a new side effect for
  `.`).
- **Guardrails and early‑abort checks:** the redirect lives in the single function that already runs before preflight
  and context construction; Step 7 asserts `pipeline.call_args[1]["context"].target_worktree` equals the flow path; Step
  7 asserts the main worktree's HEAD, branch and porcelain status are identical before and after.

### Success & Discard Criteria

- **Success:** tests (a)–(e) of Step 7 pass; all pre-existing tests in `tests/test_flow_target_worktree.py` pass
  unchanged; `flow.py` under 400 lines.
- **Discard:** if more than 5 pre-existing tests need behavioural (not just fixture-pinning) changes, stop and re-plan,
  because that signals the redirect alters a contract other callers rely on.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.5   |
| impact_pred         | 30.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 19.25 |

### Step Metrics Rationale

`EV = 0.80 * 30.0 + 0.5 * 4.0 - 0.3 * 2.5 - 6.0 = 24.0 + 2.0 - 0.75 - 6.0 = 19.25`. Highest impact and entropy of the
plan: it moves ~150 lines between modules and changes the default behaviour for every `holon flow` run started from a
`main` checkout. Probability is reduced for patch-target breakage and interaction with checkpoint resume ordering.

---

## Step 4: Probe a line-neutral engine-level guard against default-branch targets in flow/pipeline.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** Optional defence in depth with a strict budget constraint; cheap to try, cheap to discard.
- **Step Type:** EXPLORATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The engine's existing `worktree_validator` hook (`flow/pipeline.py`, assigned
  `target_worktree_problem` in `run_flow_pipeline`) can be pointed at a composed validator in `git_flow.py` that also
  refuses default-branch worktrees, by changing only the import and assignment lines, so `pipeline.py` stays at or below
  394 lines.
- **Learning target:** Whether the flow package can enforce "never mutate the default-branch worktree" for callers that
  bypass the CLI (for example programmatic `run_flow_pipeline` use) without any budget increase, and whether the
  validator has access to the intent's base branch.
- **Maximum acceptable cost for this learning:** 2.0 cost units (about 20 minutes); zero growth of any flow module.

### Intent & Git Integration

- **Step Intent:** Make Requirement 2 (NEVER MUTATE) hold even when `run_flow_pipeline` is called directly.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- If the validator receives only a path: add `flow_target_worktree_problem(path)` in `git_flow.py` that returns
  `target_worktree_problem(path)` or, failing that check,
  `default_branch_worktree_problem(path, repository_default_branch(path))`. Replace the imported name and the assignment
  in `pipeline.py` (two lines, net zero).
- If the validator signature cannot carry this without extra lines in `pipeline.py`, discard the step and document in
  the execution notes that enforcement is at the CLI boundary (which the intent's rationale names as sufficient).
- Run `python scripts/check_flow_module_budgets.py` immediately after the edit.
- Verify the existing test `test_run_flow_pipeline_wires_the_worktree_validator` still asserts the wiring (update its
  expected function name if it compares by identity; declared test change).

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint "respect the per-module line budgets of the flow package and do not raise any
  budget"; `apps/sandbox-executor/flow_module_budgets.json`.
- **Potential failure modes for this step:** a budget overrun; the engine guard refusing legitimate programmatic test
  contexts that point at a fixture repository on `main`.
- **Guardrails and early‑abort checks:** budget script after the edit; full `tests/flow/` run; revert on any budget or
  unexpected test failure.

### Success & Discard Criteria

- **Success:** `pipeline.py` line count unchanged or lower, budget script passes, a unit test shows the engine fails the
  first stage for a default-branch target before any stage or checkpoint.
- **Discard:** any net growth in `pipeline.py`, or more than 3 `tests/flow/` tests broken by the stricter validator.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 1.0   |
| impact_pred         | 8.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 5.80  |

### Step Metrics Rationale

`EV = 0.70 * 8.0 + 0.5 * 5.0 - 0.3 * 1.0 - 2.0 = 5.6 + 2.5 - 0.3 - 2.0 = 5.80`. Modest impact because the CLI layer
already satisfies the acceptance criteria; the learning value (can the budgeted package enforce invariants without
growth?) justifies the small cost. Failure is harmless: the step is reverted.

---

## Step 5: Refuse default-branch worktrees in holon review, review-loop and calibrate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two small command modules and one shared guard; low risk, but it changes the behaviour of commands that
  tests may run against a `main` checkout, so it is kept in the same execution as the test pinning in Step 7.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The direct entry points that write to the working tree of the branch they run on must exit with an
  actionable error, before any work, when their target worktree has the default branch checked out.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- In `_flow_worktree.py` (or a 30-line `cli/commands/_worktree_guard.py` if Step 3's module is near 250 lines), add
  `refuse_default_branch_worktree(target_worktree, command) -> bool`: resolve `os.path.abspath(target_worktree or ".")`;
  if it is not a working tree return True (existing error handling applies); compute `repository_default_branch(path)`;
  if `is_default_branch_worktree` holds, print `Error: <default_branch_worktree_problem(..., command=command)>` to
  stderr and return False.
- The message for these commands says: "'<path>' has the default branch '<base>' checked out. holon <command> commits
  review/calibration records to the branch it runs on, and the main worktree must stay identical to origin/<base>. Run
  it from the flow-<slug> worktree of the run (or `git worktree add ../<name> <execution-branch>`) and pass
  --target-worktree <that path>."
- `cli/commands/review.py` `run`: call the guard right after `_validate_review_args`, before runner resolution and
  before either local or container dispatch; on refusal `sys.exit(1)`. Because `review` and `pr-review-loop` are aliases
  of `review-loop`, one call covers all three names; Step 7 asserts each alias.
- `cli/commands/calibrate.py` `run`: call the guard before `cli.run_calibrate`; on refusal return 1. Apply it with
  `--skip-commit` too, because calibration still writes `plans/P-*_calibration.md` into the working tree.
- The containerised review entrypoint (`python -m sandbox_executor.entrypoint.review_loop`, used by
  `role_dispatcher.sh`) and the flow's internal Stage 4/5 calls do not pass through these command modules, so they are
  unaffected; the flow is protected by Step 3 instead.
- Add the same guard sentence to the `--target-worktree` help of both commands through `add_target_worktree_option`'s
  `help=` argument.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (acceptance criterion 4 only)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1; intent goal 4.
- **Potential failure modes for this step:** existing CLI tests that invoke `holon calibrate` or `holon review-loop`
  through `cli_main()` with the default `.` start failing on CI where the checkout is `main` but pass locally on a
  feature branch (environment-dependent flakiness); detached HEAD in CI (guard correctly does not fire).
- **Guardrails and early‑abort checks:** Step 1's list of such tests is pinned in Step 7 to a hermetic fixture on a
  non-default branch, or to a patched guard where the test is about something else; Step 8 runs the full suite once from
  a `main`-checkout fixture simulation (see Step 7) to prove environment independence.

### Success & Discard Criteria

- **Success:** test (f) passes for `review`, `review-loop`, `pr-review-loop` and `calibrate`; no pre-existing review or
  calibrate test changes outcome.
- **Discard:** if the guard requires changes to `flow/review/cli.py` (budgeted at 106 lines) to be effective, keep it at
  the command layer only and record the gap.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 10.80 |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 3.0 - 0.3 * 1.5 - 3.0 = 12.75 + 1.5 - 0.45 - 3.0 = 10.80`. Small code, but its entropy
reflects the environment-sensitivity of existing CLI tests.

---

## Step 6: Document that naming main selects the repository and base branch only

- **Sub‑intent recommendation:** NO
- **Reasoning:** Documentation only; trivially reversible.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the redirect, the checkpoint refusal and the direct-command refusals discoverable.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- `docs/git_flow.md` §2.7: add a short subsection "Target worktree and the default branch" after the stage table: naming
  the main worktree (or running from it with the default `.`) selects the repository and base branch only; the run
  happens in the sibling `flow-<slug>` worktree branched off `origin/<target_branch>` (reused when it already exists);
  the main worktree's HEAD, branch and status are never changed; checkpoints record the flow worktree; a checkpoint
  recording a default-branch worktree is refused with the flow worktree to use; `holon review`, `holon review-loop` and
  `holon calibrate` refuse a default-branch worktree.
- `docs/faq.md`: one Q&A ("Why did holon flow create a flow-<slug> directory next to my checkout?").
- `README.md` / `docs/examples.md`: amend any `holon flow --target-worktree` example that implies the run happens in the
  named checkout (grep first; only edit where wording is wrong).
- Australian English throughout ("initialise", "behaviour", "normalise").
- Run `uv run task lint-docs` (prettier) and fix formatting.

### Dependencies & Criticality

- **Depends on:** Steps 3 and 5 (wording must match the implemented messages)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent goal 5 and constraint 7 (Australian English, lint-docs passes).
- **Potential failure modes for this step:** prettier reflow failures; describing behaviour that Step 4 discarded.
- **Guardrails and early‑abort checks:** describe only CLI-level enforcement unless Step 4 succeeded.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes; help texts and docs use the same wording.
- **Discard:** N/A (documentation is always completed; a formatting issue is fixed, not discarded).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.5   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.37  |

### Step Metrics Rationale

`EV = 0.92 * 6.0 + 0.5 * 1.0 - 0.3 * 0.5 - 1.5 = 5.52 + 0.5 - 0.15 - 1.5 = 4.37`. Low cost, low risk, modest impact.

---

## Step 7: Add hermetic tests (a)–(f) and pin environment-sensitive existing tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are the acceptance evidence and must be written alongside Steps 3 and 5; they use local fixture
  repositories only (no network), so they are cheap and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove every acceptance criterion with hermetic fixtures and make the existing suite independent of
  which branch the developer's or CI's checkout is on.
- **Git branch:** current execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- New file `apps/sandbox-executor/tests/test_flow_default_branch_worktree.py` (declared new test file). Fixture
  `main_repo(tmp_path, monkeypatch)`: chdir to a non-git `outside` directory (as `tests/test_flow_target_worktree.py`
  does); create a seed repository, a bare `origin.git`, clone it into `checkout/` on `main` with local user config; push
  one extra commit to `origin` from a second clone and `git fetch` in `checkout/`, so `origin/main` is one commit ahead
  of local `main` (this distinguishes "branched off origin/main" from "branched off main"); write an intent file with
  `slug` and `target_branch: main`. Snapshot helper returns
  `(rev-parse HEAD, symbolic-ref --short HEAD, status --porcelain)` for a path.
- (a) Redirect: run `holon flow <intent> --target-worktree <checkout> --dry-run --json` with the agent/model flags and
  `run_flow_pipeline` patched (reuse the `_run_flow` pattern); assert the context's `target_worktree` is
  `<tmp>/flow-<slug>`, its branch is `flow-<slug>`, `git merge-base --is-ancestor origin/main flow-<slug>` holds and its
  HEAD equals `origin/main`; assert `get_default_checkpoint_path(context.target_worktree, slug)` lies inside it; assert
  the info log (caplog) names both paths. Also run once with cwd set to `checkout/` and the default `.` to cover the
  implicit case. To cover "all stages use it", run a second variant through the real `run_flow_pipeline` with each stage
  function patched to record `context.target_worktree` (patch targets as used in `tests/flow/helpers.py`), and assert
  every recorded value is the flow path.
- (b) Main untouched: snapshot `checkout/` before and after both (a) variants and assert equality, plus
  `git worktree list --porcelain` shows `checkout/` still on `refs/heads/main`.
- (c) Reuse: pre-create `flow-<slug>` with `git worktree add`, add a marker commit on it, run the flow; assert the same
  path is used, the marker commit is still HEAD, and no `flow-<slug>-*` sibling appears.
- (d) Non-default branch: `checkout/` linked worktree on `feature-x`; assert it is used as is and no `flow-*` sibling is
  created (mirrors `test_flow_accepts_attached_worktree`).
- (e) Checkpoint refusal: `save_checkpoint` a `FlowContext` whose `target_worktree` is `checkout/`; run
  `holon flow --checkpoint <file>` with and without `--from-stage review`; assert exit 1, the pipeline mock not called,
  stderr contains the absolute `flow-<slug>` path and `--target-worktree`, no `flow-*` sibling was created (read-only
  derivation), and `checkout/` snapshot unchanged. Add the positive counterpart: the same checkpoint with
  `--target-worktree <flow path>` proceeds.
- (f) Direct entry points: parametrise over `review-loop`, `review`, `pr-review-loop` (with `--local --dry-run` and an
  execution branch argument) and `calibrate`; with `--target-worktree <checkout>` assert exit 1, an `Error:` naming the
  path and `main`, and that `run_review_loop_cli`, `run_docker_container` and `cli.run_calibrate` mocks were not called;
  a counterpart on `feature-x` reaches the mocked callee.
- Unit tests for Step 2 helpers in the same file: `normalise_base_branch` (`origin/main`, `main`, empty),
  `checked_out_branch` on detached HEAD returns None (guard does not fire), `repository_default_branch` with and without
  `origin/HEAD`, and `ensure_flow_worktree_off_default` refusing a derived path that exists but is not a worktree.
- If Step 4 succeeded: an engine test that `run_flow_pipeline` with a `main` fixture target fails the first stage before
  any stage or checkpoint (mirrors `test_engine_guard_fails_first_stage_before_any_stage_or_checkpoint`).
- Pin existing tests found in Step 1 (declared changes to existing test files: `tests/test_cli.py`,
  `tests/test_cli_options.py`, `tests/test_preflight.py`, `tests/test_review_loop.py`, `tests/test_calibration.py` as
  applicable): pass an explicit `--target-worktree` pointing at a fixture worktree on a non-default branch, or patch the
  guard where the test is about argument parsing. Assertions are not weakened.
- Keep the new test file under ~400 lines; if it grows past that, split (f) into
  `tests/test_default_branch_command_guard.py`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 5 (and 4 for its optional test)
- **Is Bottleneck:** YES (acceptance requires the tests)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (test changes declared here); `apps/sandbox-executor/docs/hermetic_testing.md`;
  `tests/test_sandbox_hermetic_guard.py` (no network, no real repository access); intent constraint "tests strictly via
  'uv run pytest'".
- **Potential failure modes for this step:** a test whose cwd falls back to the real repository and provisions a
  `flow-*` directory next to the workspace; xdist workers sharing fixture paths; git user config missing on CI.
- **Guardrails and early‑abort checks:** every fixture lives under `tmp_path` and chdirs to a non-git directory; set
  `user.name`/`user.email` per repository; after the run check that no `flow-*` directory appeared next to the workspace
  root.

### Success & Discard Criteria

- **Success:** all new tests pass with
  `uv run pytest apps/sandbox-executor/tests/test_flow_default_branch_worktree.py apps/sandbox-executor/tests/test_flow_target_worktree.py -n auto --dist worksteal`;
  the pinned tests pass whether the enclosing checkout is on `main` or not.
- **Discard:** a test that can only pass by touching the developer's real repository is rewritten, never skipped.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.5   |
| impact_pred         | 20.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 12.55 |

### Step Metrics Rationale

`EV = 0.80 * 20.0 + 0.5 * 4.0 - 0.3 * 1.5 - 5.0 = 16.0 + 2.0 - 0.45 - 5.0 = 12.55`. Many fixture permutations and the
"all stages" variant through the real pipeline are the main effort; the pinning work is the main uncertainty.

---

## Step 8: Run affected tests, the full suite once, lint, lint-docs and the budget check

- **Sub‑intent recommendation:** NO
- **Reasoning:** Verification gate; required by the acceptance criteria.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Demonstrate acceptance criterion 5 and the budget constraint.
- **Git branch:** current execution branch (commit the change set after the gate passes; merging stays with the human
  maintainer)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Affected tests first:
  `uv run pytest apps/sandbox-executor/tests/test_flow_default_branch_worktree.py apps/sandbox-executor/tests/test_flow_target_worktree.py apps/sandbox-executor/tests/test_cli.py apps/sandbox-executor/tests/test_cli_options.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_git.py apps/sandbox-executor/tests/flow -n auto --dist worksteal`.
- Then the full suite exactly once: `uv run task test`. No repeated runs: the intent states no timing or performance
  target.
- `uv run task lint` (includes ruff check/format and `scripts/check_flow_module_budgets.py`) and
  `uv run task lint-docs`.
- Confirm `apps/sandbox-executor/flow_module_budgets.json` is unchanged (`git diff --exit-code` on it), no new
  dependencies (`uv.lock` unchanged), still exactly three ledger files under `holon-knowledge/ledger/`.
- Confirm `git -C <workspace> status --porcelain` shows only intended files and no `flow-*` sibling directory was
  created next to the workspace by the test run.
- Record pass/fail counts and the pytest-reported time of the single full run in the execution record.

### Dependencies & Criticality

- **Depends on:** Steps 2–7
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** Test-Run Discipline in the planner prompt; intent constraint 7; `constraints.md` §3 (ledgers
  append-only, untouched by this change).
- **Potential failure modes for this step:** unrelated pre-existing failures; ruff complexity limits triggered by the
  new orchestration function.
- **Guardrails and early‑abort checks:** if a failure is pre-existing (reproduces on the parent branch), record it with
  its output instead of masking it; split any function hitting `C901`/`PLR0912` rather than adding `# noqa`.

### Success & Discard Criteria

- **Success:** all four commands exit 0; budget file and `uv.lock` unchanged.
- **Discard:** if fixing lint or tests would require raising a flow module budget, stop and report rather than raise it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 7.55  |

### Step Metrics Rationale

`EV = 0.82 * 10.0 + 0.5 * 3.0 - 0.3 * 0.5 - 2.0 = 8.2 + 1.5 - 0.15 - 2.0 = 7.55`. The gate itself is mechanical;
probability reflects the chance of an environment-sensitive test or a complexity-limit finding needing one more fix
cycle.

---

### Strategy Rationale

The intent is accepted as is: it has very high value (every `holon flow` that names or runs from the main worktree
currently leaves it off `main` with extra commits, breaking the installed `holon` command and the reference checkout), a
precise specification, and existing helpers to build on. No reframing improves EV; decomposition into sub-intents would
add three branch/review cycles for a change set of roughly 400 lines of production code that must land atomically (a
redirect without the checkpoint refusal, or vice versa, leaves a bypass), so the plan runs as one execution.

Placement is chosen around the hard constraint that flow package budgets cannot rise: all new logic lives in
`git_flow.py` (not budgeted, ~360 lines after the change), a new focused module `cli/commands/_flow_worktree.py`, and
the two direct command modules; `flow.py` shrinks from 482 lines. The only touch inside `flow/` is the optional,
line-neutral Step 4.

Aggregation: entropy is the sum of step entropies (`0.5 + 1.5 + 2.5 + 1.0 + 1.5 + 0.5 + 1.5 + 0.5 = 9.5`); cost is the
sum (`2.0 + 4.0 + 6.0 + 2.0 + 3.0 + 1.5 + 5.0 + 2.0 = 25.5`). The strict product of the bottleneck steps (2, 3, 7, 8),
`0.88 * 0.80 * 0.80 * 0.82 = 0.46`, understates success because their failure modes are correlated and all are
recoverable within the same execution by an extra fix cycle; qualitatively adjusted to 0.72, with Step 3 as the
bottleneck. Step 4's failure does not affect overall success. Impact 85 reflects closing a recurring, user-visible
breach of the "never commit to main" rule for every caller. Learning value 4.0 is the mean-weighted value of Steps 1 and
4 (what writes where, and whether budgeted modules can carry invariants line-neutrally). Strong assumption: every stage
writes only under `context.target_worktree`; Step 1 verifies it before any code is written.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 Commit Boundaries (no checkout or commit to `main`): the purpose of the
    change; also governs this execution, which commits only on its execution branch.
  - `holon-config/world/constraints.md` §3 Ledger Immutability: no ledger is edited; the redirect only changes where
    future append-only rows are committed.
  - `holon-config/world/ruleset.md` §2 (typing, docstrings) and §3 (test changes declared: new test file plus pinned
    existing tests listed in Step 7).
  - `docs/safety.md` "Git is the safety boundary", "Human review is the final gate" (merging stays with the human
    maintainer; no change to merge logic), "Invariant 1: Git discipline".
  - Intent constraints: flow module budgets, no new dependencies, Australian English, `uv run pytest` only.
- **Potential violations or edge cases:**
  - Tests that implicitly run against the real repository could create a `flow-*` directory beside the workspace.
  - A user-supplied `--checkpoint` path inside the main worktree writes a checkpoint file there (not a ledger or
    calibration file; `.holon/` ignore status checked in Step 1).
  - A repository without an `origin` remote cannot branch off `origin/<base>`; the existing fallback order is kept and
    the fallback is logged at warning level.
  - Detached HEAD on the main worktree (CI) is not a "default-branch checkout" and is used as is, matching the intent's
    wording.
  - Dry-run flows from `main` now provision a sibling worktree, consistent with existing bare-target provisioning.
- **Mitigations built into the plan:** redirect before preflight, context build and pipeline; read-only derivation in
  the checkpoint refusal; snapshot-equality tests on the main worktree; hermetic fixtures pinned to a non-git cwd;
  budget script and `git diff --exit-code` on the budget file; Step 4 discarded on any growth.
- **Residual risk accepted (and why):** programmatic callers of `run_flow_pipeline` remain unguarded if Step 4 is
  discarded; acceptable because the intent's rationale scopes enforcement to the CLI, and every human and agent entry
  point goes through it.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 9.5
- **Budget Compliance:** The strategy fits within budget (9.5 of 15.0), so it runs as a single plan with no sub-intent
  list.
