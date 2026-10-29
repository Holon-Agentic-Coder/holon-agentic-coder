# git_flow.md

This document defines the **Git branching, rebasing, and merging discipline** for Holon.

---

## 1) Core Principles

### 1.1 Git as Universal State Machine

- Every intent gets its own branch
- All work happens in branches, never directly on `main`
- `main` is the canonical "promoted" state
- Branches are cheap; exploration is encouraged

### 1.2 Fractal Intent Hierarchy

- Intents can spawn sub-intents recursively; in practice each split is decided by one of the intent's plans, which sits
  between the intent and its sub-intents in the hierarchy
- Each sub-intent gets its own intent branch, cut from its parent's intent branch and targeting it. The branch nests
  under the node whose stage decided the split (sections 2.2-2.3 and 8), and its provenance records that node
- In practice a split is decided by a plan: the planner writes a `## Sub-intent list` in its plan, and the flow runs
  each item as a sub-intent nested under that plan. This is what the flow does and what every example in this document
  shows. The model leaves other stages open -- a split may also be decided at the intent, execution or review stage, as
  long as the hierarchy is honoured (section 2.2)
- Depth is capped: `holon flow` refuses to nest sub-intents more than `MAX_SUB_INTENT_DEPTH` levels below the root
  intent (see Stage 3 under the flow equivalence guarantee)

### 1.3 Human Review Boundary

- **Root intents** (top-level intents) require human review before merging to `main`: the maintainer's PR merge is the
  only human step, and no stage before it waits for a human
- **Sub-intents** merge only into their parent intent branch. In the current flow each child runs autonomously through
  the full pipeline and opens a PR into its parent intent branch, which the maintainer merges; automatic,
  evaluation-based sub-intent merging (sections 4.1, 5, 6, 7.4, 8, the sub-intent rows of section 12 and sections
  13.2-13.3) is the target model and is not yet automated
- This applies recursively: sub-sub-intents merge to sub-intents, which merge to root intents

### 1.4 Terminology

- **Root Intent**: Top-level intent that will eventually merge to `main` (requires human review)
- **Parent Intent**: Immediate parent of a sub-intent in the tree (may itself be a sub-intent)
- **Sub-Intent**: Any intent spawned from another intent, in practice by the plan that decided the split (can have its
  own sub-intents)

### 1.5 ID Generation

- Root intent, sub-intent, plan and execution IDs are generated using the current Unix timestamp.
- A sub-intent's branch nests under the node that decided its split (section 2.2), so its ID only has to be unique among
  that node's children.
- Example: `I-1771422315`, `P-1771422316`, `E-1771422317`

---

## 2) Branch Naming Convention

Any branch in the hierarchy that can have child branches (including root intents, nested sub-intents, and plan variants)
must finish with the `/_` suffix. This serves as a terminal leaf node for that specific directory level, preventing
branch/sub-branch naming collisions in Git and enabling the creation of a clean, filesystem-like hierarchy.

### 2.1 Root Intent Branch

```
I-{timestamp}-{slug}/_
```

Example:

```
I-1771890389-refactor-metrics/_
```

### 2.2 Sub-Intent Branch (Nested Under the Deciding Node)

A sub-intent's branch nests under the branch node whose stage decided the split, with no child marker in the name. An
intent can have several competing plans (section 2.4), so a branch nested only under the intent would not say which plan
spawned the sub-intent; nesting under the deciding node records it in the branch path itself.

| Split decided by         | Sub-intent branch                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------- |
| The intent stage         | `I-{timestamp}-{slug}/I-{timestamp}-{slug}/_`                                                           |
| A plan (in practice)     | `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/I-{timestamp}-{slug}/_`                             |
| An execution             | `I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/E-{timestamp}-{action-slug}/I-{timestamp}-{slug}/_` |
| A review of an execution | The reviewed execution's node, as for an execution (a review has no branch node of its own)             |

In practice, splits are decided by a plan: the planner writes a `## Sub-intent list` in its plan, the executor records
it, and the flow runs each item as a sub-intent nested under that plan. Example:

```
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
```

For illustration of the general rule only, the same sub-intent would be named as follows had the split been decided at
the intent stage or during execution:

```
I-1771890389-refactor-metrics/I-1771890392-improve-estimators/_
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/E-1771890391-init-project/I-1771890392-improve-estimators/_
```

The sub-intent's own plan and execution branches continue below its node:

