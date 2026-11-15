# Plan for I-1794727635-guardbase

- **Plan ID:** P-1794727651-2a1e918e-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-15T07:27:32.734Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (narrow and sharpen; same goal)
- **Reframed intent (if applicable):**
  `{"slug": "guardbase", "change": "When the execution branch's own ledger records an integrated decomposition of this plan branch, the empty-diff guard compares the combined execution branch with its merge base on the intent's target branch using a net (merge-inclusive) diff; every other execution keeps the current plan-branch comparison", "extra_touch": "review_target.py passes the target branch it already receives through to _execution_effective_diff (one call site)"}`
- **Exploration stance:** conservative. The fault is understood and limited to one guard. The only non-obvious part is
  that `changed_paths` counts only the head's own first-parent, non-merge commits, which a plain base switch would not
  fix. The plan deals with that directly rather than testing alternatives.
- **Safety priority level:** elevated
- **Priority Justification:** The empty-diff guard is a fail-closed safety gate in front of PR creation
  (`docs/safety.md` "Human review is the final gate" and "Failure containment"). Loosening it the wrong way would let
  bookkeeping-only branches reach human review, or let the base switch be triggered falsely. Every new branch of the
  logic therefore has to fail closed (unverifiable means refused), and the plain-execution path must stay byte-for-byte
  unchanged.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6 steps is a short, read-only verification step)
- **Justification:** The root cause has already been traced in the code (see Plan Description). The remaining
  uncertainties are wiring facts: whether the host fetches the target branch before a mounted review container runs, and
  what the package-export test requires of a new flow module. A short read-only step checks both before any edit. It is
  not a speculative experiment.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 6.3   |
| impact_pred         | 80.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 53.71 |

## Plan Description & Strategy

### Root cause (verified while planning)

1. `apps/sandbox-executor/src/sandbox_executor/flow/git_refs.py` `_execution_effective_diff` derives the plan branch
   from the execution branch name. It then calls `changed_paths(repo_dir, plan_ref, exec_ref)` and, when that is empty,
   reports `empty_diff_summary(plan_branch)` with `(compared on origin)`.
2. In a decomposed flow, `flow/stages/integration.py` `integrate_children` merges the plan branch, which already holds
   every child PR, into the parent execution branch with a **merge commit**. It then appends a superseding
   `executions.jsonl` summary row with `status: "decomposed"`, `integrated: true`, `plan_branch` and `children`.
3. As a result, the execution branch minus the plan branch contains only ledger, plans and executions content.
4. There is a second trap that the intent text does not mention. `git.py` `changed_paths(base, head)` counts a path only
   when it is in the net `base...head` diff **and** one of the head's own `--first-parent --no-merges` commits touched
   it. The children's work arrives on the execution branch only through the integration merge (and through merge or
   squash commits on the plan branch). So comparing against main with the existing `changed_paths` would **still**
   report the branch as empty. The decomposed path needs a net, merge-inclusive diff.
5. The guard is reached from `flow/review/loop.py` (line ~249) through
   `review_target.classify_review_diff(repo_dir, review_target, target_branch)` and then `_classify_target_diff`, which
   calls `_execution_effective_diff(repo_dir, branch)` without the target branch. `loop.py` already holds
   `target_branch` (from `context.intent_data`), so it needs no change. Only the one `review_target.py` call site has to
   pass the branch through.

### Strategy

- **Detection, using the data owner and failing closed:** a branch is treated as a combined (decomposed) execution only
  when the latest `executions.jsonl` summary row at the fetched execution ref records `integrated: true`, its
  `plan_branch` equals the derived plan branch, and its `children` list is non-empty. These rows are written by
  `integration.py`, so the ledger is the authority. Any other execution, including every plain one, takes today's code
  path unchanged.
- **Base:** `git merge-base <target_ref> <exec_ref>`, where the target is the intent's `target_branch` (`main` for a
  root intent, or the parent plan branch for a nested child). The execution branch contains the plan branch, so this is
  the plan's base on the target. If the target was synced into the plan or execution branch later, it is that newer
  point instead, which correctly keeps the synced target content out of the count. A missing merge base, or a target ref
  that was not fetched or cannot be fetched, gives `None` (unverifiable, PR refused).
- **Diff:** a net `base..exec` name-only diff with renames disabled, minus `EMPTY_DIFF_EXCLUDES`. There is no own-commit
  filter, because merged children are exactly what must count.
