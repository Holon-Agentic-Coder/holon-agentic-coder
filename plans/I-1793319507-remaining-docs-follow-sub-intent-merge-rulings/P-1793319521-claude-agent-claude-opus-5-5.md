# Plan for I-1793319507-remaining-docs-follow-sub-intent-merge-rulings

- **Plan ID:** P-1793319521-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-30T00:18:42.517Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A
- **Exploration stance:** conservative. The rulings and their reference wording already exist in `docs/git_flow.md` and
  `README.md`, so the work is a bounded copy-and-adapt edit across known passages; there is little to learn by
  exploring.
- **Safety priority level:** standard
- **Priority Justification:** Documentation-only change with no code, ledger or config writes. The relevant rules are
  the git-flow branch constraints (`holon-config/world/constraints.md` section 1) and the ledger immutability rule
  (section 3), which this plan only describes and never exercises. `docs/safety.md` is itself edited, so its invariants
  must be restated faithfully, not weakened.

## Exploration

- **Proportion of steps that are exploratory:** 0.0
- **Justification:** Every old-rule passage was located during planning (see the per-step line references), and the
  replacement wording is dictated by the intent. Step 7's sweep is a confirmatory search, not exploration.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 3.4   |
| impact_pred         | 60.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 43.28 |

## Plan Description & Strategy

Edit five named documents, and then any others the sweep turns up, so they state the maintainer's sub-intent rulings in
the same words that `docs/git_flow.md` sections 1.2-1.5 and Stage 3, and `README.md`, already use:

- **R1 (merge target and base):** a sub-intent split from a plan is cut from and merges into that plan branch; one split
  from an execution is cut from and merges into that execution branch. The branch is the deciding node's branch
  (`target_branch` equals `deciding_branch`).
- **R2 (human stop):** below the root no human intervention is needed. The agent raises each sub-intent's PR and merges
  it automatically into the deciding plan or execution branch, for leaf sub-intents and nested parents alike. Only the
  root intent's PR to `main` stops for a human.
- **R3 (ids):** siblings under one deciding node always have different slugs, so their ids cannot collide.
- **R4 (provenance):** the provenance field is `deciding_branch`.
- **R5 (review split):** a split decided during review may nest under the reviewed plan or execution.
- **R6 (who decides):** any level (intent, plan, execution, review) may decide a split, but in practice the plan does.
- **R7 (current-implementation note):** where the flow does not match yet, say so briefly and point to the
  current-limitation note under Stage 3 of `docs/git_flow.md`. Today's flow still targets the parent intent branch, cuts
  each child from it and leaves each child PR merge to the maintainer.

One execution branch, one commit series, with no sub-intents. The intent's Q&A rules out a split, and the work fits the
5.0 budget.

Old-rule passages found during planning (line numbers as of `d9faf43`):

- `docs/agent_design.md` 73-75: "Sub-intents open PRs into their parent intent branch, which the maintainer currently
  merges too".
- `docs/architecture.md` 33-40 ("parent-only merges", "merge ONLY into their parent intent branch", "parent intent
  boundary"); 205-206 ("typically parent intent → `main`"); 336-346 ("merges flow up the intent tree", "Sub-intent
  branches merge into parent intent branches only", "parent intent completion"); 382 and 391 ("parent intent policy",
  "parent-only merge rules").
- `docs/faq.md` 49 ("before merging into the parent intent"); 111 ("rebase onto the parent branch").
- `docs/safety.md` 37 and 61 ("parent intents (direct children of `main`)"); 306-310 ("merge only into their parent
  intent branch; ... the maintainer merges each child PR as well"); 336-355 (the human review workflow approves merges
  "to parent/main", which implies a human stop below the root); 431 ("requests parent to merge siblings sequentially");
  476-491 ("rebase from parent", "Only parent intents ..."); 505-507.
