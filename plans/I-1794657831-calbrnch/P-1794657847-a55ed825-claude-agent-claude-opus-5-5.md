# Plan for I-1794657831-calbrnch

- **Plan ID:** P-1794657847-a55ed825-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T12:04:08.766Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME
- **Reframed intent (if applicable):** The failing `git diff` is not in `flow/stages/calibrate.py`, `calibrate_sync.py`
  or `calibrate_rebuild.py`. It is `parse_actual_metrics` in
  `apps/sandbox-executor/src/sandbox_executor/calibration.py`, which `run_calibrate` reaches from Stage 5. That call
  runs `git diff --numstat {plan_ref}...{target_ref}`. When `resolve_git_ref` cannot resolve the plan branch,
  `generate_calibration` passes the raw, unqualified name through. Git prints exit 128 "ambiguous argument ...: unknown
  revision or path not in the working tree" for **any** revision it cannot resolve, so the slashes and the trailing `/_`
  are not the cause on their own. The reframed intent keeps every goal and acceptance criterion. It widens the touch set
  by one file the intent's `scope` misses (`calibration.py`) and one new helper module. It also adds a fallback so that
  an unresolvable plan branch degrades the patch-size metric (with a logged warning) instead of failing Stage 5. Adding
  `--` alone would change the error text but would not let Stage 5 commit the report.
- **Exploration stance:** balanced. Step 1 is a short exploratory reproduction that has to confirm which ref is missing
  in a nested sub-intent layout before the fix is chosen. The rest of the plan is exploitation of a well-understood git
  pattern.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §1 (Git Flow & Branch Constraints) and
  `docs/safety.md` "Git is the safety boundary" / Invariant 1. Stage 5 code creates, fast-forwards and pushes branches,
  and `calibrate_rebuild.py` force-pushes the flow's own /calibrated branch with a lease. Changing the ref arguments of
  those calls risks moving the wrong ref. Ledger immutability (§3) also applies, because Stage 5 appends
  `executions.jsonl` rows.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6 steps)
- **Justification:** The error class is well known, but which ref is missing on a real decomposed flow is still
  inferred. Possible causes: a child plan branch that the host never fetched, a remote-tracking ref missing after a
  failed fetch fallback, or a raw-name fallback in `generate_calibration`. One cheap, hermetic reproduction step removes
  that uncertainty before any production code changes.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 7.5   |
| impact_pred         | 85.0  |
| cost_pred           | 19.0  |
| learning_value_pred | 4.0   |
| ev_pred             | 44.5  |

### Strategy Rationale

The intent assumes the faulty call is in the three Stage 5 flow modules. Reading the code showed this:

- `flow/stages/calibrate.py` has no `git diff` at all.
- `calibrate_sync.py` and `calibrate_rebuild.py` already use mostly fully qualified refs (`refs/heads/...`,
  `refs/remotes/origin/...`) or SHAs.
- The only `diff` on the Stage 5 path is `calibration.py` `parse_actual_metrics` (around line 795). It raises
  `RuntimeError("Git diff failed between ... exit code 128")` and sends Stage 5 into its fallback-report path. That path
  commits and pushes nothing, which matches the reported symptom exactly.

How the failure happens:

1. `generate_calibration` derives the plan branch through `extract_branch_components`. It rebuilds
   `{intent_id}/{plan_id}/_` from the last `I-` and `P-` segments.
2. It calls `resolve_git_ref`. On failure it silently falls back to the raw name.
3. The raw name then reaches `git diff` with no `--`, so git reports it as an ambiguous ref-or-path.

A hermetic reproduction confirmed that a missing `.../P-.../_` ref gives exactly this exit 128 message.

The fix therefore has three parts:

- **Qualify:** pass `refs/heads/...`, `refs/remotes/origin/...` or a resolved SHA, never a bare branch name.
- **Separate:** end revision arguments with `--`.
- **Degrade:** when the plan ref cannot be resolved, log a warning and fall back to the execution head's first parent.
  Stage 5 must still write and commit the report, which is the first acceptance criterion.

