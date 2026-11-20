# Plan for I-1794529165-calsync

- **Plan ID:** P-1794529181-eabd8e76-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T00:19:42.294Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None.
- **Exploration stance:** conservative. The failure is well understood from the code: Stage 5 checks out the local
  execution ref and `calibration.py` branches from it. The fix is a small pre-sync plus a restore, so no exploratory
  steps are needed beyond one read-only check of how `run_calibrate` picks its start point.
- **Safety priority level:** standard
- **Priority Justification:** The change touches host git operations only (fetch, ref comparison, checkout). It never
  force-pushes, never merges to `main` and never discards local commits: a divergent local ref is refused, not
  overwritten. No `world/` or `docs/safety.md` rule is stricter than the standard level.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** The intent names the defect precisely and the touch points are known
  (`flow/stages/calibrate.py:157-173`, `calibration.py:1597-1601`, `flow/stages/calibrate.py:287-293`). Learning value
  comes from the hermetic tests, not from exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.5   |
| impact_pred         | 72.0  |
| cost_pred           | 20.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 41.5  |

### Strategy Rationale

The intent is accepted as written. Four defects share one root cause (Stage 5 trusts the local ref), so they are fixed
together in one new focused module, `flow/calibrate_sync.py`, which `stages/calibrate.py` calls before any read or
branch. That module owns three small functions: sync the execution ref to origin, restore the starting branch, and
describe a divergence. `stages/calibrate.py` is at its line budget (323), so it only gains call sites and the budget
file is raised by the minimum; the logic lives in the new module, which gets its own budget entry.

Derivation: the overall values come from the steps. The bottleneck is Step 2, the Stage 5 wiring plus restore, because
it changes the control flow of a long function that has noqa-suppressed complexity.

- `p_success_pred = 0.95 * 0.85 * 0.92 * 0.95 = 0.71` if the steps are treated as independent in series. Steps share one
  author and one fixture set, so they correlate, and the estimate is raised to 0.82 by judgement.
- `entropy_pred` is the sum of the step entropies: `1.0 + 2.5 + 1.5 + 1.0 + 0.5 = 6.5`, within the 15.0 budget.
- `cost_pred` is the sum of step costs scaled by 0.8 for shared context: `(4 + 8 + 9 + 3 + 3) * 0.8 = 21.6`, rounded
  down to 20.0 because the new module is small.
- `impact_pred = 72.0` is a qualitative judgement. It makes every Stage 5 report analyse the approved head and makes PR
  inclusion mechanical.
