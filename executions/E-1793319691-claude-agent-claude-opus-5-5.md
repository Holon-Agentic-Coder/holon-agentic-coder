# Execution Record: E-1793319691-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793319521-claude-agent-claude-opus-5-5` for intent
`I-1793319507-remaining-docs-follow-sub-intent-merge-rulings`: align the remaining docs with the maintainer's sub-intent
rulings (deciding-node merge target and base, no human stop below the root, distinct sibling slugs, `deciding_branch`
provenance, review-decided splits, plan decides in practice, current-limitation note). Done: five target docs updated,
sweep fixed one more passage in `docs/planning_and_evaluation.md`, all changes under `docs/`; `uv` was not available, so
tests could not be run (see Tests Run).

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 2.6   |
| impact_actual         | 55.0  |
| cost_actual           | 6.0   |
| learning_value_actual | 2.0   |

## Steps Executed

### Step 1: Pin the reference wording and the exclusion set (done)

- Re-read `docs/git_flow.md` sections 1.2-1.5, 2.2, 3.1 ("Building on siblings"), 4.1, 4.2 and the Stage 3 "Current
  limitation" note. Canonical cross-link form: `[Git Flow](git_flow.md) Stage 3 under the flow equivalence guarantee`
  (as used in `docs/core_concepts.md` and `docs/examples.md`).
- Excluded files: `README.md`, `docs/git_flow.md`, `docs/examples.md`, `docs/core_concepts.md`, `docs/ledger_schema.md`.
  Stage 5 calibration-formatting text is off-limits in every file.

### Step 2: `docs/agent_design.md` Human Review Boundary (done)

- Rewrote the second half of the **Human Review Boundary** bullet with R1 and R2 and added the R7 pointer to
  `git_flow.md` Stage 3. First sentence and line 116 left unchanged.

### Step 3: `docs/architecture.md` merge and review-boundary sections (done)

- Section 4 heading renamed to "deciding-node-only merges"; bullets now state R1 and R2 with an R7 pointer. The "No
  direct merges from sub-intents to `main`" bullet kept word for word.
- Human Review Boundary component: "only the root intent's PR to `main`".
- Branch topology principle rewritten as "merges flow up to the deciding node" with R1 (`target_branch` equals
  `deciding_branch`), R6, R5, R3, root-only merge to `main` and an R7 pointer; review list item now names root intent
  completion as the only human stop.
- Medium-trust bullet: "deciding-node-only merge rules". "parent intent policy" and "parent intent constraints" left
  (trust policy, not merge targets). The mermaid diagram has no merge labels, so it was skipped.

### Step 4: `docs/faq.md` rebase and merge references (done)

- Q6: state is reconciled "before merging into the deciding node's branch (in practice the plan branch; the root intent
  alone merges to `main`, after human review)".
- Active Rebase Pulses: rebase "onto the branch it was cut from (for a sub-intent, its deciding plan or execution
  branch)". No other FAQ entry covers human review of child PRs or sibling ids.

### Step 5: `docs/safety.md` review boundaries and invariants (done)

- Principle 5: "Only **root intents** (direct children of `main`)", plus automatic below-root merging (R2) and an R7
  pointer. Principle 1 line ("Only **human-approved intents** merge to `main`") already correct, left.
- Automatic human review trigger 1 renamed "Root intent merge to `main`"; R1, R2 and R7 bullets.
- Human review workflow limited to the root intent (lead-in sentence for R2, step 2, "Git diff (from `main`)", approve
  line). Code fence intact.
- Rebase-conflict recovery now rebases onto the deciding node's branch after earlier siblings merged into it.
- Invariant 1: deciding-node rebase and merge, R3 bullet, root-only merge to `main`. Invariant 4: "Root intents ... (the
  only human stop in the flow)".
- Diff checked: every governance, estimator, routing and core-invariant human-approval line is unchanged. Budget
  inheritance "parent" lines and the review-package `"parent_branch": "main"` sample (correct for a root) left.

### Step 6: `docs/planner/planning_architecture_specification.md` decomposition bullets (done)

- §3 bottleneck bullet: the child is "nested under, cut from and merged automatically into the plan branch that listed
  it" (R1, R2).
- §4 sub-intent list bullet: child is cut from and targets the deciding plan branch (its `deciding_branch`, R4), R6
  half-sentence, R7 pointer via `../git_flow.md`. No other merge-target or child-PR review text in §4.
- `holon-config/prompts/planner.template.md` deliberately not touched (out of scope).

### Step 7: Sweep of `docs/` (done)

Patterns searched (case-insensitive, `docs/agent_selection/logs/` skipped): "parent intent branch", "parent branch",
"parent-only", "merge(s) into (its/their/the) parent", "parent intent → ", "parent intents (direct", "open question",
"maintainer merges", "nested parent", "collid", plus a second pass for "parent" near merge/rebase/human and child or
sub-intent PRs near maintainer/human review.

Classification:

- (c) fixed: `docs/planning_and_evaluation.md` agent duties bullet, now "Rebase from the branch they were cut from (for
  a sub-intent, its deciding node's branch, in practice the plan branch)". Worded this way so it stays correct for root
  intents and executions too.
- (a) correct R7 notes, kept: the new notes in `agent_design.md`, `architecture.md`, `safety.md` and the planner spec;
  `git_flow.md` Stage 3 and 3.1; `core_concepts.md` 91; `ledger_schema.md` 134 and 731; `docs/metrics.md` 606 (target
  model test with a current-flow note).
- (b) generic or correct, kept: `docs/knowledgebase_schema.md` 224 and `docs/wisdombase_schema.md` 97 (sample generic
  rebase skill / invariant strings); `git_flow.md` 222 (concurrent runs under one parent branch), and the `git_flow.md`,
  `examples.md`, `core_concepts.md`, `ledger_schema.md` "nested parent" and "collide" hits, which already state the
  rulings; `metrics-explained.md` 37 and `audit-report-2026-10.md` 57 (unrelated "collide").
- No old-rule statement found in the five excluded files, so none was edited. No calibration-formatting text touched.
- Final search: only (a) and (b) hits remain.

### Step 8: Format, lint and verify (done)

- `npx prettier --write` run on each touched file.
- `git diff --name-only d7cc2fa`: only the six `docs/` files plus this record; no code, `holon-config/`, `apps/` or
  ledger changes (`holon-knowledge/ledger/` still holds exactly `executions.jsonl`, `intents.jsonl`, `plans.jsonl`).
- Added lines checked for American spellings (none) and for harness references (none).
- Relative links resolve: `git_flow.md` from `docs/`, `../git_flow.md` from `docs/planner/`.

## Tests Run

- `uv run task lint-docs`: could not run, `uv: command not found` in this sandbox. Ran its underlying command directly,
  `npx prettier --check "**/*.md"`: exit 0, "All matched files use Prettier code style!".
- `uv run pytest <paths>` / `uv run task test`: not run. `uv` is not installed and `pytest` is not importable from the
  system Python, so no pass/fail count or time can be recorded. The change is documentation only and touches no code or
  test inputs. No repeated runs were needed (no timing measurement in scope).