The sweep then applies "qualify and separate" to the remaining Stage 5, calibrate-sync and calibrate-rebuild calls, plus
the matching `diff --numstat plan execution` in `flow/stages/review.py`. That last call is inside the intent's
`flow/stages/` scope and has the same weakness.

Aggregation:

- `p_success_pred` is a qualitative judgement anchored on the bottleneck, Step 3 (0.75). The steps are strongly
  correlated (same author, same git knowledge), so the naive product `0.85 * 0.90 * 0.75 * 0.85 * 0.85 * 0.90 = 0.37`
  would understate success.
- `entropy_pred` is the sum of step entropy: `1.0 + 1.5 + 2.0 + 1.5 + 1.0 + 0.5 = 7.5`.
- `impact_pred` (85) reflects the outcome-level benefit. Decomposed flows currently lose every calibration report, so
  the dual-EV feedback loop for them is blind.
- `cost_pred` is the sum of step costs: `3 + 3 + 4 + 3 + 4 + 2 = 19`.
- `learning_value_pred` is about the mean of the step values (4.0).
- Overall EV: `EV = 0.75 * 85.0 + 0.5 * 4.0 - 0.3 * 7.5 - 19.0 = 63.75 + 2.00 - 2.25 - 19.0 = 44.5`.

Strong assumptions:

- `calibration.py` (outside the listed scope) may be edited.
- `apps/sandbox-executor/flow_module_budgets.json` may be edited if a swept flow module grows. `calibrate_sync.py`
  (186), `calibrate_rebuild.py` (123), `stages/calibrate.py` (354) and `stages/review.py` (300) all sit exactly at their
  line budgets, so the plan aims for line-neutral edits and treats a budget raise as the fallback.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: prefix-based branches; never commit to `main` or to other intents' branches.
  - `holon-config/world/constraints.md` §3: append-only ledgers. The intent restricts this to exactly three ledgers:
    `intents.jsonl`, `plans.jsonl`, `executions.jsonl`.
  - `holon-config/world/ruleset.md` §1: `uv`, Python 3.13, no new dependencies.
  - `holon-config/world/ruleset.md` §2: typing and docstrings.
  - `holon-config/world/ruleset.md` §3: test changes declared in the plan (they are: Step 1 and Step 5).
  - `docs/safety.md` "Git is the safety boundary", "Human review is the final gate", Invariant 1 (Git discipline) and
    Invariant 6 (Immutability).
  - Repository ruff limits: `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, with no new `# noqa`.
  - `scripts/check_flow_module_budgets.py`, run by `uv run task lint`.
- **Potential violations or edge cases:**
  - Qualifying a ref in `git checkout <branch>` as `refs/heads/<branch>` would detach HEAD. The `HEAD` checks in
    `_commit_stage5_reconciliation` and `_commit_stage5_promoted_artifacts` would then skip the commits, silently losing
    the ledger rows.
  - Qualifying the lease or refspec in `_replace_remote_branch` incorrectly could force-push the wrong ref.
  - The fallback base (first parent) gives a different patch-size metric for decomposed parents. That could skew
    calibration deltas if it is not labelled.
  - Hermetic tests could touch the network or the real `origin`.
  - Line growth in at-budget flow modules fails `uv run task lint`.
- **Mitigations built into the plan:**
  - `checkout`, `branch` and `push` destination names keep short branch names. Only **revision** arguments are
    qualified, and a `--` is added after them where the git subcommand accepts it.
  - `_replace_remote_branch` is left untouched apart from a test asserting its argv. It already uses `--` and fully
    qualified `refs/heads/`.
  - The fallback records `exec_meta["diff_base"] = "first_parent_fallback"` and logs a warning naming the unresolved
    plan ref, so the calibration report shows the degraded measurement.
  - All tests use local bare repositories under `tmp_path`, following `apps/sandbox-executor/docs/hermetic_testing.md`
    and the existing `tests/worktree_fixtures.py` and `tests/hermetic_fixtures.py`.
  - A budget check runs after each source edit (`uv run python scripts/check_flow_module_budgets.py`).