```
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/_
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/E-1771890399-add-estimators/_
```

Whichever node decided the split, the sub-intent branch is cut from its parent intent branch (`I-.../_`) and merges back
into it (sections 3 and 4.1); the deciding node only fixes where the name sits. A sub-intent's flow checkpoint is keyed
on its intent branch, so a child and a grandchild sharing a slug never overwrite each other's.

**Provenance.** A sub-intent's `intents.jsonl` row and its plan header record the node that decided the split:

- `parent_intent_id`: the parent intent.
- `deciding_branch`: the deciding node's branch (`I-.../_`, `I-.../P-.../_` or `I-.../P-.../E-.../_`).
- `parent_plan_id`: the deciding plan, when a plan, or an execution or review of that plan, decided the split.
- `parent_execution_id`: the deciding execution, when an execution or a review of it decided the split.
- `target_branch`: the parent intent branch the sub-intent merges into.

The specific item of the deciding node's sub-intent list is identified by the sub-intent's own recorded goal and
description.

```json
{
  "intent_id": "I-1771890392-improve-estimators",
  "parent_intent_id": "I-1771890389-refactor-metrics",
  "deciding_branch": "I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/_",
  "parent_plan_id": "P-1771890390-claude-code-claude-sonnet-4.6",
  "target_branch": "I-1771890389-refactor-metrics/_"
}
```

**Promotion.** If an operator promotes a sub-intent to its own root intent, the root intent records its source plan
(`parent_plan_id`), so the lineage is not lost even though the new root no longer nests under that plan.

### 2.3 Sub-Sub-Intent Branch (Deeper Nesting)

Deeper nesting repeats the pattern. For plan-decided splits:

```
I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/I-{timestamp}-{slug}/_
```

Example:

```
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/I-1771890400-p-success/_
```

**Pattern:** Each level appends the deciding node and the sub-intent to the path, creating a filesystem-like hierarchy.

### 2.4 Plan Branch (Proposal)

Plan branches are created as siblings to the `/_` node within the same intent directory, using a timestamped identifier
to allow for multiple competing proposals.

```
I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/_
```

Example:

```
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/_
```

### 2.5 Execution Branch (Action)

Execution branches are branched off a specific plan, appending the action slug to the plan's path to maintain a strict
lineage from intent to proposal to implementation.

```
I-{timestamp}-{slug}/P-{timestamp}-{agent}-{model}/E-{timestamp}-{action-slug}/_
```

Example:

```
I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/E-1771890391-init-project/_
```

### 2.6 Hierarchy Summary

`/_` is the Intent Root (The "What") `/P-.../_` is the Plan (The "How") `/P-.../E-.../_` is the Execution (The "Do")

### 2.7 The 5-Stage Holon Flow Lifecycle

The evolution pipeline traverses five deterministic stages, representing a single continuous provenance lifecycle:

| Stage              | Name                  | CLI Command                            | Artifacts & Outputs                                                                       |
| :----------------- | :-------------------- | :------------------------------------- | :---------------------------------------------------------------------------------------- |
| **1. Intent**      | Intent Initialisation | `holon intent <intent.json>`           | Intent branch `I-.../_` + `holon-knowledge/ledger/intents.jsonl`                          |
| **2. Plan**        | Plan Generation       | `holon plan <intent_branch>`           | Plan branch `I-.../P-.../_` + `plans/P-*.md` + `plans.jsonl`                              |
| **3. Execute**     | Sandbox Execution     | `holon execute <plan_branch>`          | Execution branch `I-.../P-.../E-.../_` + `executions.jsonl`                               |
| **4. Review Loop** | PR Review Loop        | `holon review-loop <execution_branch>` | PR raised + sandboxed reviewer ensemble (unanimity) + micro-actions in `executions.jsonl` |
| **5. Calibrate**   | Calibration Analysis  | `holon calibrate <execution_branch>`   | `/calibrated` branch + `plans/P-*_calibration.md` (included in PR diff)                   |

#### Flow Equivalence Guarantee