- **Summary when refused:** `empty_diff_summary(...)` is given a base label that names the actual base. For example:
  `main@1a2b3c4d5e6f (merge base of 'I-.../P-.../_' with main)`, followed by `(compared on origin)` or the local source.
  This satisfies goal 2.
- **Code placement:** `git_refs.py` is exactly at its 394-line budget. The new logic therefore goes into a new focused
  module, `flow/combined_base.py`, which imports only `sandbox_executor.git` and `sandbox_executor.redaction`. That
  avoids an import cycle and avoids any new entrypoint-to-flow import, because `git_refs.py` passes in the result of
  `workspace_mounted()`, which it already imports. `git_refs.py` gains only the call site. Its budget is raised by the
  exact net growth, with a sentence appended to the `flow_module_budgets.json` description, following the established
  practice in that file. The new module gets a budget entry at creation size.
- **Single execution:** the whole change is about 120 to 180 changed lines of source and tests, well under the entropy
  budget of 15.0. No sub-intents are used. Splitting it would itself create a decomposed flow, which is the path this
  intent repairs, and would add risk with no benefit.

### Touch set (module granularity)

- `apps/sandbox-executor/src/sandbox_executor/git.py`: new `net_changed_paths` helper.
- `apps/sandbox-executor/src/sandbox_executor/flow/combined_base.py` (new): detection, base resolution and verdict.
- `apps/sandbox-executor/src/sandbox_executor/flow/git_refs.py`: new `target_branch` parameter and the call into
  `combined_base`.
- `apps/sandbox-executor/src/sandbox_executor/review_target.py`: one call site passes `base_branch` through. This is
  outside the intent's literal scope list, but it is the only link between `loop.py` and `git_refs.py` and is required
  for goal 1.
- `apps/sandbox-executor/flow_module_budgets.json`: new entry, `git_refs.py` raise, and a description sentence.
- `apps/sandbox-executor/src/sandbox_executor/flow/__init__.py`: only if the package-export test requires the new public
  names (Step 1 decides).
- `apps/sandbox-executor/tests/test_combined_base.py` (new) and a small addition to `tests/test_git.py`.
- `flow/review/loop.py` is in scope but is expected **not** to change, because it already passes `target_branch`.

---

## Step 1: Confirm wiring facts before editing (read-only)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A cheap read-only check that removes the two remaining unknowns. It produces no artefact of its own.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** In a mounted review container, the host has already fetched
  `refs/remotes/origin/<target_branch>` before dispatch. A new flow module with public names must be re-exported from
  `flow/__init__.py`.
- **Learning target:** Whether the mounted path can rely on the target ref, or must report it as "not fetched" (fail
  closed). Whether the new module's helpers should be private (underscore) names, so that `__init__.py` and its budget
  stay untouched.
- **Maximum acceptable cost for this learning:** about 10% of plan cost (`<= 1.5` cost units). Stop reading after that.

### Intent & Git Integration

- **Step Intent:** Pin down the fetch and export assumptions that the implementation relies on.
- **Git branch:** the plan's execution branch (no separate branch).
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Grep `flow/stages/review.py`, `flow/host_remote.py` (`fetch_base`) and `entrypoint/review_loop.py` for what the host
   fetches before a review dispatch. In particular, check whether the target branch is fetched.
2. Grep the tests for the package-export test (the budget notes refer to "the package export test"). Read its rule for
   new modules.
3. Confirm in `flow/github/merge.py` and `merge_method.py` that child PRs land on the plan branch as a merge commit or a
   squash. Both arrive on the execution branch through the integration merge, which confirms that the net diff is
   needed.
4. Record the findings in the execution notes. If the host does **not** fetch the target before a mounted review, keep
   fail-closed behaviour (unverifiable, with a message naming the unfetched target). Do not add a fetch inside the
   container. Flag the host-side gap in the execution notes rather than widening scope.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Git is the safety boundary"; prompt rule "make no code changes outside the
  plan".
- **Potential failure modes for this step:** misreading the mounted-container contract.
- **Guardrails and early‑abort checks:** read only; no edits in this step.

### Success & Discard Criteria

- **Success:** Both questions are answered with file and line references in the execution notes.
- **Discard:** If the answers cannot be found within the cost cap, assume the fail-closed choices (target must already
  be fetched when mounted; private helper names) and continue.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 8.51  |