- **Residual risk accepted (and why):** The real decomposed-flow failure log is not in the repository (a search of
  `holon-knowledge/kb`, `plans/` and `executions/` for "ambiguous argument" found nothing). The exact missing ref is
  therefore inferred. The defensive "qualify, separate and degrade" fix covers every missing-ref cause, so the residual
  risk is only a mislabelled cause in the PR description, which Step 1 records honestly.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 7.5
- **Budget Compliance:** The strategy fits within budget (`7.5 <= 15.0`). It runs as one plan with no sub-intent list.

## Plan Description & Strategy

Touch set (module granularity):

- NEW `apps/sandbox-executor/src/sandbox_executor/git_revisions.py`: a focused helper module of about 60 lines. It owns
  "turn a branch name into an unambiguous revision argument". It is not under `flow/`, so it adds no flow-budget entry.
  It holds:
  - `qualify_branch_revision(repo_dir, name) -> str | None`: the order is SHA, then `refs/heads/<name>`, then
    `refs/remotes/origin/<name>`, then None. It strips `origin/`, `refs/heads/` and `refs/remotes/origin/` prefixes
    first, so already-qualified input stays idempotent.
  - `revision_args(*revisions) -> list[str]`: returns the revisions followed by `--`.
- EDIT `apps/sandbox-executor/src/sandbox_executor/calibration.py`: change `parse_actual_metrics` (the diff, rev-parse,
  merge-base and rev-list calls) and the plan and execution ref resolution in `generate_calibration`. The function
  already has `# noqa: C901, PLR0912, PLR0915`, so the new branch logic goes into a small extracted helper
  (`_patch_diff_range`) instead of growing that function.
- EDIT `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_sync.py`: qualify `_divergence_message` rev-list and
  the `publish_calibration` ff-only merge source. Line-neutral.
- EDIT `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rebuild.py`: add `--` to the SHA-only `rev-list`, the
  `log -1` and `diff-tree` calls. Line-neutral.
- EDIT `apps/sandbox-executor/src/sandbox_executor/flow/stages/review.py`: qualify the `diff --numstat` plan and
  execution refs and add `--`. Line-neutral if possible.
- POSSIBLE EDIT `apps/sandbox-executor/flow_module_budgets.json`: only if a flow module unavoidably grows, with a
  sentence appended to the `description`.
- NEW TESTS:
  - `apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py`: end-to-end Stage 5 on a nested layout, plus
    `parse_actual_metrics`.
  - `apps/sandbox-executor/tests/flow/test_calibrate_sync_refs.py`: argv assertions for each swept call in
    calibrate-sync, calibrate-rebuild and review.
  - `apps/sandbox-executor/tests/flow/test_git_revisions.py`: the helper.

The executor works on one execution branch below the plan branch, commits per step, and opens the pull request into the
intent branch. Merging stays with the human maintainer.

---

## Step 1: Reproduce the exit 128 on a nested sub-intent branch layout (hermetic, failing first)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, cheap, test-only step whose value is diagnostic. Splitting it out would add pipeline overhead
  without reducing risk.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** On a nested decomposed layout (root intent `I-<ts>-root/_`, child intent
  `I-<ts>-root--1-child/_`, child plan `I-<ts>-root--1-child/P-<ts>-<hex>-a-m/_`, child execution
  `.../E-<ts>-<hex>-a-m/_`), Stage 5 reaches `parse_actual_metrics` with a plan ref that exists only as a remote-only
  ref without a tracking ref, or not at all. `git diff --numstat <raw>...<exec>` then exits 128 with "ambiguous
  argument". The trailing `/_` alone does not cause the failure.
- **Learning target:** Which of three cases reproduces the reported message, and which ones do not:
  - the plan ref is absent locally but present on origin;
  - the plan ref is absent everywhere;
  - the plan ref is present locally.
- **Maximum acceptable cost for this learning:** about 3 cost units (one test module, no production edits).

### Intent & Git Integration

