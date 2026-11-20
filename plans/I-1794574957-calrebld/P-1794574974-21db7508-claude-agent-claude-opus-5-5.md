# Plan for I-1794574957-calrebld

- **Plan ID:** P-1794574974-21db7508-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-13T13:02:55.225Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with a declared, minimal touch-set extension, see below)
- **Reframed intent (if applicable):** Not reframed. The scope list names `calibrate_sync.py`, `stages/calibrate.py` and
  `tests/`; the plan also touches (a) a new focused module `flow/calibrate_rebuild.py`, because `calibrate_sync.py`
  already sits exactly at its 183-line budget in `flow_module_budgets.json`; (b) `flow_module_budgets.json` for the new
  module's entry and small raises; (c) `cli/commands/flow.py`, whose `_report_flow_summary` is the flow's final report,
  for one line naming the calibrated head. All three follow from goal items 3 and 5 ("keep source files focused").
- **Exploration stance:** balanced, leaning conservative. The change is a guarded, destructive-on-purpose git operation
  (deleting and re-pointing a branch), so correctness of the "only calibration commits" classifier matters more than
  novelty; one small info-gathering step confirms the exact calibration commit shapes before coding.
- **Safety priority level:** elevated
- **Priority Justification:** The step deletes a local branch and, when the remote `/calibrated` branch is stale, must
  replace it on origin (a non-fast-forward update). `holon-config/world/constraints.md` §1 (commit boundaries, only
  humans merge to `main`) and §3 (append-only ledgers: dropping an unmerged calibration commit removes ledger rows that
  were never on the execution branch) both bear on it, and the module docstring promises "nothing here resets, forces or
  merges to main". The plan restricts every forced update to the flow's own `/calibrated` ref, uses `--force-with-lease`
  pinned to the observed SHA, and fails loudly otherwise.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6 steps is INFO_GATHERING / BALANCED)
- **Justification:** The design is clear from the code; the only real uncertainty is the exact set of commit subjects
  and paths that Stage 5 produces and how `run_calibrate` reacts to a rebuilt local branch beside a stale remote one.
  One cheap read-only step resolves both.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.1   |
| impact_pred         | 72.0  |
| cost_pred           | 14.0  |
| learning_value_pred | 4.5   |
| ev_pred             | 45.46 |

### Strategy Rationale

The failure is a deliberate fail-loudly guard in `prepare_calibrated_branch` (`calibrate_sync.py`) that fires whenever
an existing local or remote `/calibrated` branch does not contain the execution head. That guard is correct when the
branch carries someone else's work but wrong when the branch only carries Stage 5's own output from an earlier run. The
strategy is to keep the guard and add a narrow classifier in front of it:

1. Compute the commits on the existing branch that the execution head lacks (`git rev-list <start_sha>..<existing>`).
2. Empty range: the branch is a strict ancestor of the head, so it is simply re-pointed (rebuild without loss).
3. Non-empty range made only of Stage 5 calibration commits (non-merge, `chore(calibration): ` subject, paths inside the
   calibration allow-list): delete and rebuild from the origin execution head, log the replaced SHA(s).
4. Anything else: raise the existing `CalibrationSyncError` message unchanged.

The final report gets a `calibrated_head` field in the Stage 5 payload and one summary line, so a merged PR's
calibration report can be traced to a head (or flagged as not refreshed when Stage 5 failed).

Aggregation: `p_success` is the product-like bottleneck of Step 2 (classifier and rebuild, 0.88) and Step 5 (hermetic
tests over real bare-repo git, 0.90), softened because Steps 1, 3, 4, 6 are near-certain: `0.88 _ 0.90 _ 0.97 \* 0.97

- 0.98 ≈
  0.73`, raised to 0.82 because failures in Steps 3/4/6 are cheap to iterate inside the same execution and the existing `test*calibrate_sync.py` fixture already does most of the git scaffolding. Entropy is the sum of step entropies (`0.3 +
  2.0 + 0.9 + 0.8 + 1.5 + 0.6 = 6.1`). Cost is the sum of step costs (`1 + 4 + 2 + 2 + 3.5 + 1.5 =
  14.0`). Impact is the plan-level judgement: removes a manual step after every conflict merge and closes a silent reporting gap on merged PRs. EV uses `docs/metrics.md`: `EV
  = P(success) * Impact + mu _ LearningValue - lambda _ Delta*S_intent - Cost`with`mu = 0.5`, `lambda =
  0.3`, `Delta_S_intent ≈ 6.1`: `EV = 0.82 * 72.0 + 0.5 _ 4.5 - 0.3 _ 6.1 - 14.0 = 59.04 + 2.25 - 1.83 - 14.0 = 45.46`.