`holon flow <intent.json>` is equivalent to running the five commands above by hand, in order. Stages 1-3 call the same
dispatch function as `holon intent`, `holon plan` and `holon execute` (`dispatch_role_agent` in `cli.py`), so they
launch the same containers with the resolved runner and model: `holon/agent-<runner>` for the planner and executor, and
`holon/orchestrator` for the intent creator (which does not run a model). Those containers author every Stage 1-3
branch, plan file and ledger row; the flow only fetches `origin`, adopts the branch each container pushed and validates
it, except that it appends a superseding failure row to an empty execution's branch (see the limits below). A branch is
adopted only if it was not on `origin` before the container was dispatched and its name carries the dispatched runner
and model; when no such branch or more than one appears, the stage fails instead of guessing, so neither clock skew
between host and container nor a concurrent run under the same parent branch can redirect the flow. Stages 4 and 5 run
the same review loop and calibration code as `holon review-loop` and `holon calibrate`. Because Stages 1-3 leave the
host checkout where `holon flow` started, Stages 4 and 5 each first check out the adopted execution branch (and fail if
it cannot, for example because local changes would be overwritten; nothing is discarded), so the review loop's ledger
rows and the Stage 5 calibration report and ledger reconciliation land on that branch even under `--skip-review` or a
resume that enters at Stage 5. The conflict probes of Stages 2-5 name the branch each stage works from (Stage 1 has none
yet), and the review records the execution branch's tip, freshly fetched from `origin`, as the reviewed commit, never
the operator's `HEAD`.