- **Step Intent:** Pin the defect with a failing hermetic test before changing production code.
- **Git branch:**
  I-1794657831-calbrnch/P-1794657847-a55ed825-claude-agent-claude-opus-5-5/E-{ts}-{hex}-{agent}-{model}/\_ (shared
  execution branch, commit 1)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py`. Reuse the existing `tests/flow/conftest.py`,
   `tests/worktree_fixtures.py` and `tests/hermetic_fixtures.py` helpers for a hermetic repository: a local bare repo as
   `origin`, a clone as the target worktree, and the git identity set through environment variables.
2. Build the nested layout with plain git commands in the fixture:
   - a `main` commit;
   - the root intent branch, the child intent branch (`--1-child` naming, as produced by `fit_child_intent_branch` in
     `ref_names.py`), the child plan branch and the child execution branch, each with one commit;
   - a minimal `holon-knowledge/ledger/executions.jsonl` summary row and a plan markdown, so that
     `parse_predicted_metrics` and `parse_actual_metrics` have data.
3. Parametrise three cases:
   - (a) the plan branch only on origin, with the local branch and tracking ref deleted;
   - (b) the plan branch deleted everywhere;
   - (c) the plan branch present locally, as a control.
4. Call `sandbox_executor.calibration.generate_calibration(execution_branch, target_worktree=...)` directly. Before the
   fix, assert that cases (a) and (b) raise `RuntimeError` containing "exit code 128" and "ambiguous argument". Mark
   them `xfail(strict=True)` while still red so the commit stays green, and remove the marker in Step 3. Assert that (c)
   succeeds.
5. Monkeypatch `fetch_remote_ref_if_needed` (or point `origin` at the local bare repo) so that no network access is
   possible. The network guard in `tests/network_guard.py` must stay active.
6. Record which cases reproduced the message in the commit message body (Australian English).

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes declared here);
  `apps/sandbox-executor/docs/hermetic_testing.md`; `docs/safety.md` Invariant 2 (Sandboxing).
- **Potential failure modes for this step:**
  - The fixture accidentally uses the developer's global git config or the real remote.
  - The reproduction does not fail because `resolve_git_ref`'s fetch fallback succeeds against the local origin in case
    (a).
- **Guardrails and early‑abort checks:**
  - If case (a) resolves through the fetch fallback, keep it as a passing control and rely on case (b) as the red case.
    Note in the commit that the reported failure needs an absent or unfetchable plan ref.
  - Abort if no case reproduces exit 128 after 2 fixture variants, and escalate with findings instead of guessing.

### Success & Discard Criteria

- **Success:** At least one parametrised case reproduces exit 128 "ambiguous argument" through `generate_calibration` on
  a nested branch layout, hermetically, via
  `uv run pytest apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py`.
- **Discard:** Cost exceeds 1.5x `cost_pred`, or no variant reproduces the failure.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 16.7  |

### Step Metrics Rationale

A manual hermetic probe during planning already showed git's exit 128 message for a missing `.../P-.../_` revision, so
reproduction is likely (0.85). Entropy is low because the step is test-only. Learning value is the highest in the plan
because it decides whether the plan ref is missing or merely unfetched.
`EV = 0.85 * 20.0 + 0.5 * 6.0 - 0.3 * 1.0 - 3.0 = 17.0 + 3.0 - 0.3 - 3.0 = 16.7`.

---

## Step 2: Add a focused revision-qualification helper module

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, pure and reusable helper. It is low risk and is fully covered by its own tests in Step 5.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one owner for "branch name to unambiguous revision argument", so that every fixed call shares
  the same rule.
- **Git branch:** same execution branch, commit 2
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/git_revisions.py` with a module docstring. The docstring explains
   that nested intent branches (`.../_`) must reach git as fully qualified refs followed by `--`, so git never reads a
   missing ref as a path.
2. `qualify_branch_revision(repo_dir: str, name: str, remote: str = "origin") -> str | None`:
   - Strip whitespace and the `refs/heads/`, `refs/remotes/<remote>/` and `<remote>/` prefixes.
   - Return a 7–40 hex input unchanged when `rev-parse --verify --quiet <sha>^{commit}` succeeds.
   - Otherwise return `refs/heads/<name>` when that ref verifies, then `refs/remotes/<remote>/<name>`, else None.
   - Use `sandbox_executor.git.run_git` with `check=False`. Never raise for an unknown ref.
3. `revision_args(*revisions: str) -> list[str]`: returns `[*revisions, "--"]`. Document that callers add pathspecs
   after it.