Strong assumptions: (a) Stage 5 never produces a merge commit on `/calibrated` (true today: `run_calibrate` and the two
follow-up commits in `stages/calibrate.py` are plain commits); (b) the operator accepts a `--force-with-lease` update of
the flow's own remote `/calibrated` ref when, and only when, it holds only calibration commits.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 Git Flow: changes stay on the execution branch; only humans merge to `main`.
  - `holon-config/world/constraints.md` §3 Ledger Immutability: exactly the three append-only ledgers; no rewriting.
  - `docs/safety.md` merge rules (no `--delete-branch`, no auto-merge) and hermetic testing rules
    (`apps/sandbox-executor/docs/hermetic_testing.md`): no network, bare-repo origins only.
  - Repository lint: ruff complexity limits, `flow_module_budgets.json` line budgets, `lint-docs`.
- **Potential violations or edge cases:**
  - A rebuilt branch drops unmerged calibration commits that appended rows to `holon-knowledge/ledger/executions.jsonl`
    on the `/calibrated` branch only. Those rows never reached the execution branch, so no merged ledger history is
    rewritten, and the rebuilt run appends fresh rows; still, it must be logged.
  - A remote `/calibrated` branch that is stale requires a non-fast-forward update on origin.
  - A user commit disguised with a `chore(calibration):` subject but touching source files.
  - A resolver force-push that rewrote the execution branch: old execution commits then appear in the range and must
    classify as foreign.
  - `--skip-push` runs and dry runs must not touch origin.
- **Mitigations built into the plan:**
  - Classifier requires all three: non-merge commit, `chore(calibration): ` subject, every changed path in the
    allow-list (calibration report under `plans/`, `holon-knowledge/ledger/executions.jsonl`, `holon-knowledge/kb/`,
    `holon-knowledge/wisdom/`, confirmed in Step 1). Any miss keeps the existing error verbatim.
  - The remote update is
    `git push --force-with-lease=<calibrated>:<observed_remote_sha> origin <start_sha>:refs/heads/<calibrated>`, never
    on `main` or the execution branch; a lease failure raises `CalibrationSyncError` (fail loudly). With `skip_push` the
    remote is untouched and only the local ref is rebuilt.
  - The log names the branch, the replaced local and remote SHAs, the number of calibration commits dropped and the new
    start SHA, so the old commits are recoverable by SHA.
  - No change to `publish_calibration`, merging, or ledger writers.
- **Residual risk accepted (and why):** A forced update of the flow's own `/calibrated` remote branch. Accepted because
  the branch is a flow-owned artefact, the lease pins the exact SHA inspected, the classifier proves it holds only
  calibration output, and the alternative (manual delete-and-resume) is what produced a stale report on a merged PR.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.1
- **Budget Compliance:** The strategy fits within budget; executed as one plan with no sub-intents.

## Plan Description & Strategy

Single execution branch, six steps: confirm calibration commit shapes (read-only), add a focused rebuild module, hook it
into `prepare_calibrated_branch`, surface the calibrated head in the payload and flow summary, add hermetic tests (and
update the one existing test whose scenario becomes a rebuild), then validate with targeted tests, the full suite,
`uv run task lint` and `uv run task lint-docs`. No new dependencies; Australian English in messages, docstrings and
comments ("behaviour", "recognise", "summarise").

---