Stage 3 never halts for a human. The executor's outcome is judged against the plan's persisted entropy budget (see
[Turn and Stage Budgets](budgets.md#entropy-budget)):

- **Fits the budget:** the plan is executed as usual.
- **Explicit sub-intent list:** the executor records the parsed list on its `decomposed` summary row and writes no
  `intents.jsonl` rows. The flow then runs each item as a child intent, strictly one after another and never
  concurrently, through the full pipeline (intent, plan, execute, review, calibrate). Each child nests under the plan
  that listed it and records it as its provenance (section 2.2), targets its immediate parent intent branch
  (`target_branch`), never `main`, and stops at its own Stage 4 consensus halt. The first failing child fails the
  parent's Stage 3; when all succeed, the parent's own Stage 4 and Stage 5 are skipped with reason `decomposed`, because
  review and calibration ran per child. Nesting is bounded by `MAX_SUB_INTENT_DEPTH`, checked against the plan before
  the executor is dispatched when the plan can be read.

  **Current limitation:** decomposed children target the parent intent branch, and the flow stops there. Each child is
  cut from the parent intent branch as it stood when that child started, so a later child does not see an earlier
  child's unmerged changes; order dependent items accordingly. Aggregating the merged children and raising the parent
  intent branch's PR to its own target (for a root intent, `main`) are not yet automated. The `holon flow` summary lists
  each child's PR (or its execution branch when no PR was recorded), lists a child that itself decomposed as having no
  PR of its own with its own children nested below it, and names this pending parent-to-target step, which the
  maintainer performs after merging the child PRs. Those PR merges are the only human steps. Sections 4.1, 5, 6, 7.4, 8,
  the sub-intent rows of section 12 and sections 13.2-13.3 describe the target model of automatic, evaluation-based
  sub-intent merges, which is not yet implemented.

- **Over budget without a list:** the flow returns to Stage 2 automatically, asking for a smaller plan or an explicit
  sub-intent list (only a smaller plan when the intent is at the sub-intent depth limit), at most `MAX_REPLAN_ATTEMPTS`
  times; then Stage 3 fails with a message naming the entropy, the budget and the attempts. When the plan can be read,
  the flow refuses each over-budget plan before dispatching the executor, so those attempts create no execution branch
  and append no `executions.jsonl` row; each rejected plan keeps its `plans.jsonl` row, and the refusals are recorded
  only in the flow's stage results, log and checkpoint. When the plan cannot be read before dispatch, the executor
  refuses the plan itself and the flow reads the `over_budget` row it records on an execution branch (exit code 3). That
  row also covers a direct `holon execute` run, which exits with code 3 (so the refusal is not mistaken for success)
  only when that row is committed and then either pushed or kept local because pushing is disabled, and with code 1
  otherwise (see
  [Entropy Budget and Decomposition Evaluation](executor/execution_architecture_specification.md#5-entropy-budget-and-decomposition-evaluation)).

The guarantee has five deliberate limits:

- **No fabricated plans.** If no planner agent produced `plans/P-*.md` on a plan branch under the intent branch, Stage 2
  fails; the flow never writes a plan from a template.
- **Empty executions fail.** An execution branch with no changes outside `holon-knowledge/ledger/`, `plans/` and
  `executions/` relative to its plan branch (content merged in from the target branch does not count) is recorded as
  `status: "failed"` (`failure_reason: "empty_diff"`, no `test_pass_rate`), Stage 3 fails and no PR is created or
  updated. The same guard runs inside `holon execute` and in every non-dry `holon review-loop` run, whatever `--auto-pr`
  is, whether or not `gh` is installed and for every target (a PR number or URL is first resolved to its head SHA and
  base branch, and the head SHA is compared with the PR's own base branch, or with the intent's target branch when `gh`
  reports no base; a PR whose base cannot be resolved or fetched is `diff_unverified`); for an execution branch it first
  fetches the execution and plan branches and compares the origin copies the PR is opened from, and for any other branch
  it fetches both that branch and the base, so stale or unpushed local branches never decide it (a failed fetch, or a
  git command that times out, skips the PR as `diff_unverified`; only a repository without an `origin` remote compares
  its local branches). When the guard refuses the PR (`empty_diff` or `diff_unverified`), the review loop fails closed:
  it does not wait for CI, runs no review iteration and posts no consensus, so no evidence -- not even a Stage 3
  `test_pass_rate` of 1.0 or green CI on an existing PR -- can approve it. The loop returns `approved: false` with the
  refusal as its `error` (an `empty_diff` refusal appends no ledger rows; a `diff_unverified` refusal appends only a
  `failed` "halted without consensus" summary row naming it and the review action event, with no `ACT-REV`, `ACT-FIND`
  or `ACT-POST` rows), and Stage 4 fails with the refusal's own reason
  (`Autonomous review halted: Refusing to create or update a PR for ...`), never as a test failure or as unmeasured
  tests, so Stage 5 does not run. An empty diff has consensus status `rejected` and an unverifiable one `failed`. Dry
  runs never invoke the guard, dispatch no reviewer and are never approved: a clean simulation reports `not_measured`. A
  consensus whose post to the PR fails or raises is `post_failed` and not approved, and `holon review-loop` exits 1
  unless the ensemble verdict is `APPROVED` and the consensus is approved and posted.
- **Dispatch needs `origin`.** `--dry-run` validates the payload and names the branches without dispatching anything.
  `--skip-push` is rejected outside `--dry-run` -- on every run, including a `--from-stage` or `--checkpoint` resume
  from any stage -- before any stage runs, because the role containers clone from and push to `origin`, Stage 4 opens
  the PR from the `origin` execution branch and Stage 5 pushes the `/calibrated` branch.
- **Unmeasured tests are never a pass.** Stage 3 carries the executor container's recorded `test_pass_rate` from its
  `executions.jsonl` summary row; when the row records none, it is `null` ("not measured"). Stage 4 then reports
  `test_results` with `pass_rate: null`, `measured: false`, `passed: null`, and when no CI checks exist the review
  loop's `local_tests` fallback reports `NOT_MEASURED` and withholds consensus instead of passing. Before its first
  iteration the loop waits for CI checks to register on the PR (when `gh` is available; up to
  `HOLON_CI_REGISTRATION_TIMEOUT` seconds, default 180, `0` disables the wait), without consuming review iterations or
  writing ledger rows. If none register, the loop stops after that single iteration with consensus status `not_measured`
  (one iteration's review rows and one summary row, not rows for every iteration), Stage 4 fails with
  `test results not measured: no recorded Stage 3 test result and no CI checks`, and the pipeline halts before Stage 5
  calibration runs. Checks that registered but are still pending are re-probed every `CI_REGISTRATION_POLL_SECONDS` (15
  s) until they settle or the iteration's `review_iteration` budget runs out; checks still pending then count as a CI
  finding. `--dry-run` does not wait, and its simulated consensus reports `not_measured`.
- **Token reduction is auto-detected.** `holon flow` (like `holon review-loop`) resolves token reduction with
  `detect_holon_coherence`: unless `HOLON_TOKEN_REDUCE` opts out (`0`, `false`, `no`, `off`), it is enabled by
  `--token-reduce`, `--mitm-web`, a truthy `HOLON_TOKEN_REDUCE` or `HOLON_PASSIVE_MONITORING`, a reachable proxy port, a
  local holon-coherence or mitmproxy image, or a `holon-coherence` binary on `PATH`, and the result is passed to the
  Stage 1-3 containers. `holon intent`, `holon plan` and `holon execute` enable it only on the explicit opt-ins
  (`--token-reduce`, `--mitm-web`, `HOLON_TOKEN_REDUCE`, `HOLON_PASSIVE_MONITORING`), so on a host where detection
  succeeds the flow routes the role containers through the proxy and the manual commands do not. To launch identical
  containers either way, pass `--token-reduce` to the manual commands or set `HOLON_TOKEN_REDUCE=0` for both.

**Reviewer ensemble.** Every Stage 4 iteration is decided by real reviewers: after the mergeability and CI probes,
`run_review_loop()` runs `--ensemble-size` (default 3, on both `holon flow` and `holon review-loop`) independent,
mutually blind reviewer agent turns on the real diff between the base and the resolved head SHA
(`sandbox_executor/review_ensemble.py`). Approval needs every reviewer to approve with zero findings, plus a mergeable
PR and green CI; zero or failed reviewers, any finding (a single nit) and unknown CI never approve. There is no
automatic resolver: the first iteration that is not approved stops the stage, its `ACT-FIND` rows are recorded in
`executions.jsonl` and the command exits 1. The posted report contains only real reviewer data and is posted only when
at least one reviewer completed. Reviewer turns run only inside the sandbox. `holon review-loop` runs the loop in the
`review-loop` role container; `holon flow` Stage 4 runs the loop on the host, where it refuses with "Refusing to run
reviewer agent turns on the host" before any PR, iteration or post, so a flow on a host currently stops at Stage 4 and
the review is run with `holon review-loop <execution_branch>`. Routing Stage 4 reviewer turns into the role container
needs a result transport for the container's ledger rows and is follow-up work. See the
[sandbox-executor README](../apps/sandbox-executor/README.md#reviewer-ensemble).

#### Safety Halt & Merge Boundary (Bean 0034)

Merging to `main` is strictly reserved for the human maintainer. Upon successful consensus approval in Stage 4 and
calibration report inclusion in Stage 5, the automated flow halts and requests human review and merge. Autonomous
merging to `main` is strictly forbidden. This Stage 4 consensus stop is the only halt in the flow: no earlier stage
waits for a human, and merging the PR is the only human step.

---

## 3) Mandatory Rebase Discipline

### 3.1 Dual Rebase Rule

Every sub-intent **must rebase from its immediate parent**:

1. **Before execution** (to get latest parent state)
2. **After execution** (to ensure clean merge)

The immediate parent is the parent intent branch (`I-.../_`), not the node that decided the split (section 2.2).

**Building on siblings (target model).** A sub-intent that depends on earlier siblings starts from, or rebases onto, the
parent intent branch only after those siblings have merged into it, so no sub-intent is implemented blind to the work it
depends on. The before-execution rebase of the dual rebase rule is that step. In the current flow each child is cut from
the parent intent branch as it stood when that child started (see Stage 3 under the flow equivalence guarantee).

### 3.2 Rebase Commands

**Before execution:**

```bash
$ git checkout I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
$ git fetch origin
$ git rebase I-1771890389-refactor-metrics/_
```

**After execution:**

```bash
$ git fetch origin
$ git rebase I-1771890389-refactor-metrics/_
```

### 3.3 Conflict Handling

If rebase fails:

1. Log conflict to ledger
2. Spawn reactive intent to resolve conflict
3. If unresolvable automatically, escalate to human

---

## 4) Merge Rules

### 4.1 Sub-Intent → Parent Merge (Automatic)

> [!NOTE] Target model. In the current flow, each child's PR into the parent intent branch is merged by the maintainer
> (see Stage 3 under the flow equivalence guarantee).

**Trigger:** Sub-intent execution completes successfully

**Process:**

1. Evaluate all completed sub-intents under the same parent
2. Compute `merge_value` for each:
   ```
   merge_value = impact_actual - conflict_risk - entropy_actual - redundancy_penalty
   ```
3. Filter by success (`success_actual == 1`)
4. Sort by `merge_value` (descending)
5. Merge sequentially or in parallel (based on conflict detection)

**Example:**

```bash
# Sub-intent completes
$ git checkout I-1771890389-refactor-metrics/_
$ git merge I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
# Automatic, no human review
```

**Ledger event:**

```json
{
  "event_type": "git_merge_attempted",
  "payload": {
    "intent_id": "I-1771890392-improve-estimators",
    "from_branch": "I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_",
    "to_branch": "I-1771890389-refactor-metrics/_",
    "status": "success",
    "merge_type": "automatic_sub_intent",
    "human_review_required": false
  }
}
```

### 4.2 Root Intent → Main Merge (Human Review Required)

**Trigger:** All sub-intents merged, root intent ready for promotion

**Parent PR (target model):** once all sub-intents have merged into the parent intent branch, the parent intent's PR to
its own target is raised, reviewed (Stage 4) and calibrated (Stage 5) on the combined diff, stopping at the human merge
boundary. In the current flow this parent-to-target step is not yet automated (see Stage 3 under the flow equivalence
guarantee).

**Process:**

1. Generate review package:
   - Intent goal and constraints
   - Predicted vs actual metrics
   - Diff summary
   - Test results
   - Calibration errors
2. Request human review
3. Human approves or rejects
4. If approved, merge to `main`

**Example:**

```bash
# Human reviews
$ holon review approve I-1771890389-refactor-metrics

# System merges
$ git checkout main
$ git merge I-1771890389-refactor-metrics/_
$ git push origin main
```

**Ledger event:**

```json
{
  "event_type": "intent_promoted_to_main",
  "payload": {
    "intent_id": "I-1771890389-refactor-metrics",
    "merge_sha": "abc123",
    "reviewer": "human@example.com"
  }
}
```

### 4.3 Merge Constraints

**Forbidden:**

- Sub-intent merging directly to `main` (must go through root intent)
- Merging without prior rebase
- Merging failed intents (unless explicitly overridden by human)

**Allowed:**

- Discarding sub-intents (close branch without merging)
- Multiple sub-intents merging to same parent
- Recursive sub-intent trees up to `MAX_SUB_INTENT_DEPTH` levels below the root intent

---

## 5) Automatic Merge Evaluation

### 5.1 Merge Value Calculation

```python
def compute_merge_value(sub_intent):
    return (
        sub_intent.impact_actual
        - estimate_conflict_risk(sub_intent)
        - sub_intent.entropy_actual
        - compute_redundancy_penalty(sub_intent)
    )
```

### 5.2 Conflict Risk Estimation

```python
def estimate_conflict_risk(sub_intent):
    # Check file overlap with other pending sub-intents
    overlapping_files = find_overlapping_files(sub_intent)

    # Check if files are "hot" (frequently modified)
    hot_file_penalty = sum(is_hot_file(f) for f in sub_intent.files_modified)

    return len(overlapping_files) * 5.0 + hot_file_penalty * 2.0
```

### 5.3 Redundancy Detection

```python
def compute_redundancy_penalty(sub_intent):
    # Check if another sub-intent already solved the same problem
    siblings = get_sibling_intents(sub_intent)

    for sibling in siblings:
        if solves_same_problem(sub_intent, sibling):
            if sibling.merge_value > sub_intent.merge_value:
                return 1000.0  # High penalty, discard this one

    return 0.0
```

---

## 6) Merge Strategies

### 6.1 Sequential Merge (Safe Default)

```python
def sequential_merge(sub_intents, parent_branch):
    # Sort by merge_value descending
    sorted_subs = sorted(sub_intents, key=lambda s: s.merge_value, reverse=True)

    for sub in sorted_subs:
        rebase_from_parent(sub, parent_branch)
        if rebase_successful():
            merge_to_parent(sub, parent_branch)
            log_merge(sub, "automatic_sequential")
        else:
            handle_conflict(sub)
```

### 6.2 Parallel Merge (Optimised)

```python
def parallel_merge(sub_intents, parent_branch):
    # Cluster by file overlap
    clusters = cluster_by_file_overlap(sub_intents)

    for cluster in clusters:
        if cluster.no_internal_conflicts():
            # Merge all in parallel
            for sub in cluster:
                rebase_and_merge(sub, parent_branch)
        else:
            # Fall back to sequential within cluster
            sequential_merge(cluster, parent_branch)
```

---

## 7) Branch Lifecycle

### 7.1 Creation

```bash
# Root intent
$ git checkout main
$ git checkout -b I-1771890389-refactor-metrics/_

# Sub-intent: nested under the plan that decided the split, cut from the parent intent branch, which it targets
$ git checkout -b I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_ origin/I-1771890389-refactor-metrics/_
```

### 7.2 Execution

- Work happens in the branch
- Commits are made
- Tests run in sandbox
- Metrics measured

### 7.3 Rebase (Post-Execution)

```bash
$ git rebase I-1771890389-refactor-metrics/_
```

### 7.4 Merge (Automatic for Sub-Intents)

Target model. In the current flow the maintainer merges the sub-intent's PR into the parent intent branch.

```bash
# Target model: the sub-intent branch merges automatically
$ git checkout I-1771890389-refactor-metrics/_
$ git merge I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
```

### 7.5 Promotion (Human Review for Root Intents)

```bash
$ holon review approve I-1771890389-refactor-metrics
$ git checkout main
$ git merge I-1771890389-refactor-metrics/_
$ git push origin main
```

### 7.6 Cleanup

```bash
# Delete merged branches
$ git branch -d I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_
$ git branch -d I-1771890389-refactor-metrics/_
```

---

## 8) Example: Multi-Level Intent Tree

### Intent Hierarchy and Git Branch Structure

Intent Hierarchy (each split decided by a plan):

```
I-1771890389-refactor-metrics (root)
└── P-1771890390-claude-code-claude-sonnet-4.6 (plan, decided the split)
    ├── I-1771890392-improve-estimators (sub-intent)
    │   └── P-1771890398-claude-code-claude-opus-4.6 (plan, decided the split)
    │       ├── I-1771890400-p-success (sub-sub-intent)
    │       ├── I-1771890401-entropy (sub-sub-intent)
    │       └── I-1771890402-impact (sub-sub-intent)
    ├── I-1771890394-add-logging (sub-intent)
    └── I-1771890395-add-tests (sub-intent)
```

Git branch structure:

```
main
└── I-1771890389-refactor-metrics
    ├── _
    ├── P-1771890390-claude-code-claude-sonnet-4.6
    │   ├── _
    │   ├── I-1771890392-improve-estimators
    │   │   ├── _
    │   │   └── P-1771890398-claude-code-claude-opus-4.6
    │   │       ├── _
    │   │       ├── I-1771890400-p-success
    │   │       │   └── _
    │   │       ├── I-1771890401-entropy
    │   │       │   └── _
    │   │       └── I-1771890402-impact
    │   │           └── _
    │   ├── I-1771890394-add-logging
    │   │   └── _
    │   └── I-1771890395-add-tests
    │       └── _
    └── P-1771890397-gemini-cli-gemini-3.6-flash
        ├── _
        └── E-1771890403-init-project
            └── _
```

`P-1771890397-gemini-cli-gemini-3.6-flash` is a competing plan for the same root intent; the sub-intents sit under
`P-1771890390-claude-code-claude-sonnet-4.6`, the plan whose `## Sub-intent list` created them.

### Merge Flow

Sub-intents merge into their parent intent branch, never into the deciding plan branch.

1. **Depth 2 → Depth 1** (automatic):
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/I-1771890400-p-success/_`
     → `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_`
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/I-1771890401-entropy/_`
     → `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_`
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/I-1771890402-impact/_`
     → `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_`

2. **Depth 1 → Root** (automatic):
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890392-improve-estimators/_` →
     `I-1771890389-refactor-metrics/_`
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890394-add-logging/_` →
     `I-1771890389-refactor-metrics/_`
   - `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/I-1771890395-add-tests/_` →
     `I-1771890389-refactor-metrics/_`

3. **Root → Main** (human review):
   - `I-1771890389-refactor-metrics/_` → `main` (after human approval)

---

## 9) Conflict Resolution

### 9.1 Automatic Resolution (Attempted First)

```python
def auto_resolve_conflict(sub_intent, parent_branch):
    # Try simple strategies
    if conflict_is_whitespace_only():
        accept_parent_version()
        return True

    if conflict_is_import_order():
        sort_imports()
        return True

    if conflict_is_non_overlapping():
        accept_both_changes()
        return True

    return False  # Escalate
```

### 9.2 Reactive Intent (If Auto-Resolution Fails)

```python
def handle_conflict(sub_intent):
    spawn_intent(
        {
            "intent_id": f"{sub_intent.intent_id}-R001-resolve-conflict",
            "parent_intent_id": sub_intent.intent_id,
            "goal": f"Resolve rebase conflict in {sub_intent.conflict_files}",
            "intent_type": "reactive",
            "trigger": "rebase_conflict",
        }
    )
```

---

## 10) Ledger Events

All Git operations are logged to the ledger:

```json
{
  "event_type": "git_branch_created",
  "payload": {
    "branch": "I-1771890389-refactor-metrics/_"
  }
}
{
  "event_type": "git_rebase_completed",
  "payload": {
    "status": "success",
    "conflict_files": []
  }
}
{
  "event_type": "git_merge_attempted",
  "payload": {
    "status": "success",
    "merge_type": "automatic_sub_intent"
  }
}
{
  "event_type": "intent_promoted_to_main",
  "payload": {
    "merge_sha": "abc123"
  }
}
```

---

## 11) Discard Policy

### 11.1 When to Discard Sub-Intents

Sub-intents should be discarded (branch closed without merging) when:

1. **Execution failed** (`success_actual == 0`)
2. **Negative merge value** (`merge_value < 0`)
3. **Redundant with higher-value sibling** (another sub-intent solved the same problem better)
4. **Conflicts with higher-value sibling** (unresolvable conflict, lower-value intent discarded)
5. **Entropy budget exceeded** (sub-intent consumed too much entropy)

### 11.2 Discard Process

```bash
# Close branch and delete locally/remotely
$ git branch -D <branch-name>
$ git push origin --delete <branch-name>
```

### 11.3 Learning from Discarded Intents

Even discarded intents provide value:

- **Failure modes** extracted to KB
- **Calibration data** (predicted vs actual metrics)
- **Negative examples** (what not to do)

---

## 12) Summary Table

| Operation                 | Automatic? | Human Review? | Rebase Required?       |
| ------------------------- | ---------- | ------------- | ---------------------- |
| Sub-intent → Parent merge | ✓ Yes      | ✗ No          | ✓ Yes (before & after) |
| Root intent → Main merge  | ✗ No       | ✓ Yes         | ✓ Yes (before)         |
| Branch creation           | ✓ Yes      | ✗ No          | N/A                    |
| Conflict resolution       | Attempted  | If fails      | N/A                    |
| Sub-intent discard        | ✓ Yes      | ✗ No          | N/A                    |

**Key insight:** In the target model, human review is only required at the root intent level and all sub-intent merges
are automatic and evaluation-based. In the current flow, the maintainer also merges each child's PR into its parent
intent branch; those PR merges are the only human steps, and no stage before them waits for a human.

---

## 13) Edge Cases

Sections 13.2 and 13.3 describe the target model. In the current flow the children of a sub-intent list run one at a
time, and the first failing child fails the parent's Stage 3, so the remaining children do not run, nothing is merged
automatically and no stage waits for a human.

### 13.1 Root Intent with No Sub-Intents

If a root intent has no sub-intents (leaf intent):

- Execute directly in root intent branch
- Still requires human review before merging to `main`

### 13.2 All Sub-Intents Fail

If all sub-intents under a parent fail:

- Parent intent is marked as failed
- If parent is root intent, human review requested (may choose to retry or abandon)
- If parent is sub-intent, it is discarded automatically

### 13.3 Partial Sub-Intent Success

If some sub-intents succeed and others fail:

- Successful sub-intents merge automatically
- Failed sub-intents discarded
- Parent intent continues with merged work

### 13.4 Long-Running Root Intent

If a root intent remains open for extended period (e.g., weeks):

- Periodic rebase from `main` recommended
- System entropy monitoring triggers maintenance
- Human may choose to merge incrementally or wait for completion

---

## 14) Best Practices

### 14.1 Keep Sub-Intents Focused

- Each sub-intent should have a single, clear goal
- Avoid "mega sub-intents" that do too much
- Better to have more sub-intents with smaller scope

### 14.2 Monitor File Overlap

- Track which files are "hot" (frequently modified)
- Consider sequential execution for sub-intents touching hot files
- Use finer-grained decomposition to reduce overlap

### 14.3 Rebase Frequently

- Don't let branches diverge too far
- Rebase from parent regularly, not just before/after execution
- Reduces conflict resolution cost

### 14.4 Prune Aggressively

- Discard low-value sub-intents early
- Don't wait for all sub-intents to complete before merging high-value ones
- Keep branch count low to reduce system entropy

---

## Related Documents

- [`metrics.md`](metrics.md) — merge value calculation, entropy formulas
- [`examples.md`](examples.md) — concrete examples of merge flows
- [`ledger_schema.md`](ledger_schema.md) — event logging for Git operations
- [`architecture.md`](architecture.md) — overall system design