### Step Metrics Rationale

`EV = 0.95 * 8.0 + 0.5 * 4.0 - 0.3 * 0.3 - 1.0 = 7.60 + 2.00 - 0.09 - 1.0 = 8.51`. A read-only step with high certainty
and modest direct impact. Its value lies in avoiding a wrong fetch assumption in Step 3.

---

## Step 2: Add a net, merge-inclusive changed-path helper to git.py

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small pure helper next to `changed_paths`, which owns the empty-diff definition. Low risk and reused
  only by Step 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide `net_changed_paths(repo_dir, base, head, exclude=EMPTY_DIFF_EXCLUDES, git_runner=None)`. It
  returns the sorted, de-duplicated paths of the net `base..head` diff outside the excluded prefixes, counting content
  that arrived through merges.
- **Git branch:** the plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `git.py`, directly after `changed_paths`, add `net_changed_paths` with five parameters (within `PLR0913 <= 5`). It
   runs one `git diff --name-only --no-renames -z <base> <head>` (two-dot, because the caller passes an explicit
   merge-base SHA). It filters out empty entries and excluded paths with the existing `_is_excluded` and returns a
   sorted list.
2. Reuse the same default runner pattern as `changed_paths` (`GitRepository(repo_dir).run(args).stdout`) so that git
   failures raise `RuntimeError`, which the callers treat as fail-closed.
3. Write the docstring in Australian English. It states why the own-commit filter is deliberately absent: a combined
   decomposed branch receives its children through merges. It also states that the caller must pass a base that already
   excludes target-branch syncs (a merge base).
4. Update the module docstring bullet at the top of `git.py` (line ~10) to mention the helper.
5. Leave `changed_paths`, `has_effective_diff`, `EMPTY_DIFF_EXCLUDES` and `empty_diff_summary` unchanged, so the
   executor entrypoint's in-container guard (`entrypoint/executor.py` ~1395) behaves exactly as before.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset "Typing Guidelines" and "Docstring Requirements"; the no-new-dependencies goal.
- **Potential failure modes for this step:** a three-dot diff accidentally used, or a rename hiding a move into an
  excluded directory.
- **Guardrails and early‑abort checks:** `--no-renames` exactly as `_NAME_ONLY_ARGS`, reusing that tuple; unit tests in
  Step 5.

### Success & Discard Criteria

- **Success:** The helper exists, `uv run task lint` is clean for `git.py`, and the existing `tests/test_git.py` still
  passes.
- **Discard:** Any need to change `changed_paths` semantics. Stop and keep the old function intact.

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

`EV = 0.95 * 15.0 + 0.5 * 1.0 - 0.3 * 0.8 - 1.5 = 14.25 + 0.50 - 0.24 - 1.5 = 13.01`. The pattern is well known and sits
next to an existing, tested function.

---

## Step 3: Add flow/combined_base.py and route the guard for integrated executions

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the core change and the bottleneck. It is still small: one new module of about 70 to 90 lines,
  about 8 net lines in `git_refs.py` and about 2 in `review_target.py`. A sub-intent would add flow overhead with no
  isolation gain.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `_execution_effective_diff` compare a combined decomposed execution branch with its base on the
  target branch, while keeping the plain path and the fail-closed behaviour.
- **Git branch:** the plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `flow/combined_base.py` with a module docstring that explains the plan-owned decomposition layout and why the
   plan branch cannot be the base. It contains three small functions, private unless Step 1 says otherwise, each under
   the ruff complexity limits:
   - `integrated_plan_recorded(rows, plan_branch) -> bool`: True only when some `executions.jsonl` summary row has
     `integrated` true, `plan_branch` equal to the derived plan branch, and a non-empty `children` list. The rows are
     parsed by `git_refs._read_ledger_rows` and passed in, so `combined_base` never imports `git_refs`.
   - `target_ref_error(repo_dir, target_branch, mounted) -> tuple[str | None, str | None]`: returns `(ref, None)` or
     `(None, reason)`.
     - With an origin remote and not mounted: fetch `+refs/heads/<target>:refs/remotes/origin/<target>` with
       `run_git(..., check=False, timeout=300)` and redact any stderr with `redact_agent_secrets`.
     - When mounted: only verify that the tracking ref exists, otherwise return "'<target>' is not fetched from origin;
       the host must fetch it before dispatch".
     - Without an origin remote: resolve the local branch.
   - `combined_diff_verdict(repo_dir, plan_branch, exec_ref, target, source) -> tuple[bool | None, str]`, where `target`
     is a `(branch, ref)` pair to stay within five parameters.
     - Run `git merge-base <target_ref> <exec_ref>`. A failure or empty output returns
       `(None, "no merge base between '<target>' and '<plan_branch>'")`.
     - Call `net_changed_paths(repo_dir, base_sha, exec_ref)`. A `RuntimeError` or `TimeoutError` returns `None` with a
       redacted reason.
     - If no paths changed, return `(False, f"{empty_diff_summary(label)} (compared on {source})")`, where `label` is
       `<target>@<sha12> (merge base of '<plan_branch>' with <target>)`.
     - Otherwise return `(True, "<n> changed path(s) against <label> on <source>")`.