## Step 1: Confirm the exact shape of Stage 5 calibration commits and the stale-remote path

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, minutes of work, and it de-risks the classifier that the whole plan depends on.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Every commit Stage 5 puts on `/calibrated` is a non-merge commit whose subject starts
  with `chore(calibration): ` and whose paths are limited to `plans/<plan>_calibration.md`,
  `holon-knowledge/ledger/executions.jsonl`, `holon-knowledge/kb/` and `holon-knowledge/wisdom/`.
- **Learning target:** The definitive subject list and `commit_paths` in `calibration.py` (around line 1640), whether
  `check_and_resolve_stage_conflicts` or `ledger_interleave.py` can commit onto `/calibrated`, and how `run_calibrate`
  behaves when the local `/calibrated` is re-pointed while `origin/<calibrated>` is stale (it runs
  `merge --ff-only origin/<calibrated>` and only warns on failure).
- **Maximum acceptable cost for this learning:** 1.0 cost unit; abort exploration after that and use the conservative
  allow-list above.

### Intent & Git Integration

- **Step Intent:** Pin down the classifier inputs before writing code.
- **Git branch:** `I-1794574957-calrebld/P-1794574974-21db7508-claude-agent-claude-opus-5-5/E-<ts>-calrebld/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `calibration.py` around the commit at line ~1640 to list `commit_paths` and the subject.
2. Read `stages/calibrate.py` `_commit_stage5_reconciliation` and `_commit_stage5_promoted_artifacts` (subjects
   `chore(calibration): record stage 5 reconciliation` and `... promoted knowledge and wisdom`).
3. Grep `flow/` for any other commit that can land on a `.../calibrated` ref (ledger interleave, stage conflicts).
4. Read `run_calibrate`'s branch switch (lines ~1547-1600) to confirm a pre-created local branch is checked out as is.
5. Record findings as a short note in the execution report, not in the repository.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1 (no commits), hermetic_testing.md.
- **Potential failure modes for this step:** missing a commit producer, leading to a classifier that is too strict
  (safe: it keeps failing loudly) or too lax (unsafe).
- **Guardrails and early‑abort checks:** when in doubt, keep a path out of the allow-list; strictness only costs a
  manual recovery, laxity could delete foreign work.

### Success & Discard Criteria

- **Success:** A confirmed list of subjects and paths, and confirmation that only plain commits are produced.
- **Discard:** If a Stage 5 producer creates merge commits on `/calibrated`, stop and re-plan the classifier.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 7.67  |

### Step Metrics Rationale

`EV = 0.97 * 8.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 7.76 + 1.0 - 0.09 - 1.0 = 7.67`. Low impact on its own but it removes
the main correctness risk of Step 2.

---

## Step 2: Add `flow/calibrate_rebuild.py` with the calibration-commit classifier and the rebuild

- **Sub‑intent recommendation:** NO
- **Reasoning:** About 90-120 lines in one focused module; risky logic but small and fully covered by Step 5's hermetic
  tests in the same branch, so a separate evaluation cycle adds cost without reducing risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Decide whether an existing `/calibrated` branch is the flow's own stale output and, if so, rebuild it
  from the origin execution head with a logged, lease-protected replacement.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New module `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rebuild.py`, module docstring explaining the
   rule: rebuild the flow's own stale branch, refuse to touch foreign work, never force anything but `/calibrated`.
2. Constants: `CALIBRATION_SUBJECT_PREFIX = "chore(calibration): "` and an allow-list predicate for paths (report file
   under `plans/` ending `_calibration.md`, `holon-knowledge/ledger/executions.jsonl`, prefixes `holon-knowledge/kb/`
   and `holon-knowledge/wisdom/`), adjusted to Step 1's findings.
3. `commits_missing_from_head(repo_dir, start_sha, existing_sha) -> list[str]` using
   `git rev-list <start_sha>..<existing_sha>`; a git failure returns `None` and is treated as foreign (fail loudly).
4. `is_calibration_commit(repo_dir, sha) -> bool`: reject when the commit has more than one parent
   (`git rev-list --parents -n 1`), when the subject (`git log -1 --format=%s`) lacks the prefix, or when any path from
   `git diff-tree --no-commit-id --name-only -r --root <sha>` falls outside the allow-list. The commit author is logged
   but not required, because `calibration_commit_env` only sets the identity with `setdefault`.
5. `holds_only_calibration_commits(repo_dir, start_sha, existing_sha) -> bool`: true when the range is empty (strict
   ancestor) or every commit passes step 4.
6. `rebuild_calibrated_branch(context, start_sha, local_sha, remote_sha) -> None`:
   - If `remote_sha` exists and is not a descendant of `start_sha`, and `context.skip_push` is false, run
     `git push --force-with-lease=<calibrated>:<remote_sha> origin <start_sha>:refs/heads/<calibrated>` and then
     `git update-ref refs/remotes/origin/<calibrated> <start_sha>`; on non-zero exit raise `CalibrationSyncError` naming
     the branch and the lease SHA. With `skip_push` origin and the remote-tracking ref are left as they are; the later
     `merge --ff-only origin/<calibrated>` in `run_calibrate` then only logs a warning.
   - Re-point the local branch with `git branch -f --no-track <calibrated> <start_sha>` (or create it when absent);
     refuse when HEAD is currently on `<calibrated>` by first checking out the execution branch (Stage 5 already did).
   - Log one line in Australian English, for example
     `Stage 5: rebuilt '<calibrated>' from origin head <start12>; replaced local <old12> and origin <old12> (<n> calibration commit(s) not on the execution head).`
7. Keep each function below `C901 <= 10` and `PLR0913 <= 5`; no `# noqa`. Keep the dependency one-directional:
   `calibrate_sync.py` imports from `calibrate_rebuild.py`, never the reverse. Move `CalibrationSyncError` into
   `calibrate_rebuild.py` and re-export it from `calibrate_sync.py` so existing imports keep working, and give
   `calibrate_rebuild.py` its own small private rev-parse and ancestry helpers.