4. Reuse the string constant `DEFAULT_REMOTE` only if importing it does not create an import from `flow/` into a
   top-level module. Otherwise default to the literal `"origin"`, as `calibration.py` already does.
5. Add type annotations and docstrings that cover inputs, outputs and side-effects (`holon-config/world/ruleset.md` §2).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2; the code placement rule ("new behaviour in a focused module");
  "no imports from entrypoints into the host flow engine".
- **Potential failure modes for this step:**
  - Prefix stripping mangles a branch whose bare name starts with `origin/` (not possible for `I-` branches, but
    document it).
  - A circular import with `calibration.py`.
- **Guardrails and early‑abort checks:** The module imports only `re` and `sandbox_executor.git`. Run `uv run task lint`
  after creation.

### Success & Discard Criteria

- **Success:** The module exists, lint is clean, and the helper returns the qualified ref for local-only, remote-only
  and SHA inputs, and None for a missing ref (proved in Step 5).
- **Discard:** The helper would need more than about 80 lines, or it would duplicate `resolve_git_ref`'s fetch behaviour
  (it must not fetch).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 25.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 20.05 |

### Step Metrics Rationale

This is a simple, well-understood git plumbing helper. Entropy of 1.5 reflects one new module in the package.
`EV = 0.90 * 25.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 22.5 + 1.0 - 0.45 - 3.0 = 20.05`.

---

## Step 3: Fix the Stage 5 patch-size diff in calibration.py (qualify, separate, degrade)

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the bottleneck, but it is a single function family in one module. A sub-intent would add a full
  pipeline round trip without reducing the risk, which is covered by the Step 1 red tests turning green.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Three changes together let Stage 5 commit the report on every nested layout from Step 1
  without changing results on the existing layouts that `tests/test_calibration.py` covers:
  - qualified revisions for the plan and execution refs;
  - a `--` separator;
  - a logged first-parent fallback when the plan ref is unresolvable.
- **Learning target:** Whether any existing calibration test relies on the raw-name fallback or on the exact
  `RuntimeError` text.
- **Maximum acceptable cost for this learning:** about 4 cost units. If more than 3 existing tests need assertion
  changes, stop and reassess.

### Intent & Git Integration

- **Step Intent:** Make `parse_actual_metrics` robust to nested `/_` branch names and unresolvable plan refs, so Stage 5
  produces and commits the calibration report.
- **Git branch:** same execution branch, commit 3
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `generate_calibration`, keep the `resolve_git_ref` calls, because they may fetch. If plan resolution fails, pass
   an empty plan ref rather than the raw name, and log a warning: "plan branch '<name>' could not be resolved; patch
   size measured against the execution head's first parent".
2. Extract a helper `_patch_diff_range(base_ref, target_ref, target_worktree) -> tuple[list[str], str]` in
   `calibration.py`, so the already `noqa`-annotated `parse_actual_metrics` does not grow in complexity. It:
   - qualifies both refs with `git_revisions.qualify_branch_revision`;
   - when both qualify and differ, returns `["diff", "--numstat", f"{base}...{target}", "--"]` and the label `"plan"`;
   - when the base is empty or unqualifiable, returns `["diff", "--numstat", f"{target}~1..{target}", "--"]` and the
     label `"first_parent_fallback"`;
   - when the target itself is unqualifiable, raises the existing `RuntimeError`. Calibration of an unknown head must
     still fail loudly, which keeps current behaviour.
3. Apply the same qualified revisions plus `--` to the `rev-parse`, `merge-base` and `rev-list --count --merges` calls
   in the same block:
   - `rev-parse --verify <rev>^{commit}`;
   - `merge-base` takes no `--`, but qualified refs make it unambiguous;
   - `rev-list ... <range> --`. Skip these calls when the label is `first_parent_fallback`.
4. Record `exec_meta["diff_base"]` with the label, so the report states how the patch size was measured.
5. In `run_calibrate`, change `merge --ff-only origin/<calibrated>` to
   `merge --ff-only refs/remotes/origin/<calibrated>`. Leave `checkout <calibrated>` and
   `checkout -b <calibrated> <start>` with the short branch name, because a qualified name would detach HEAD. Qualify
   the start point instead (`refs/remotes/origin/<calibrated>`, or the resolved SHA).