- `ev_pred = 0.82 * 72.0 + 0.5 * 5.0 - 0.3 * 0.0 - 20.0 = 59.04 + 2.5 - 20.0 = 41.54`, recorded as 41.5.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/ruleset.md` section 3: tests live in `tests/`, run via `uv run pytest`; tests are added by
    declared steps only (Step 4 declares them).
  - Python 3.13, ruff complexity limits, no new dependencies, `uv` only.
  - `docs/safety.md`: git is the safety boundary; humans merge to `main`; this plan never merges to `main`.
  - `apps/sandbox-executor/flow_module_budgets.json`: line budgets checked by `uv run task lint`.
  - Exactly three append-only ledgers; no new ledger file is created.
- **Potential violations or edge cases:**
  - The local execution ref has unpushed commits (divergence): must fail, not overwrite.
  - The execution branch does not exist on origin (a skipped push, or `--skip-push` runs): must not fail; fall back to
    the local ref.
  - The worktree is dirty with ledger changes written by earlier stages: a hard sync is unsafe.
  - The worktree is on a detached HEAD, or on the starting branch, when the flow begins.
  - Dry-run mode must not touch refs.
  - Resume that enters at Stage 5 with the calibrated branch already existing locally or on origin.
- **Mitigations built into the plan:**
  - Use `merge-base --is-ancestor` to decide: local is ancestor of origin means fast-forward; origin is ancestor of
    local (or the two differ) means refuse with an actionable error naming both SHAs and the recovery command
    (`git push origin <branch>` or reset deliberately).
  - Dirty worktree: do not hard-sync; branch from the origin SHA explicitly instead, and fast-forward the local ref only
    when the checkout is clean.
  - Record the starting branch in `FlowContext` before Stage 5 changes anything, and restore it in a `finally` path.
  - Hermetic tests use local bare repos as `origin`; no network, per `docs/testing/testing_guide.md`.
- **Residual risk accepted (and why):** Restoring the starting branch can fail when a calibration write is left
  uncommitted (a format failure). That is logged as a warning and the flow result is unchanged, because failing the flow
  after a successful report push would hide the real outcome.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.5
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add `flow/calibrate_sync.py` and call it from `run_calibrate_stage` so that Stage 5 fetches origin, compares the local
execution ref with `origin/<execution_branch>`, fast-forwards the local ref when it is behind, refuses loudly when it
holds commits origin lacks, and passes the origin SHA to `run_calibrate` as the start point of the calibrated branch (so
`calibration.py` needs only a pass-through, not new logic). The final fast-forward of the execution branch is kept but
verified: because the calibrated branch descends from the origin head, `merge --ff-only` is guaranteed to succeed. Its
result is checked and logged rather than ignored. The starting branch is recorded when the flow begins and restored at
the end of Stage 5. Tests build a bare origin plus a clone with the origin ahead. Docs gain a short section.

No sub-intents: the whole plan fits the entropy budget and the steps share one fixture set.

---

## Step 1: Confirm how the calibrated branch start point is chosen and where the flow records its starting branch

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap, and removes the main design uncertainty (whether `run_calibrate` can take an explicit
  start SHA).
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Fix the exact seams before editing.
- **Git branch:** I-1794529165-calsync/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Read `calibration.py` around lines 1540-1610: the start point is `report.evaluated_commit_sha`, else the resolved
  execution ref. Note that `evaluated_commit_sha` is computed earlier, probably from the local ref; trace it.
- Find where `FlowContext` is constructed in `flow/context.py` and `pipeline.py`, and choose the field for the starting
  branch (for example `starting_branch: str | None`).
- Find existing tests that fake `origin` (`test_flow_target_worktree.py`, `worktree_fixtures.py`,
  `hermetic_fixtures.py`, `test_calibrate_final_head_ci.py`) to reuse their fixtures.
- Check `host_remote.fetch_base` and `git_refs._fast_forward_local_branch` for reuse. The latter already refuses to move
  a diverged local branch, so Step 2 calls it rather than duplicating it.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3.
- **Potential failure modes for this step:** `evaluated_commit_sha` is read from the local ref in several places.
- **Guardrails and early‑abort checks:** If the SHA is derived deep in the report builder, route the origin SHA through
  the `execution_branch` ref resolution instead of editing the report builder.

### Success & Discard Criteria

- **Success:** A written list of seams: field name, call sites, and fixtures to reuse.
- **Discard:** Not applicable (read-only).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 7.5   |

### Step Metrics Rationale

`ev_pred = 0.95 * 10.0 + 0.5 * 4.0 - 4.0 = 9.5 + 2.0 - 4.0 = 7.5`. Low cost, and it lowers the risk of Step 2.

---

## Step 2: Add the calibrate_sync module and wire it into Stage 5

- **Sub‑intent recommendation:** NO
- **Reasoning:** One module plus three call sites, with medium risk. The tests in Step 4 cover it, so a separate branch
  adds overhead without safety.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Base the calibrated branch on the origin head, fail on divergence, and guarantee the fast-forward.
- **Git branch:** I-1794529165-calsync/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/calibrate_sync.py` with a module docstring in Australian English. Functions:
  - `sync_execution_ref(context) -> str | None`: skip in dry-run; fetch via `host_remote.fetch_base`-style helper (the
    execution branch refspec, with `check=False`); if `origin/<branch>` is absent, log and return the local SHA
    unchanged (offline and `--skip-push` runs keep working).
  - Compare the SHAs with `merge-base --is-ancestor`: equal means nothing to do; local is an ancestor means fast-forward
    the local ref through `git_refs._fast_forward_local_branch` (which advances the checkout when it is on that branch);
    otherwise raise `CalibrationSyncError` with the branch, both SHAs, the count of local-only commits
    (`rev-list --count origin/<b>..<b>`) and the recovery: push or reset the branch deliberately, then resume.
  - `record_starting_branch(context)`: store `git symbolic-ref --short -q HEAD` (or the detached SHA) in a new
    `FlowContext` field, set once and only if unset, so a resume does not overwrite it with a flow branch.
  - `restore_starting_branch(context)`: checkout the recorded branch, skip when already on it, and warn on failure
    without raising. It also logs when uncommitted ledger changes remain.