2. In `git_refs.py`:
   - Add a keyword parameter `target_branch: str = "main"` to `_execution_effective_diff` and a one-line note in its
     docstring.
   - After the existing plan and execution refs are resolved (origin or local), and before the plan-branch diff: read
     the rows with `_read_ledger_rows(repo_dir, exec_ref, "executions.jsonl")`. If
     `integrated_plan_recorded(rows, plan_branch)`, resolve the target with
     `target_ref_error(repo_dir, target_branch, workspace_mounted())` (or the local resolution when there is no origin),
     return `None` on an error, and otherwise return `combined_diff_verdict(...)`.
   - Leave everything else, including the plain-path return strings, untouched.
   - Keep the function within `C901 <= 10` and `PLR0912 <= 12`. If it would exceed them, move the "is this a combined
     execution, and if so its verdict" block into one `combined_base` entry point that takes the rows and refs, so
     `git_refs.py` grows by about 5 lines only.
3. In `review_target.py` `_classify_target_diff`, pass `base_branch` as `target_branch=` to `_execution_effective_diff`.
   `loop.py` already supplies `target_branch` to `classify_review_diff`, so it is left unchanged. Check whether
   `loop.py` reaches the guard by another route, and wire it the same way if it does.
4. Update `flow_module_budgets.json`:
   - Add `"combined_base.py": <final line count>`.
   - Raise `git_refs.py` by its exact net growth.
   - If Step 1 requires it, raise `__init__.py` by its export lines.
   - Append one sentence to the description, in the file's existing style, saying why.
5. Confirm with `uv run python scripts/check_flow_module_budgets.py`, or through `uv run task lint`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (p_success 0.86 is above 0.6, so a sub-intent is not required)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review is the final gate" and "Failure containment"; the module-budget and
  complexity rules from the planner prompt; no new imports from entrypoints into the flow engine; the intent goal "keep
  exactly the three append-only ledgers" (the ledgers are only read here, never written).
- **Potential failure modes for this step:**
  - The guard is opened falsely: a plain execution's branch carries a forged `integrated` row and so gets the target
    base. Mitigation: the row must name the exact derived plan branch and list children. For a plain execution, the plan
    branch normally holds only plan files, so switching the base adds no implementation paths. This is accepted and
    documented as residual risk.
  - The mounted container lacks the target ref. This fails closed with a named reason (see Step 1).
  - An import cycle between `git_refs` and `combined_base`. Prevented because `combined_base` imports only `git` and
    `redaction`.
  - Budget overflow in `git_refs.py`. Handled by moving logic into `combined_base` and an explicit, justified budget
    raise.
- **Guardrails and early‑abort checks:**
  - Run
    `uv run pytest apps/sandbox-executor/tests/test_review_mounted_no_fetch.py tests/test_review_loop.py tests/test_review_target.py`
    immediately after this step. Any change in plain-path messages is a regression and must be reverted.

### Success & Discard Criteria

- **Success:**
  - The existing guard tests pass unchanged.
  - A manual check in a temporary repository shows a combined branch reported as `True` against `main@<sha>`.
  - Lint and the budget check pass.
- **Discard:**
  - The change needs to edit `changed_paths`' semantics or the executor entrypoint's guard.
  - Or `git_refs.py` would need more than about 15 extra lines.
  - In either case, re-plan with the decision moved to `review_target.py` instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.86  |
| entropy_pred        | 2.6   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 34.92 |

### Step Metrics Rationale