6. Remove the `xfail` markers from Step 1 and assert that `generate_calibration` returns a report with
   `exec_meta["diff_base"]` equal to `"plan"` (local or remote-only plan ref) or `"first_parent_fallback"` (plan ref
   absent).
7. Run
   `uv run pytest apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_calibration_dual_ev.py`.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (p_success_pred 0.75, above 0.6, so a sub-intent is not mandated)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` Invariant 1 (Git discipline); `holon-config/world/constraints.md` §1 and §3; ruff
  complexity limits with no new `# noqa`.
- **Potential failure modes for this step:**
  - A detached HEAD from a qualified checkout. Prevented by step 5 of the details above, and caught by the
    `_commit_stage5_*` HEAD checks in tests.
  - The fallback hides a real bug (for example the plan branch was wrongly derived). Mitigated by the warning plus the
    `diff_base` label.
  - Existing tests that mock `run_git` by exact argv break.
- **Guardrails and early‑abort checks:**
  - Grep `tests/` for the old argv shapes (`"--numstat", f"`) before editing, and update only mock argv expectations,
    never behavioural assertions.
  - Abort and reassess if more than 3 existing tests need changes.

### Success & Discard Criteria

- **Success:**
  - The Step 1 cases (a) and (b) pass without `xfail`.
  - The existing calibration tests pass.
  - `uv run task lint` is clean, with no new `noqa`.
- **Discard:** The fallback cannot be added without raising `parse_actual_metrics` complexity past its existing
  suppressions, or the existing calibration semantics change for non-nested layouts.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.0   |
| impact_pred         | 35.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 23.15 |

### Step Metrics Rationale

This step carries most of the risk. `calibration.py` is 1678 lines with heavy mock-based tests, so argv changes can
ripple. The fix logic itself is straightforward.
`EV = 0.75 * 35.0 + 0.5 * 3.0 - 0.3 * 2.0 - 4.0 = 26.25 + 1.5 - 0.6 - 4.0 = 23.15`.

---

## Step 4: Sweep the Stage 5, calibrate-sync, calibrate-rebuild and Stage 4 stat git calls

- **Sub‑intent recommendation:** NO
- **Reasoning:** These are mechanical, line-neutral argv edits in at-budget modules. They are low risk if `checkout` and
  `branch` names are left alone.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Satisfy "every fixed git call uses fully qualified refs and a path separator" across the swept
  modules.