- `docs/planner/planning_architecture_specification.md` 41 ("resolved before the parent intent branch is raised to its
  target"); 60 ("child intent targeting the parent intent branch").
- Sweep candidates: `docs/planning_and_evaluation.md` 314 ("Rebase from parent branch"); `docs/knowledgebase_schema.md`
  224 (a JSON example string, "Rebase from parent branch").

---

## Step 1: Pin the reference wording and the exclusion set

- **Sub‑intent recommendation:** NO
- **Reasoning:** A read-only step that takes minutes; it exists so that every later edit copies the canonical phrasing
  and stays out of the excluded files.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Collect the exact canonical sentences for R1-R7 and fix the list of files that must not be touched.
- **Git branch:**
  `I-1793319507-remaining-docs-follow-sub-intent-merge-rulings/P-1793319521-claude-agent-claude-opus-5-5/E-{timestamp}-apply-sub-intent-rulings-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Re-read `docs/git_flow.md` sections 1.2 to 1.5, 2.2, 3.1, 4.1 and 4.2, and the Stage 3 "Current limitation" note.
   Also re-read `README.md` lines 80-115 and 195-240. Note the anchor text used for cross-links: "Stage 3 under the flow
   equivalence guarantee" and `docs/git_flow.md` section numbers.
2. List the docs that describe Stage 5 calibration formatting and mark them off-limits. Find them with a search for
   "calibration" combined with format or `_calibration.md`; so far only `docs/git_flow.md` and `docs/ledger_schema.md`
   match. Inside those two files, touch only sub-intent sentences, never the Stage 5 calibration text.
3. Record the five excluded files (README.md, git_flow.md, examples.md, core_concepts.md, ledger_schema.md). Edit them
   only if Step 7 finds an old-rule statement in them; planning found none.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints (touch set, concurrent Stage 5 change); `holon-config/world/constraints.md`
  section 1.
- **Potential failure modes for this step:** misreading the current-limitation note and overstating what the flow does
  today.
- **Guardrails and early‑abort checks:** quote rather than paraphrase the R7 note; read only, make no writes.

### Success & Discard Criteria

- **Success:** A working list of the R1-R7 sentences and the off-limits files exists before any edit.
- **Discard:** N/A (read-only); if the reference docs contradict each other, stop and report instead of choosing one.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.98  |
| entropy_pred        | 0.1   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 9.27  |

### Step Metrics Rationale

`EV = 0.98 * 10.0 + 0.5 * 1.0 - 0.3 * 0.1 - 1.0 = 9.80 + 0.50 - 0.03 - 1.0 = 9.27`. It is a read-only step and nearly
certain to succeed.

---

## Step 2: Update `docs/agent_design.md` Human Review Boundary contract

- **Sub‑intent recommendation:** NO
- **Reasoning:** A single bullet, three lines long; low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the behavioural contract state R1, R2 and R7.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Rewrite the **Human Review Boundary** bullet (lines 73-75). Keep the first sentence (the root intent's PR merge by
   the maintainer is the only human step). Replace the second with: sub-intents merge only into the branch of their
   deciding node (in practice the plan branch that listed them; an execution branch when an execution decided the
   split), and the agent raises and merges each sub-intent's PR automatically, so only the root intent's PR to `main`
   stops for a human.
2. Add a short R7 clause: until the implementation follows, the current flow still targets the parent intent branch and
   leaves each sub-intent PR merge to the maintainer, linking `git_flow.md` Stage 3.
3. Leave line 116 ("Recursive intent decomposition without human intervention") unchanged, because it agrees with R2.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Australian English; Prettier.
- **Potential failure modes for this step:** dropping the "no stage before it waits for a human" sentence.
- **Guardrails and early‑abort checks:** compare the edited bullet with `git_flow.md` section 1.3 sentence by sentence.

### Success & Discard Criteria

- **Success:** No "parent intent branch" merge target and no maintainer merge of a sub-intent remains, except inside the
  R7 note.
- **Discard:** If the edit needs restructuring beyond the bullet, keep it to the bullet and note this in the execution
  report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.3   |
| ev_pred             | 6.74  |

### Step Metrics Rationale

`EV = 0.96 * 8.0 + 0.5 * 0.3 - 0.3 * 0.3 - 1.0 = 7.68 + 0.15 - 0.09 - 1.0 = 6.74`. The edit is tiny and its wording is
already fixed.

---

## Step 3: Update `docs/architecture.md` merge and review-boundary sections

- **Sub‑intent recommendation:** NO
- **Reasoning:** Four passages in one file; moderate wording effort but low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the architecture overview state R1, R2, R5, R6 and R7.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Section "4) Strict rebase discipline; parent-only merges" (lines 33-40). Rename the heading to refer to deciding-node
   merges. Rewrite the bullets:
   - sub-intents rebase from their deciding node's branch (in practice the plan branch) before and after execution;
   - they merge ONLY into that branch (the plan branch, or the execution branch when an execution decided the split);
   - they never merge directly to `main`;
   - below the root the agent raises and merges every sub-intent PR automatically, and only the root intent's PR to
     `main` stops for a human;
   - an R7 sentence that links `git_flow.md` Stage 3.
2. Lines 205-206, the Human Review Boundary component: change "typically parent intent → `main`" to "only the root
   intent's PR to `main`".
3. Lines 336-346, the branch topology:
   - rephrase the principle as "merges flow up to the deciding node". Sub-intent branches nest under, are cut from and
     merge into the deciding plan or execution branch (`target_branch` equals `deciding_branch`).
   - Add R6 (any level may decide a split, in practice the plan) and R5 (a review-decided split may nest under the
     reviewed plan or execution).
   - Add R3 (siblings under one deciding node have different slugs, so their ids cannot collide).
   - Only the root intent branch merges into `main`.
   - Change the human review list item "parent intent completion" to "root intent completion (its PR to `main`, the only
     human stop in the flow)".
4. Line 391: change "parent-only merge rules" to "deciding-node-only merge rules". Leave line 382 ("parent intent
   policy") alone, because it concerns trust policy, not merge targets.
5. If the file has a mermaid diagram that labels a merge to the parent intent, update the label; otherwise skip this.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1 (only humans approve merges to `main`, which the
  edit must keep).
- **Potential failure modes for this step:** implying that a sub-intent can reach `main`; contradicting the depth cap
  text.
- **Guardrails and early‑abort checks:** keep the "No direct merges from sub-intents to `main`" bullet word for word.

### Success & Discard Criteria

- **Success:** A grep of the file for "parent intent branch" and "parent-only" returns nothing outside the R7 note.
- **Discard:** If restructuring would grow beyond about 40 changed lines, limit the work to the four passages.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 12.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 9.11  |

### Step Metrics Rationale

`EV = 0.92 * 12.0 + 0.5 * 0.5 - 0.3 * 0.6 - 2.0 = 11.04 + 0.25 - 0.18 - 2.0 = 9.11`. This file has the most passages,
which makes it the largest prose edit after `docs/safety.md`.

---

## Step 4: Update `docs/faq.md` rebase and merge references

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two phrases.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Remove the parent-intent merge target from the FAQ.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Line 49: change "before merging into the parent intent" to "before merging into the deciding node's branch (in
   practice the plan branch; the root intent alone merges to `main`, after human review)".
2. Lines 110-112: change "a background rebase onto the parent branch" to "a background rebase onto the branch it was cut
   from (for a sub-intent, its deciding plan or execution branch)".
3. Read the rest of `docs/faq.md` for any question about human review of child PRs or sibling ids. If one exists, align
   it with R2 and R3.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Australian English; Prettier.
- **Potential failure modes for this step:** the execution-branch rebase sentence concerns an execution, not a
  sub-intent, so over-editing it could make it wrong.
- **Guardrails and early‑abort checks:** keep the meaning "rebase onto the branch it was cut from".

### Success & Discard Criteria

- **Success:** No sentence in `docs/faq.md` states a merge into the parent intent.
- **Discard:** N/A.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.96  |
| entropy_pred        | 0.2   |
| impact_pred         | 5.0   |
| cost_pred           | 0.8   |
| learning_value_pred | 0.2   |
| ev_pred             | 4.04  |

### Step Metrics Rationale

`EV = 0.96 * 5.0 + 0.5 * 0.2 - 0.3 * 0.2 - 0.8 = 4.80 + 0.10 - 0.06 - 0.8 = 4.04`. It is a trivial edit with low impact.

---

## Step 5: Update `docs/safety.md` review boundaries and invariants

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the most safety-relevant document, with about seven passages, but they are all prose in one
  file; a sub-intent would add overhead without lowering risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the safety model state R1, R2, R3 and R7, without weakening any invariant.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Lines 37 and 61, principle 5: change "Only **parent intents** (direct children of `main`)" to "Only **root intents**
   (direct children of `main`)". Add that every merge below the root is raised and merged automatically by the agent
   into the deciding plan or execution branch.
2. Lines 306-310, automatic human review trigger 1:
   - rename the trigger to "Root intent merge to `main`";
   - sub-intents run autonomously through the full flow and merge only into their deciding node's branch (the plan
     branch that listed them, or the execution branch that decided the split), automatically, with no human step below
     the root;
   - the root intent's PR to `main` is the only human step;
   - an R7 sentence that links `git_flow.md` Stage 3.
3. Lines 336-355, the human review workflow: limit it to the root intent.
   - Step 2 becomes "Agent requests the root intent's merge to `main`".
   - "Git diff (from parent branch)" becomes "from `main`".
   - "Approve: Intent merges to parent/main" becomes "Approve: root intent merges to `main`".
   - Add one sentence before the block saying that sub-intent PRs never enter this workflow (R2).
4. Line 431, rebase-conflict recovery: change "requests parent to merge siblings sequentially" to "rebases onto the
   deciding node's branch after earlier siblings have merged into it". This matches `git_flow.md` "Building on
   siblings".
5. Lines 488-491, Invariant 1:
   - sub-intents always rebase from their deciding node's branch before and after execution, and merge only into it;
   - add R3 as one bullet (siblings under one deciding node have different slugs, so their ids cannot collide);
   - only root intents merge to `main`, after human review.
6. Line 507, Invariant 4: change "Parent intents" to "Root intents" and add "(the only human stop in the flow)".
7. Leave the "parent's remaining budget" entropy-accounting lines (285, 289, 518) unchanged. They concern budget
   inheritance, not merge targets or the human stop.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (it is the largest and most scrutinised edit; reviewers will judge the plan mostly on it)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` invariants 1 and 4; `holon-config/world/constraints.md` section 1 (only human
  operators approve merges to `main`).
- **Potential failure modes for this step:**
  - removing a governance or estimator human-approval requirement by mistake;
  - presenting automatic sub-intent merging as already implemented;
  - breaking the code fence of the review-workflow block.
- **Guardrails and early‑abort checks:**
  - after editing, diff the file and check that every "require(s) human approval" line for governance, estimators and
    core invariants survives;
  - check that the R7 note is present wherever automatic merging is stated.

### Success & Discard Criteria

- **Success:** Every passage listed above is updated. No "parent intent branch" merge target or below-root human stop
  remains, and every governance approval line is unchanged.
- **Discard:** If an edit would change the meaning of a governance invariant, revert that hunk and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.9   |
| impact_pred         | 15.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 10.98 |

### Step Metrics Rationale

`EV = 0.90 * 15.0 + 0.5 * 0.5 - 0.3 * 0.9 - 2.5 = 13.50 + 0.25 - 0.27 - 2.5 = 10.98`. This step has the highest impact
(it is the safety contract) and the highest entropy (the most passages, plus the fenced block), so it is the bottleneck.

---

## Step 6: Update `docs/planner/planning_architecture_specification.md` decomposition bullets

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two sentences.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the planner specification state where decomposed children branch from and merge to (R1, R4, R6,
  R7).
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Line 41: change "resolved before the parent intent branch is raised to its target" to "nested under, cut from and
   merged automatically into the plan branch that listed it, before that plan's work is raised to its own target".
2. Lines 59-60: change "as a child intent targeting the parent intent branch" to "as a child intent cut from and
   targeting the deciding plan branch (its `deciding_branch`)". Add a short R7 sentence, and an R6 half-sentence saying
   that the plan is the stage that decides a split in practice.
3. Check §4 of the file for text about merge targets or human review of child PRs, and align it with R2.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** touch set (`docs/` only); never touch the planner prompt template under `holon-config/`, even
  though it carries similar wording.
- **Potential failure modes for this step:** being tempted to align `holon-config/prompts/planner.template.md`, which is
  out of scope.
- **Guardrails and early‑abort checks:** before committing, check that `git diff --name-only` lists only `docs/` paths.

### Success & Discard Criteria

- **Success:** No "targeting the parent intent branch" text remains, and the R7 note is present.
- **Discard:** N/A.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.3   |
| impact_pred         | 7.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.3   |
| ev_pred             | 5.71  |

### Step Metrics Rationale

`EV = 0.95 * 7.0 + 0.5 * 0.3 - 0.3 * 0.3 - 1.0 = 6.65 + 0.15 - 0.09 - 1.0 = 5.71`.

---

## Step 7: Sweep the whole `docs/` tree for remaining old-rule statements

- **Sub‑intent recommendation:** NO
- **Reasoning:** A search followed by small fixes; the candidates were already identified during planning.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Satisfy goal 2: no file under `docs/` states the old rules.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Search `docs/` recursively, skipping `docs/agent_selection/logs/`, case-insensitively, for these patterns:
   - "parent intent branch", "parent branch", "parent-only", "merge(s) into (its/their/the) parent";
   - "parent intent → `main`", "parent intents (direct children";
   - "open question", "nested parent" near "human", and "collid" near "sibling";
   - "maintainer merges" near "sub-intent" or "child".
2. Classify each hit:
   - (a) correct current-implementation (R7) note: keep;
   - (b) generic rebase or budget wording that does not name a merge target or a human stop: keep;
   - (c) an old-rule statement: fix with R1-R7 wording.
3. Expected (c) fixes: `docs/planning_and_evaluation.md` line 314. Change "Rebase from parent branch" to "Rebase from
   the deciding node's branch (in practice the plan branch)".
4. `docs/knowledgebase_schema.md` line 224 is a sample entry string for a generic rebase skill, so classify it as (b)
   and leave it. Edit it only if the context shows it means the sub-intent merge target.
5. For any hit in the five excluded files, make the minimal fix and record it in the execution report. Planning found
   only correct R7 notes there. Never edit Stage 5 calibration-formatting text.
6. Run the search again and check that only (a) and (b) hits remain.

### Dependencies & Criticality

- **Depends on:** Steps 2-6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent touch set; the concurrent Stage 5 calibration change.
- **Potential failure modes for this step:** over-editing correct R7 notes; touching calibration text and creating a
  merge conflict with the concurrent change.
- **Guardrails and early‑abort checks:** for any hit inside a calibration section, record it as (b) or report it, and
  never edit it.

### Success & Discard Criteria

- **Success:** The final search shows only (a) and (b) hits, and the execution report lists each classification.
- **Discard:** If more than about 10 new (c) hits appear outside the expected files, fix the clear ones and list the
  rest for a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 8.0   |
| cost_pred           | 1.2   |
| learning_value_pred | 1.0   |
| ev_pred             | 6.32  |

### Step Metrics Rationale

`EV = 0.90 * 8.0 + 0.5 * 1.0 - 0.3 * 0.6 - 1.2 = 7.20 + 0.50 - 0.18 - 1.2 = 6.32`. The learning value is higher than in
the edit steps because the sweep is the only check that the doc tree is consistent.

---

## Step 8: Format, lint and verify the change set

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical validation.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Meet the formatting, lint, touch-set and language constraints, then commit.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run the repository Prettier (`npx prettier --write`) on the touched files only, then run `uv run task lint-docs`,
   which must pass.
2. Run `git diff --name-only` against the plan branch. Every path must be under `docs/`. Make sure there are:
   - no changes under `holon-knowledge/ledger/` (still exactly three ledgers);
   - no code, `holon-config/` or `apps/` changes.
3. Check the added lines for Australian spelling (for example "behaviour", "organise", "recognise") and for references
   to any coordinating harness, its directories or tracker ids. There must be none.
4. Check that relative links added in edited docs resolve, for example `git_flow.md` from `docs/` and `../git_flow.md`
   from `docs/planner/`.
5. Do not run tests. The change is documentation only, so no test is affected, and the test-run rules call for no
   repeated runs. If the executor policy requires a suite run, run `uv run task test` once and report the result.
6. Commit with a message summarising the five documents and the sweep fixes.

### Dependencies & Criticality

- **Depends on:** Steps 2-7
- **Is Bottleneck:** YES (a failing lint-docs check fails the intent)

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints 3; `holon-config/world/constraints.md` section 3 (ledgers).
- **Potential failure modes for this step:**
  - Prettier oscillating on underscores or asterisks in new text;
  - lint failing on an unrelated pre-existing file.
- **Guardrails and early‑abort checks:**
  - wrap identifiers such as `deciding_branch` and `target_branch` in code spans;
  - if lint fails only on files outside the change, report it and do not edit them.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` exits with 0, the diff is limited to `docs/`, and the commit is pushed to the
  execution branch.
- **Discard:** If lint fails on untouched files that the concurrent change owns, stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.2   |
| ev_pred             | 4.23  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 0.2 - 0.3 * 0.4 - 0.5 = 4.75 + 0.10 - 0.12 - 0.5 = 4.23`.

---

### Strategy Rationale

The intent is accepted as it stands. The maintainer has already made the rulings, and the reference wording exists, so
reframing would add no value. The Q&A rules out a split, and the summed entropy (3.4) is under the 5.0 budget, so there
is no `## Sub-intent list`.

How the overall metrics were derived from the steps:

- **p_success:** the bottleneck steps are Step 5 (0.90) and Step 8 (0.95), with Step 3 (0.92) and Step 7 (0.90) close
  behind. The step failures are correlated (all are similar prose edits checked by the same reviewer), so the overall
  value is set by judgement at 0.88, slightly below the weakest step, rather than as the product of the steps (about
  0.65).
- **entropy:** the sum of the steps: `0.1 + 0.3 + 0.6 + 0.2 + 0.9 + 0.3 + 0.6 + 0.4 = 3.4`.
- **impact:** the sum of the steps (70) is discounted to 60 because the steps overlap. Removing the old rules everywhere
  matters more than any single file.
- **cost:** the sum of the steps, `1.0 + 1.0 + 2.0 + 0.8 + 2.5 + 1.0 + 1.2 + 0.5 = 10.0`.
- **learning value:** about 3.0. It comes from the sweep's evidence on how consistent the docs are.

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S - Cost`

`EV = 0.88 * 60.0 + 0.5 * 3.0 - 0.3 * 3.4 - 10.0 = 52.80 + 1.50 - 1.02 - 10.0 = 43.28`

This uses `lambda = 0.3` from `holon-config/metrics/ev_config.json` and `mu = 0.5`.

There is one strong assumption: the current-implementation note in `docs/git_flow.md` Stage 3 stays accurate while this
change runs. The code change is separate and has not landed, so the R7 notes added here must point to that one note
rather than restate its details. If the code later changes, only one note then needs updating.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: only humans approve merges to `main`. The plan restates this rule and
    never weakens it.
  - section 1 commit boundaries: commits go only to this execution branch.
  - section 3: append-only ledgers, never touched.
  - `docs/safety.md` invariants 1 and 4: the plan edits their wording but keeps their meaning for governance and root
    promotion.
- **Potential violations or edge cases:**
  - editing a Stage 5 calibration-formatting passage that the concurrent change owns;
  - stating automatic sub-intent merging as already implemented;
  - drifting into `holon-config/prompts/` or the `README.md` wording;
  - a Prettier oscillation caused by an unescaped underscore.
- **Mitigations built into the plan:**
  - Step 1 fixes the exclusion set;
  - every automatic-merge statement carries an R7 pointer;
  - Step 8 checks that the diff is limited to `docs/`, keeps identifiers in code spans and runs lint-docs;
  - Step 5 checks that the governance-approval lines survive.
- **Residual risk accepted (and why):** some "parent" wording that concerns budget inheritance or trust policy is left
  as it is on purpose. It is not a merge-target or human-stop rule, and rewriting it would widen the change beyond the
  intent.
- **Allocated Entropy Budget:** 5.0
- **Predicted Plan Entropy:** 3.4
- **Budget Compliance:** The strategy fits within budget.