`EV = 0.86 * 45.0 + 0.5 * 2.0 - 0.3 * 2.6 - 4.0 = 38.70 + 1.00 - 0.78 - 4.0 = 34.92`.

- **Impact:** this step carries most of the plan's impact, because it unblocks Stage 4 for every decomposed flow.
- **Entropy:** moderate, because it touches a safety gate across four files.
- **Risks to p_success:** the budget and complexity limits, and the mounted-fetch contract.

---

## Step 4: Hermetic tests for the combined-execution guard

- **Sub‑intent recommendation:** NO
- **Reasoning:** The tests use local bare repositories only and follow the existing fixture patterns
  (`TestReviewLoopEmptyDiffGuardOrigin._setup` and `tests/test_review_mounted_no_fetch.py`). They are reusable but
  small.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove goals 1 to 3 with hermetic pytest tests and no network. These test-file additions are declared
  here, as `world/ruleset.md` section 3 requires.
- **Git branch:** the plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/test_combined_base.py`. It uses a module-level helper that builds the following
   layout in `tmp_path`:
   - a bare `origin.git`;
   - a working clone with `main` (holding `app.py` and an empty `holon-knowledge/ledger/executions.jsonl`);
   - the plan branch `I-1-guard/P-1-claude-agent-m/_` off `main`, with a `plans/` file;
   - a child branch `I-1-guard/P-1-claude-agent-m/I-2-part/_` that changes `src/feature.py`, merged into the plan branch
     with `--no-ff` (and a parametrised squash variant);
   - the execution branch `.../E-2-claude-agent-m/_` taken off the plan branch **before** the child merged, then given a
     `--no-ff` merge of the plan branch, plus an `executions.jsonl` row with `entry_type: "summary"`,
     `status: "decomposed"`, `integrated: true`, the plan branch and one child;
   - everything pushed to origin.
   - Configure the git identity locally in each repository, as the existing tests do.
2. Tests, each asserting both the verdict and the detail text:
   - `test_combined_execution_with_merged_children_passes`:
     `_execution_effective_diff(host, EXEC, target_branch="main")` returns `True`. The detail names `main@`. Run it for
     both the merge and the squash variant.
   - `test_combined_execution_with_only_bookkeeping_is_refused`: the child changes only `executions/` and `plans/`
     files. The result is `False`, and the detail starts with
     `Execution produced no changes outside holon-knowledge/ledger/, plans/ and executions/ relative to main@`, contains
     the merge-base SHA prefix and the plan branch name, and ends with `(compared on origin)`.
   - `test_plain_execution_is_unchanged`: no integrated row, and the execution branch changes only the ledger while the
     plan branch has no implementation. The result is `False`, with exactly the old summary
     `... relative to <plan branch> (compared on origin)`. A second case with an implementation commit on the execution
     branch is `True` with the old `"<n> changed path(s) on origin"` text.
   - `test_integrated_row_for_another_plan_branch_keeps_plan_base`: a row naming a different `plan_branch` (or an empty
     `children` list) leaves the plain comparison in place. This is the fail-closed detection test.
   - `test_compared_on_origin_from_unfetched_host`: an unfetched host clone (origin remote only) fetches the plan,
     execution and target branches itself and reaches the same `True` or `False` verdicts with `(compared on origin)`.
   - `test_mounted_container_without_target_ref_is_unverifiable`: patch
     `sandbox_executor.flow.git_refs.workspace_mounted` to return `True` with the plan and execution refs present but
     not `main`. The result is `None`, and the reason names `'main'`.
   - `test_missing_merge_base_is_unverifiable`: the target is an orphan branch. The result is `None`.
3. In `tests/test_git.py`, add two unit tests for `net_changed_paths`:
   - content arriving through a merge is counted;
   - excluded prefixes and a rename into `executions/` behave as `changed_paths` does (the source path is reported).
4. Add one test through `review_target.classify_review_diff(repo, EXEC, "main")` on the combined layout, asserting
   `DiffVerdict.VERIFIED`. This proves the `target_branch` threading from the review loop's call shape.
5. Keep every test hermetic: use `tmp_path` and local paths only, with no `gh` and no network. Use the existing
   `conftest.py` and network guard unchanged.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO (a failing test points to a Step 3 defect, which is fixed in place)

### Safety & Constraint Considerations

- **Relevant rules:** `world/ruleset.md` section 3 (tests in `tests/`, declared in the plan); the intent goal of running
  tests strictly via `uv run pytest`; `apps/sandbox-executor/docs/hermetic_testing.md`.
- **Potential failure modes for this step:**
  - Git default-branch or identity differences on the runner.
  - Flaky ordering in squash merges.
- **Guardrails and early‑abort checks:**
  - Pass `-b main` explicitly on init and set the identity per repository.
  - Do not edit any existing assertion. Existing tests may only gain new cases.

### Success & Discard Criteria

- **Success:** All new tests pass with
  `uv run pytest apps/sandbox-executor/tests/test_combined_base.py apps/sandbox-executor/tests/test_git.py -n auto --dist worksteal`,
  and each one fails if the Step 3 routing is reverted. Check this once by reasoning or by a local revert, and do not
  commit the revert.
- **Discard:** A test needs network or `gh`. Redesign it as a local fixture instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.4   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 15.33 |

### Step Metrics Rationale

`EV = 0.90 * 20.0 + 0.5 * 1.5 - 0.3 * 1.4 - 3.0 = 18.00 + 0.75 - 0.42 - 3.0 = 15.33`. The fixture patterns already exist
in the repository. Most of the cost is building the git topology carefully.

---

## Step 5: Targeted tests, then the full suite and lints once

- **Sub‑intent recommendation:** NO
- **Reasoning:** A verification gate. Repeated runs are not needed, because the intent does not measure timing.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Meet the fourth acceptance criterion: the tests, `uv run task lint` and `uv run task lint-docs` all
  pass.
- **Git branch:** the plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first, with
   `uv run pytest apps/sandbox-executor/tests/test_combined_base.py apps/sandbox-executor/tests/test_git.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_mounted_no_fetch.py apps/sandbox-executor/tests/test_review_target.py -n auto --dist worksteal`.
2. Run the full suite once with `uv run task test`.
3. Run `uv run task lint`, which covers ruff, the complexity limits, the flow module budgets and the repository hygiene
   tests, then run `uv run task lint-docs`.
4. Record the pass and fail counts and the pytest-reported time of each run in the execution notes. There is one run of
   each, so no median is required.
5. If formatting changes are needed, apply them only through the project's own formatter task, and only to files in the
   touch set.

### Dependencies & Criticality

- **Depends on:** Step 4
- **Is Bottleneck:** YES (an acceptance criterion)

### Safety & Constraint Considerations

- **Relevant rules:** test-run discipline from the planner prompt; the intent goals of no new dependencies and keeping
  exactly three ledgers (`git status` must show no new `*.jsonl` file).
- **Potential failure modes for this step:**
  - Unrelated pre-existing failures.
  - A budget-description sentence that trips `lint-docs` or the hygiene test.
- **Guardrails and early‑abort checks:**
  - If an unrelated failure also occurs on `main`, record it with output and do not "fix" it out of scope.
  - Hygiene: the new text must not refer to external directories or tasks (see commit 19a5a1f2).

### Success & Discard Criteria

- **Success:** All green. `uv.lock` and `pyproject.toml` are unchanged.
- **Discard:** More than 2 fix-and-rerun cycles on in-scope failures. Stop and report as a partial result.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 6.87  |

### Step Metrics Rationale

`EV = 0.88 * 10.0 + 0.5 * 0.5 - 0.3 * 0.6 - 2.0 = 8.80 + 0.25 - 0.18 - 2.0 = 6.87`. Running the full suite is the main
cost. The main risk to p_success is the module-budget and docs lints.

---

## Step 6: Execution notes and hand-off to human review

- **Sub‑intent recommendation:** NO
- **Reasoning:** Bookkeeping only. Merging stays with the human maintainer.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Record what changed, the residual risk (a forged integrated row) and any host-fetch gap found in Step
  1, and leave the PR for the human.
- **Git branch:** the plan's execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Write the execution notes file under `executions/`. It covers:
   - the root cause, including the first-parent trap;
   - the touch set and the budget raises with their line counts;
   - the test evidence from Step 5.
2. Append rows to the existing ledgers only, using the standard executor path. Do not create, rewrite or reorder any
   ledger line.
3. Do not merge anything. Stage 4 raises the PR, and the human maintainer merges.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `world/constraints.md` section 3 (ledger immutability); `docs/safety.md` "Human review is the
  final gate"; the Australian English requirement.
- **Potential failure modes for this step:** a ledger edit instead of an append.
- **Guardrails and early‑abort checks:** check that `git diff` on `holon-knowledge/ledger/` shows only appended lines.

### Success & Discard Criteria

- **Success:** The notes are written and the ledgers are only appended to.
- **Discard:** N/A (trivial).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.6   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 4.42  |

### Step Metrics Rationale

`EV = 0.97 * 5.0 + 0.5 * 0.5 - 0.3 * 0.6 - 0.5 = 4.85 + 0.25 - 0.18 - 0.5 = 4.42`. The step is routine.

---

### Strategy Rationale

- **Assessment:** The intent is valid, high-value and fixable within budget. Without the fix, no decomposed flow can
  reach Stage 4 review, which blocks the system's main decomposition path. It is reframed only to make two hidden
  requirements explicit:
  - the decomposed comparison must count content that arrived through merges, which `changed_paths`' first-parent filter
    excludes;
  - one call site in `review_target.py`, outside the scope list, must pass the target branch through.
- **Detection choice:** It relies on the integration ledger row, which `integration.py` writes, rather than on git
  topology heuristics. A plain execution and a combined one share the same topology (both descend from the plan branch),
  so topology alone cannot tell them apart. The ledger row is authoritative and is already read by `git_refs` helpers.
- **Base choice:** `merge-base(origin/<target>, exec)` equals the plan's base on the target. If the target was synced
  later, it is the synced point, which keeps synced target content from passing the guard. It works for nested
  decompositions, where the target is a parent plan branch.
- **Aggregation:**
  - `p_success`: the product of the bottleneck steps (3 and 5) with a small correlation uplift: `0.86 * 0.88 = 0.757`,
    raised to 0.82 because Steps 3 and 5 share failure causes (lint and budget), so the failures are not independent,
    and Step 4 fixes surface Step 3 defects within the same execution.
  - `entropy`: the sum of the steps, `0.3 + 0.8 + 2.6 + 1.4 + 0.6 + 0.6 = 6.3`.
  - `impact`: a qualitative 80. The impact of the steps adds up to the plan's purpose: it unblocks every decomposed
    flow's final PR.
  - `cost`: the sum of the steps, `1.0 + 1.5 + 4.0 + 3.0 + 2.0 + 0.5 = 12.0`.
  - `learning_value`: 4.0. It is the maximum of the steps and captures the first-parent trap and the mounted-fetch
    contract.
  - `EV = 0.82 * 80.0 + 0.5 * 4.0 - 0.3 * 6.3 - 12.0 = 65.60 + 2.00 - 1.89 - 12.0 = 53.71`.
- **Strong assumptions:**
  - Stage 4 always reaches the guard with the intent's `target_branch`. Confirmed in `loop.py` line ~200.
  - Integrated executions always carry the superseding row before Stage 4. Confirmed: `integrate_children` fails Stage 3
    if writing the row fails.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: only humans merge to `main`, and changes stay scoped to the branch.
  - `holon-config/world/constraints.md` section 3: the ledgers are append-only, and are only read in this change.
  - `holon-config/world/ruleset.md` section 2: typing and docstrings. Section 3: tests in `tests/`, with test changes
    declared (Step 4).
  - `docs/safety.md`: "Human review is the final gate", "Failure containment" and "Safety invariants".
  - Planner prompt: ruff complexity limits, flow module budgets, and no new entrypoint-to-flow imports.
- **Potential violations or edge cases:**
  - A forged `integrated` row on a plain execution switches the base.
  - The mounted review container lacks the target ref.
  - `git_refs.py` grows past its current budget.
  - `review_target.py` is outside the literal scope list.
- **Mitigations built into the plan:**
  - Strict row matching on the plan branch and non-empty children.
  - Fail closed with a named reason whenever a ref or merge base is missing.
  - New logic lives in a new module, with an explicit, justified budget raise.
  - The plain path is byte-for-byte unchanged and pinned by tests.
  - The one-line `review_target.py` touch is declared and justified.
  - No dependency or ledger-file changes.
- **Residual risk accepted (and why):** An executor that could forge an integrated row on a plain execution gains only
  the plan branch's own content in the count. That content is normally plans and bookkeeping, which is excluded anyway.
  The human maintainer still reviews and merges every PR.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 6.3
- **Budget Compliance:** The strategy fits within budget (6.3 is at most 15.0). No sub-intent list.

## Sub-intent list

- None