- **Git branch:** same execution branch, commit 4
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow/calibrate_sync.py`:
   - `_divergence_message`: change the rev-list range to `refs/remotes/origin/<b>..refs/heads/<b>` followed by `--`.
   - `publish_calibration`: change the ff-only merge source to `refs/heads/<calibrated>`. `checkout <branch>` keeps the
     short name, so HEAD stays attached for the later `push origin <branch>`.
   - `_rev` already uses qualified refs or SHAs with `^{commit}`, so no change is needed. Record it as audited.
   - `push` refspecs use short names as destinations, which is safe. Record them as audited.
2. `flow/calibrate_rebuild.py`:
   - Add `--` to `rev-list <start>..<existing>`, `rev-list --parents -n 1 <sha>`, `log -1 --format=%s <sha>` and
     `diff-tree ... <sha>` (diff-tree accepts `--` before paths).
   - `merge-base --is-ancestor` takes SHAs, so leave it.
   - `_replace_remote_branch` is already qualified with `--`, so it is audited only.
3. `flow/stages/calibrate.py`: its git calls are `rev-parse --abbrev-ref HEAD`, `add` and `commit -- <paths>` and
   `status -- <paths>`. They already separate paths and take no branch revisions, so record them as audited with no
   change.
4. `flow/stages/review.py` (Stage 4 diff summary, same pattern, inside `flow/stages/` scope): qualify `plan_branch` and
   `execution_branch` through `git_revisions.qualify_branch_revision` and add `--`. When either side is unqualifiable,
   skip the stat and keep the zero summary instead of parsing an error.
5. Keep every edit line-neutral by folding the argv onto existing lines. Run
   `uv run python scripts/check_flow_module_budgets.py` (or `uv run task lint`). If a module unavoidably grows, raise
   that module's budget in `apps/sandbox-executor/flow_module_budgets.json` by the exact line count and append one
   sentence to its `description`, following the file's convention.
6. Write the audit table (call, file, before, after or "already safe") into the execution report so a reviewer can check
   the sweep.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Git is the safety boundary"; `calibrate_rebuild.py`'s lease contract (the only
  force is the flow's own /calibrated branch); the module budget lint.
- **Potential failure modes for this step:**
  - `merge --ff-only refs/heads/<calibrated>` changes the merge message. This does not matter for a fast-forward.
  - `review.py` gains an import line, which pushes it over its 300-line budget.
- **Guardrails and early‑abort checks:**
  - Do not touch `push` or `branch -f` arguments.
  - Rerun `tests/test_calibrate_sync.py`, `tests/test_calibrate_rebuild.py` and `tests/flow/stages/test_review.py` after
    each file.

### Success & Discard Criteria

- **Success:** Every swept call that takes a revision uses a qualified ref or SHA and a `--` separator where git accepts
  one. The existing sync, rebuild and review tests pass, and the budget lint is clean.
- **Discard:** A swept change alters ref movement semantics (any existing sync or rebuild test changes its asserted
  outcome).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 10.3  |

### Step Metrics Rationale

This is mechanical work, but it touches ref-moving code in four modules that are at their budgets.
`EV = 0.85 * 15.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 12.75 + 1.0 - 0.45 - 3.0 = 10.3`.

---

## Step 5: Hermetic tests for each fixed call and an end-to-end Stage 5 on the nested layout

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests belong with the change, and they are what the acceptance criteria are judged against.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove each fix individually, and prove the first acceptance criterion: Stage 5 completes on a nested
  layout and commits the report.
- **Git branch:** same execution branch, commit 5
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/tests/flow/test_git_revisions.py`: real-git hermetic tests for `qualify_branch_revision`,
   covering:
   - a local-only nested `.../_` branch, which returns `refs/heads/...`;
   - a remote-only branch, which returns `refs/remotes/origin/...`;
   - an already-qualified input, which is returned idempotently;
   - a SHA;
   - a missing branch, which returns None;
   - a branch name that also exists as a file path in the worktree, which still resolves as a ref.

   Also cover `revision_args`, which ends with `--`.

2. Extend `apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py` with an end-to-end test. It builds a
   `FlowContext` for the nested child execution branch with `skip_push=True`, using the existing
   `tests/flow/stages/conftest.py` patterns for the CI and gh fakes, and runs `run_calibrate_stage`. It asserts:
   - `StageStatus.SUCCESS`;
   - `payload["calibrated_branch"]` ends with `/calibrated`;
   - `git log -1 --format=%s refs/heads/<exec>` shows the `chore(calibration):` report commit, fast-forwarded;
   - `plans/<P-id>_calibration.md` exists in that commit;
   - the worktree is restored to the starting branch.
3. `apps/sandbox-executor/tests/flow/test_calibrate_sync_refs.py`:
   - Recording-runner tests (patch `run_git` in the module under test, as the existing tests do) assert the exact argv
     of each swept call: `_divergence_message`, the `publish_calibration` merge, the `calibrate_rebuild` rev-list, log
     and diff-tree calls, and the `stages/review.py` numstat. Each must hold only qualified refs or SHAs and must
     contain `--`.
   - One real-git test runs `_divergence_message` on a nested `/_` branch whose short name collides with a worktree
     path, and asserts the count is numeric rather than "some".
4. Add a `parse_actual_metrics` unit test in the same nested-refs module that asserts the diff argv ends with `--`, and
   that `exec_meta["diff_base"]` is recorded for both labels.
5. All tests are hermetic: `tmp_path` repositories, a local bare origin, git identity through `GIT_*` environment
   variables, and no network (the network guard stays enabled). No `time.sleep`; use the existing `sleep_patch`.
6. Write the docstrings and comments in Australian English (for example "normalise", "behaviour").

### Dependencies & Criticality