- In `run_calibrate_stage`: call `record_starting_branch` (non-dry-run) before `_checkout_execution_branch`; call
  `sync_execution_ref` immediately after the checkout and before the stage-conflict check, turning
  `CalibrationSyncError` into `_stage_failure` with the actionable message. Then pass the origin SHA as the start point
  (via an optional `start_point` parameter of `run_calibrate`, used only in the branch-creation `else` arm, which keeps
  the existing remote-calibrated-branch resume arms intact).
- At the end of the stage, replace the unchecked `checkout` and `merge --ff-only` with: ff-merge and log on failure,
  then push, then `restore_starting_branch`. Put the restore in a `finally` around the post-calibration section so that
  failures restore as well, and extract the push-and-fast-forward block into `calibrate_sync.publish_calibration` to
  keep `calibrate.py` within its 323-line budget and to avoid raising complexity.
- Update `flow_module_budgets.json`: add `calibrate_sync.py` at its creation size, and raise `stages/calibrate.py` only
  by the minimum needed (aim for net zero by moving the push block out). Export the new names from `flow/__init__.py` if
  the package export test requires it, with a matching budget bump.
- Keep the `# noqa` on `run_calibrate_stage` as is, with no new suppressions.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** safety.md (git is the safety boundary; humans merge to `main`); ruleset.md section 2.
- **Potential failure modes for this step:**
  - A dirty worktree blocks the checkout or fast-forward, and the error text is cryptic.
  - The fetch fails offline and wrongly aborts the stage.
  - Restoring the starting branch on top of uncommitted calibration output loses nothing, but the warning must say so.
  - Resume after the calibrated branch already exists leaves it based on a stale commit.
- **Guardrails and early‑abort checks:**
  - A fetch failure is a warning when `origin/<branch>` is absent. When a stale remote-tracking ref is present, still
    refuse nothing, but log that the sync could not be confirmed.
  - If the existing calibrated branch does not descend from the origin head, log a warning with the rebuild command
    rather than reusing it silently.
  - Never use `reset --hard` or `push --force`.

### Success & Discard Criteria

- **Success:** Stage 5 runs on the origin head, refuses a divergent local ref with an actionable error, fast-forwards
  successfully, and ends on the starting branch; the existing Stage 5 tests still pass.
- **Discard:** If `run_calibrate` start-point plumbing needs changes beyond about 30 lines in `calibration.py` (1678
  lines already), stop and instead create the calibrated branch in `calibrate_sync` before calling it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 60.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 45.0  |

### Step Metrics Rationale

`ev_pred = 0.85 * 60.0 + 0.5 * 4.0 - 8.0 = 51.0 + 2.0 - 8.0 = 45.0`. This step carries almost all the value, and its
risk is the line budget and the interplay with the existing resume arms.

---

## Step 3: Guard the resume and edge cases (existing calibrated branch, detached HEAD, dry-run)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small refinements inside the Step 2 module, best done and tested together with it.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the behaviour safe when Stage 5 resumes or the flow starts in an unusual state.
- **Git branch:** I-1794529165-calsync/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Detached HEAD at start: record the SHA and restore with a detached checkout; log it.
- Starting branch equal to the execution branch: nothing to restore, and the log says so.
- Existing local or remote `/calibrated` branch that does not contain the origin head: warn and name the one-line
  recovery (delete the stale branch), rather than basing a report on a stale tree.
- Dry-run: no fetch, no ref moves, no restore (parity with the existing dry-run guards).
- Confirm no uncommitted ledger changes are left after a successful run: `git status --porcelain` is checked at the end
  and logged when not empty.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** safety.md (no destructive operation), ruleset.md section 2.
- **Potential failure modes for this step:** Over-warning noise; a status check failing in odd repos.
- **Guardrails and early‑abort checks:** Every check uses `check=False` and downgrades to a log line.

### Success & Discard Criteria

- **Success:** Each edge case has a defined, tested outcome and no new complexity-limit breach.
- **Discard:** If this grows past about 40 lines, defer the stale-calibrated-branch warning to a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 11.3  |

### Step Metrics Rationale