8. Add the module to `flow_module_budgets.json` at its creation size and extend the description sentence.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (p_success 0.88 > 0.6, so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** constraints.md §1, §3; the `calibrate_sync.py` docstring promise about forcing; safety.md merge
  rules.
- **Potential failure modes for this step:** classifier too lax; lease push touching a wrong ref; cyclic import.
- **Guardrails and early‑abort checks:** forced push only with an explicit refspec whose destination equals
  `refs/heads/` plus `calibration_branch(context.execution_branch)`; assert the destination ends with `/calibrated`
  before pushing; never call any force path with `main` or the execution branch.

### Success & Discard Criteria

- **Success:** Module lints clean, under budget, unit-level behaviour verified by Step 5.
- **Discard:** If the classifier cannot be expressed without merge-commit heuristics or the lease approach is rejected
  by review, fall back to rebuilding only the strict-ancestor case and keep the loud failure for diverged branches.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 2.0   |
| impact_pred         | 40.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 31.1  |

### Step Metrics Rationale

`EV = 0.88 * 40.0 + 0.5 * 1.0 - 0.3 * 2.0 - 4.0 = 35.2 + 0.5 - 0.6 - 4.0 = 31.1`. Highest entropy of the plan because it
adds a destructive git path; mitigated by narrow refspecs and the lease.

---

## Step 3: Hook the rebuild into `prepare_calibrated_branch`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few-line change at one call site; low risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the unconditional raise with "rebuild when the flow owns the branch, raise otherwise".
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `prepare_calibrated_branch`, collect
   `stale = [sha for sha in (local_sha, remote_sha) if sha and not _is_ancestor(repo_dir, start_sha, sha)]`.
2. If `stale` is empty, return False as today.
3. If every stale SHA passes `holds_only_calibration_commits`, call `rebuild_calibrated_branch(...)` and return True
   (the branch was created by this call, so `discard_empty_calibrated_branch` can still clean up a failed calibration).
4. Otherwise raise `CalibrationSyncError` with the existing message text, byte for byte.
5. Update the docstring (Returns/Raises) and the module docstring line about forcing to say the flow's own `/calibrated`
   ref is the only one it may replace, and only with a lease.
6. Raise the `calibrate_sync.py` budget in `flow_module_budgets.json` by the exact number of added lines and append the
   reason to the description, matching the existing convention.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** budgets lint (`scripts/check_flow_module_budgets.py`), ruff complexity.
- **Potential failure modes for this step:** message drift breaking the existing assertion
  `"does not contain the execution head"`; complexity creeping over `C901`.
- **Guardrails and early‑abort checks:** keep the message in one f-string unchanged; extract a helper if complexity
  rises.

### Success & Discard Criteria

- **Success:** The foreign case raises the unchanged message; the own-stale case rebuilds.
- **Discard:** If the budget raise exceeds ~10 lines, move more logic into `calibrate_rebuild.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.9   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 12.53 |

### Step Metrics Rationale

`EV = 0.97 * 15.0 + 0.5 * 0.5 - 0.3 * 0.9 - 2.0 = 14.55 + 0.25 - 0.27 - 2.0 = 12.53`.

---

## Step 4: State which head the calibration report covers in the final report

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small payload plus one summary line; low risk and directly answers goal 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the Stage 5 result and the flow summary name the execution head the calibration report covers,
  or say plainly that no report was refreshed for the current head.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `stages/calibrate.py`, keep `start_sha` from `sync_execution_ref` in scope and add `"calibrated_head": start_sha`
   to the success payload; on the `CalibrationSyncError` failure paths pass a payload with `"calibrated_head": None` if
   `_stage_failure` accepts one, otherwise record it on the returned `StageResult.payload` before returning.
2. Extend the "Stage 5 completed" log line with `covering execution head <sha12>`.
3. In `cli/commands/flow.py` `_report_flow_summary`, for the `calibrate` stage print
   `calibration report covers execution head <sha12>` when `calibrated_head` is set, and
   `calibration report not refreshed for the current head` when Stage 5 failed. JSON output already carries the payload.
4. Raise the `stages/calibrate.py` budget by the exact added lines with a description note. `cli/` is not under the flow
   budget file.

### Dependencies & Criticality

- **Depends on:** NONE (independent of Steps 2-3; tested in Step 5)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** no entrypoint imports into the flow engine (the CLI already imports from `sandbox_executor.flow`,
  which is the allowed direction); Australian English.
- **Potential failure modes for this step:** the `noqa`-carrying `_calibrate_execution_branch` growing further; payload
  key clashing with an existing consumer.
- **Guardrails and early‑abort checks:** grep for `calibrated_head` before adding; keep the change to a handful of
  lines.

### Success & Discard Criteria

- **Success:** Summary prints the head SHA; JSON payload carries it.
- **Discard:** If `_report_flow_summary` changes break existing CLI tests that compare exact output, add the line only
  to the payload and the log and note it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 12.26 |

### Step Metrics Rationale

`EV = 0.95 * 15.0 + 0.5 * 0.5 - 0.3 * 0.8 - 2.0 = 14.25 + 0.25 - 0.24 - 2.0 = 12.26`.

---

## Step 5: Hermetic tests for rebuild, foreign-commit refusal and the reported head

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests live alongside the change and reuse the existing bare-repo fixture; splitting would leave the
  implementation unverified.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the three acceptance behaviours with real git on bare-repo origins, no network.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New file `apps/sandbox-executor/tests/test_calibrate_rebuild.py`, reusing `repos`, `git`, `commit_file`,
   `push_ahead`, `run_stage` from `test_calibrate_sync.py` (import them, or lift them into a small shared fixture module
   if importing a test module trips lint).
2. Test: diverged own branch is rebuilt. Create `/calibrated` at the old execution head, add a commit with subject
   `chore(calibration): add calibration report for P-2-y` touching `plans/P-2-y_calibration.md`, push it to origin, then
   `push_ahead`. Run the stage: SUCCESS; `/calibrated` and origin `/calibrated` descend from the new origin head; the
   log contains `rebuilt`, the old SHA prefix and the new head prefix.
3. Test: strict-ancestor branch is rebuilt (the scenario of the current
   `test_stale_existing_calibrated_branch_fails_ loudly`, a `/calibrated` at `main`). Update that existing test, renamed
   to describe the rebuild, since an ancestor with no own commits is now the flow's own stale branch by definition.
4. Test: foreign commit still stops. Same as test 2 but the commit subject is `add notes` (or the subject is correct and
   it touches `src/x.py`, as a second parametrised case). Assert FAILED, the message contains
   `does not contain the execution head` and `git branch -D`, and both local and origin `/calibrated` SHAs are
   unchanged.
5. Test: merge commit on `/calibrated` counts as foreign.
6. Test: `skip_push=True` rebuilds locally and leaves origin `/calibrated` untouched.
7. Test: the Stage 5 payload `calibrated_head` equals the origin execution head, and `_report_flow_summary` (via
   `capsys`) prints `calibration report covers execution head <sha12>`; a failed Stage 5 prints the "not refreshed"
   line.
8. All tests use `tmp_path`, set `GIT_CONFIG_GLOBAL=/dev/null`, never touch repository ledgers.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4
- **Is Bottleneck:** YES (p_success 0.90 > 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `apps/sandbox-executor/docs/hermetic_testing.md`, the network guard in `tests/conftest.py`, tests
  strictly via `uv run pytest`.
- **Potential failure modes for this step:** `fake_calibrate` checks out an existing branch, so test expectations must
  follow the re-pointed branch; the push from the `other` clone must not race the host.
- **Guardrails and early‑abort checks:** run the new file alone first; a flaky result is a bug, not a retry case.

### Success & Discard Criteria

- **Success:** All new and updated tests pass with
  `uv run pytest tests/test_calibrate_rebuild.py tests/test_calibrate_sync.py`.
- **Discard:** If cost exceeds 1.5 times `cost_pred`, keep tests 2-4 and 7 (the acceptance set) and drop the extras.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 19.05 |

### Step Metrics Rationale

`EV = 0.90 * 25.0 + 0.5 * 1.0 - 0.3 * 1.5 - 3.5 = 22.5 + 0.5 - 0.45 - 3.5 = 19.05`. Cost includes updating the existing
stale-branch test.

---

## Step 6: Validate with targeted tests, the full suite once, lint and lint-docs

- **Sub‑intent recommendation:** NO
- **Reasoning:** Standard gate, no timing measurement involved, so no repeated runs.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Meet acceptance criterion 4.
- **Git branch:** same execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From `apps/sandbox-executor`:
   `uv run pytest tests/test_calibrate_rebuild.py tests/test_calibrate_sync.py tests/test_calibrate_final_head_ci.py tests/test_cli.py tests/test_calibration.py`.
2. Full suite once: `uv run task test`.
3. `uv run task lint` (ruff, complexity, flow module budgets) and `uv run task lint-docs`.
4. Report pass/fail counts and pytest-reported time; no repeated runs, as the intent does not measure timing.
5. Confirm `git diff --stat` touches only the declared modules, tests and `flow_module_budgets.json`, and no ledger
   file.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** exactly three append-only ledgers untouched; merging stays with the human maintainer (no merge
  commands run).
- **Potential failure modes for this step:** an unrelated pre-existing flaky test; budget lint mismatch by a line.
- **Guardrails and early‑abort checks:** if an unrelated test fails, re-run only that test once to confirm and record it
  as pre-existing rather than changing it.

### Success & Discard Criteria

- **Success:** All green; diff limited to the touch set.
- **Discard:** A failure caused by this change that cannot be fixed within 1.5 times `cost_pred`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.07  |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 0.5 - 0.3 * 0.6 - 1.5 = 9.5 + 0.25 - 0.18 - 1.5 = 8.07`.

---

## Touch Set (module granularity)

- New: `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rebuild.py`
- Changed: `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_sync.py`
- Changed: `apps/sandbox-executor/src/sandbox_executor/flow/stages/calibrate.py`
- Changed: `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py` (one summary line)
- Changed: `apps/sandbox-executor/flow_module_budgets.json`
- New: `apps/sandbox-executor/tests/test_calibrate_rebuild.py`; changed:
  `apps/sandbox-executor/tests/test_calibrate_sync.py`

## Sub-intent list

None