- **Depends on:** Step 3, Step 4
- **Is Bottleneck:** NO (but it is required for acceptance)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3; `apps/sandbox-executor/docs/hermetic_testing.md`;
  `tests/test_sandbox_hermetic_guard.py` and `tests/test_credential_hermeticity.py` conventions.
- **Potential failure modes for this step:**
  - The end-to-end Stage 5 test pulls in Prettier (`format_calibration_report`), which may not be available
    hermetically.
  - The CI final-head check tries `gh`.
- **Guardrails and early‑abort checks:**
  - Reuse the existing Stage 5 test fakes for Prettier, `check_final_head_ci` and `reverify_and_report`, as
    `tests/flow/stages/test_calibrate.py` already does. Mirror them; do not invent new global patches.
  - Check that `tests/flow/test_patch_targets.py` and `tests/flow/test_sleep_patch_guard.py` still pass.

### Success & Discard Criteria

- **Success:** All new tests pass via
  `uv run pytest apps/sandbox-executor/tests/flow/test_git_revisions.py apps/sandbox-executor/tests/flow/test_calibrate_nested_refs.py apps/sandbox-executor/tests/flow/test_calibrate_sync_refs.py`,
  and every fixed call has at least one asserting test.
- **Discard:** The end-to-end test needs network or a real `gh`. In that case, fall back to calling
  `_calibrate_execution_branch` with the existing fakes and document why.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 14.2  |

### Step Metrics Rationale

The test infrastructure for Stage 5 already exists, so most of the effort is fixture wiring for the nested layout.
`EV = 0.85 * 20.0 + 0.5 * 3.0 - 0.3 * 1.0 - 4.0 = 17.0 + 1.5 - 0.3 - 4.0 = 14.2`.

---

## Step 6: Full verification: affected tests, full suite once, lint and lint-docs

- **Sub‑intent recommendation:** NO
- **Reasoning:** A standard gate with no design decisions.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Meet the third acceptance criterion and confirm that ledger and budget invariants hold.
- **Git branch:** same execution branch (no new commit unless fixes are needed)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the affected tests first with `uv run pytest`:
   - `apps/sandbox-executor/tests/flow/`;
   - `apps/sandbox-executor/tests/test_calibration.py`;
   - `apps/sandbox-executor/tests/test_calibration_dual_ev.py`;
   - `apps/sandbox-executor/tests/test_calibrate_sync.py`;
   - `apps/sandbox-executor/tests/test_calibrate_rebuild.py`;
   - `apps/sandbox-executor/tests/test_calibrate_final_head_ci.py`.
2. Run the full suite once with `uv run task test`. The intent does not concern timing, so no repeated runs.
3. Run `uv run task lint`, which includes ruff complexity limits and the flow module budgets, and
   `uv run task lint-docs`.
4. Confirm that `git diff --stat main...HEAD -- holon-knowledge/ledger/` shows no edits to historical lines, and that
   the ledger directory still holds exactly `intents.jsonl`, `plans.jsonl` and `executions.jsonl`.
5. Confirm that `uv.lock` and both `pyproject.toml` files are unchanged (no new dependencies).
6. Report the pass and fail counts and pytest-reported times in the execution report. Leave merging to the human
   maintainer.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` "Human review is the final gate" and Invariant 6; the intent's goal 4.
- **Potential failure modes for this step:**
  - Unrelated flaky tests in the full suite.
  - `lint-docs` flags the new module docstrings or the plan markdown.
- **Guardrails and early‑abort checks:** If a failure is unrelated to the touch set, record it with output and do not
  "fix" unrelated code.

### Success & Discard Criteria

- **Success:** All tests pass, and lint and lint-docs are clean. Ledgers and dependencies are unchanged apart from
  appends made by the flow itself.
- **Discard:** Persistent failures traced to Step 3 semantics. Go back to Step 3 rather than weakening the tests.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.35  |

### Step Metrics Rationale

This is a routine gate. Its main risk is the at-budget module lint, which Step 4 already guards.
`EV = 0.90 * 10.0 + 0.5 * 1.0 - 0.3 * 0.5 - 2.0 = 9.0 + 0.5 - 0.15 - 2.0 = 7.35`.

---

## Sub-intent list

None