`ev_pred = 0.92 * 15.0 + 0.5 * 3.0 - 4.0 = 13.8 + 1.5 - 4.0 = 11.3`.

---

## Step 4: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests are the acceptance evidence and reuse existing fixtures. This step is declared as the
  test-file change that the world ruleset requires.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the four acceptance behaviours with local bare-repo fixtures only.
- **Git branch:** I-1794529165-calsync/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `apps/sandbox-executor/tests/test_calibrate_sync.py` using the fixtures in `worktree_fixtures.py` and
  `hermetic_fixtures.py` (a bare `origin`, a clone, and a second clone that pushes extra commits to the execution
  branch, including a merge-from-main style commit and a ledger-interleave commit):
  1. Origin ahead of the local ref: after the sync, `merge-base --is-ancestor origin/<exec> <calibrated>` holds, and
     `merge --ff-only` of the calibrated branch onto the execution branch succeeds without a merge commit.
  2. The calibrated report tree contains the files from the commits that only origin had (the stale tree defect).
  3. Divergence: a local-only commit makes Stage 5 return a failed result whose error names the branch, both SHAs and
     the recovery step; no calibrated branch is created and the local ref is untouched.
  4. Restore: the worktree starts on a third branch; after Stage 5 `HEAD` is that branch, no flow branch is checked out,
     and `git status --porcelain` is empty. Also a detached-HEAD case.
  5. No `origin/<exec>` (a skip-push run): the stage proceeds from the local ref.
  6. Dry-run touches no ref.
- Stub the agent and CI check as the existing `test_calibrate_final_head_ci.py` does, so no network, no Docker.
- Run the new file first with `uv run pytest apps/sandbox-executor/tests/test_calibrate_sync.py`, then the neighbours
  `test_calibration.py`, `test_calibrate_final_head_ci.py`, `test_flow_target_worktree.py` and
  `test_flow_default_branch_worktree.py`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3; `docs/testing/testing_guide.md` and `docs/hermetic_testing.md` (the network
  guard and credential hermeticity tests must stay green).
- **Potential failure modes for this step:** Fixtures that need a real `gh`; tests leaking the user git config.
- **Guardrails and early‑abort checks:** Use the repo's `gh_write_guard` and `network_guard`, and set the git identity
  in the fixture.

### Success & Discard Criteria

- **Success:** All new tests pass and fail on the pre-change code (verify at least test 1 and test 4 by stashing the
  source change once).
- **Discard:** Not applicable; tests are required.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 1.0   |
| impact_pred         | 30.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 21.6  |

### Step Metrics Rationale

`ev_pred = 0.92 * 30.0 + 0.5 * 6.0 - 9.0 = 27.6 + 3.0 - 9.0 = 21.6`. The test step has the highest learning value: it is
the first end-to-end proof that Stage 5 handles an origin-ahead state.

---

## Step 5: Documentation, lint and the full suite

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and mechanical; it depends on every earlier step.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the new behaviour and run the gates.
- **Git branch:** I-1794529165-calsync/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Update `docs/git_flow.md` (and the Stage 5 section of `docs/architecture.md` if it describes calibration) with: the
  fetch-and-base rule, the divergence error and its recovery, the guaranteed fast-forward, and the restored starting
  branch. Use Australian English (behaviour, recognise, artefact) and wrap prose as the repo's Prettier config does.
- Run `uv run pytest` on the affected test files first, then `uv run task lint`, `uv run task lint-docs`, and the full
  suite once with `uv run task test`. No repeated runs: the intent has no timing requirement.
- Confirm `scripts/check_flow_module_budgets.py` passes with the new entry and that `holon-knowledge/ledger` has exactly
  the three ledgers.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset.md section 3; the Prettier markdown gate.
- **Potential failure modes for this step:** Prettier reflow oscillation on formulas; a budget check failing.
- **Guardrails and early‑abort checks:** Put formulas in code spans and run `uv run task lint-docs` before the full
  suite.

### Success & Discard Criteria

- **Success:** `uv run pytest`, `uv run task lint` and `uv run task lint-docs` all pass.
- **Discard:** A failure outside the touch set that is not caused by this change is reported, not fixed here.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 7.5   |

### Step Metrics Rationale

`ev_pred = 0.95 * 10.0 + 0.5 * 2.0 - 3.0 = 9.5 + 1.0 - 3.0 = 7.5`.

---
