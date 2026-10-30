# Execution Record: E-1793319691-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1793319521-claude-agent-claude-opus-5-5` for intent
`I-1793319507-remaining-docs-follow-sub-intent-merge-rulings`: align the remaining docs with the maintainer's sub-intent
rulings (deciding-node merge target and base, no human stop below the root, distinct sibling slugs, `deciding_branch`
provenance, review-decided splits, plan decides in practice, current-limitation note). In progress.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Pin the reference wording and the exclusion set (done)

- Re-read `docs/git_flow.md` sections 1.2-1.5, 2.2, 3.1 ("Building on siblings"), 4.1, 4.2 and the Stage 3 "Current
  limitation" note. Canonical cross-link form: `[Git Flow](git_flow.md) Stage 3 under the flow equivalence guarantee`
  (as used in `docs/core_concepts.md` and `docs/examples.md`).
- Excluded files: `README.md`, `docs/git_flow.md`, `docs/examples.md`, `docs/core_concepts.md`,
  `docs/ledger_schema.md`. Stage 5 calibration-formatting text is off-limits in every file.

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
