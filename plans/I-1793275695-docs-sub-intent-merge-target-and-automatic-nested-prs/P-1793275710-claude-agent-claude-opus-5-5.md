# Plan for I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs

- **Plan ID:** P-1793275710-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T12:08:31.258Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The maintainer has decided all five questions and asked for one intent; the
  plan only adds one framing rule (see "Framing rule" below) so the documents stay truthful about code that has not yet
  changed.
- **Exploration stance:** conservative, with one balanced verification step. The work is a deterministic documentation
  rewrite with a known edit inventory; the only uncertainty is completeness (missing a stale sentence) and consistency
  of the "current flow" notes, which a dedicated sweep step targets.
- **Safety priority level:** standard
- **Priority Justification:** Documentation-only change on an intent branch; no code, tests, ledgers or `main` are
  touched (`holon-config/world/constraints.md` section 1 commit boundaries and section 3 ledger immutability are
  respected trivially). The intent's touch set and the concurrent ownership of the ledger write-guarantee wording in
  `docs/ledger_schema.md` are the only elevated-care points, handled by explicit guardrails rather than a higher level.

## Exploration

- **Proportion of steps that are exploratory:** 0.14 (1 of 7 steps is BALANCED; none is fully EXPLORATORY)
- **Justification:** The decisions are fixed, so exploration has low value for the outcome. The one balanced step
  (Step 6) tests the hypothesis that a phrase-based sweep finds every stale merge-target or open-question statement,
  which is reusable knowledge for future cross-document doc-consistency intents.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 3.6   |
| impact_pred         | 70.0  |
| cost_pred           | 12.5  |
| learning_value_pred | 3.0   |
| ev_pred             | 43.92 |

## Plan Description & Strategy

Apply the five maintainer decisions consistently in the five touch-set files, editing the canonical document
(`docs/git_flow.md`) first so every other file can mirror its wording, then sweeping all five files for residual stale
statements, then formatting and linting.

**The decided rules to write (canonical wording to reuse across files, in Australian English):**

1. **Field name.** `deciding_branch` stays. Nothing lists it as open.
2. **Review-decided split.** A split decided during review may nest under the reviewed plan or under the reviewed
   execution; either is acceptable. The `docs/git_flow.md` section 2.2 table row "A review of an execution" becomes "A
   review" with "The reviewed plan's node or the reviewed execution's node (either is acceptable; a review has no branch
   node of its own)". `parent_plan_id`/`parent_execution_id` descriptions follow that choice.
3. **Merge target and branch base.** The deciding node's branch is both the branch base and the merge target of a
   sub-intent: a plan-decided sub-intent is cut from and merges into that plan branch (`I-.../P-.../_`); an
   execution-decided one is cut from and merges into that execution branch (`I-.../P-.../E-.../_`); an intent-stage
   split's deciding node is the intent branch itself, so nothing changes there. `target_branch` equals
   `deciding_branch`. The sub-intent's immediate parent for the dual rebase rule is therefore the deciding node's
   branch, and siblings build on each other through that branch.
4. **Human stop.** Below the root no human intervention is needed: the agent raises each sub-intent's PR automatically
   and merges it automatically into the deciding plan or execution branch, for leaf sub-intents and nested parents
   alike. Only the root intent's PR to `main` stops for a human.
5. **Sibling invariant.** Siblings under one deciding node always have different slugs, so their ids (timestamp plus
   slug) cannot collide even when created in the same second. Placed next to the project-wide id uniqueness rule in
   `docs/git_flow.md` section 1.5 and mirrored briefly where ids are discussed.
6. **Kept rules.** Any level may decide a split but in practice the plan decides; ids are unique project-wide; branch
   refs end in `/_`; examples keep causally ordered timestamps and convention ids.

**Framing rule (the plan's one judgement call).** The intent says the implementation is a separate change. The documents
therefore state the decided rules as the model everywhere (no "open" wording, no "target model; whether ..." hedges
about nested parents). Where a paragraph explicitly describes what `holon flow` does today (the Stage 3 "Current
limitation" paragraph in `docs/git_flow.md` and the child-row payload paragraph in `docs/ledger_schema.md`), keep one
short, factual "until the implementation follows" note saying the current flow still records the parent intent branch as
`target_branch` and leaves the child PR merges to the maintainer. That note is a statement of fact, not an open
question, so it satisfies decision 4 while keeping the docs honest about code that this intent may not change. All other
"current flow: the maintainer merges each child PR" sentences in README, core concepts and examples are replaced by the
decided rule, pointing to that single note in `docs/git_flow.md` Stage 3 where a pointer is useful.

---

## Step 1: Baseline inventory and lint baseline

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, minutes of work, no risk; it only feeds later steps.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Produce the exact list of sentences to change in the touch set and confirm the docs lint passes
  before any edit.
- **Git branch:**
  `I-1793275695-docs-sub-intent-merge-target-and-automatic-nested-prs/P-1793275710-claude-agent-claude-opus-5-5/E-{timestamp}-docs-sub-intent-merge-target/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run task lint-docs` on the untouched tree and record the result, so a later failure can be attributed.
2. Grep the five touch-set files (case-insensitive) for: `open question`, `is open`, `still open`, `stops for a human`,
   `combined-diff`, `parent intent branch`, `merges into`, `merge to parent`, `target_branch`, `deciding_branch`,
   `immediate parent`, `cut from`, `branch from`, `collid`, `collision`, `same second`, `same-second`,
   `review of an execution`, `maintainer merges`, `maintainer also merges`, `target model`.
3. Known sites from planning (main as of this plan; re-confirm line numbers): README.md lines ~106-110, ~198-202,
   ~230-236 and the sub-intent branch block below it; `docs/git_flow.md` lines ~19-37, 49-53 (section 1.5), 75-136
   (section 2.2 table, base/merge paragraph, provenance list and JSON), ~226-240 (Stage 3), 331-341 (section 3.1),
   372-415 (section 4.1), 417-430 (section 4.2), 464-476 (section 4.3), ~570 and ~587-600 (sections 7.x), 617-690
   (section 8 trees and merge order), ~716-735 (reactive intent provenance), 805-820 (section 12), 822-850 (section 13);
   `docs/examples.md` ~637-660, ~763-900 (Example 2 ledger rows, merge commands), ~933-1115 (Example 3), ~1682-1760
   (Example 10); `docs/core_concepts.md` lines ~73-91, ~261-263, ~311-312; `docs/ledger_schema.md` lines ~122-136 (child
   intent rows) and ~718 (validation rule 9).
4. Identify the paragraph(s) in `docs/ledger_schema.md` that carry the ledger write-guarantee wording (append-only,
   locking, atomic write) and mark them as do-not-edit.
5. Note that the same-second collision question and a `deciding_branch` naming question were not found by planning-time
   grep in the touch set; if Step 1 also finds none, decisions 1 and 5 reduce to "ensure nothing presents them as open"
   plus adding the sibling invariant.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` section 1 (commit boundaries); intent constraint 7 (touch
  set).
- **Potential failure modes for this step:** baseline lint already failing on unrelated files.
- **Guardrails and early‑abort checks:** if the baseline lint fails on files outside the touch set, record it and do not
  fix those files; judge Step 7 only on the touch set plus "no new failures".

### Success & Discard Criteria

- **Success:** an edit inventory covering every grep hit, each classified as change / keep, and a recorded baseline lint
  result.
- **Discard:** none realistic; if `uv` or `npx` is unavailable, continue and run Prettier by the repository config in
  Step 7.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.2   |
| impact_pred         | 10.0  |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 9.64  |

### Step Metrics Rationale

`EV = 0.97 * 10.0 + 0.5 * 2.0 - 0.3 * 0.2 - 1.0 = 9.70 + 1.00 - 0.06 - 1.00 = 9.64`. Read-only, so near-certain success;
its impact is enabling completeness in later steps.

---

## Step 2: Rewrite the canonical rules in docs/git_flow.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** Largest edit, but it is prose in one file with a fixed specification and full rollback by git; the
  intent explicitly forbids splitting.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make `docs/git_flow.md` the canonical statement of all five decisions, so the other files can mirror
  it.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Section 1 core principles (~19-38): sub-intent branch is cut from, and merges into, the branch of the node that
   decided the split (in practice the plan branch); replace "merge only into their parent intent branch ... open
   (section 4.2)" with the decided rule: below the root the agent raises each sub-intent's PR and merges it
   automatically into the deciding plan or execution branch; only the root intent's PR to `main` stops for a human.
   Recursive bullet: sub-sub-intents merge into the deciding node inside their parent sub-intent, and the root intent's
   tree reaches `main` only through the root PR. Keep "never directly to `main`".
2. Section 1.4 terminology: keep Parent Intent definition; add or adjust a "Deciding node" term (the intent, plan or
   execution branch whose stage decided the split; its branch is the sub-intent's base and merge target).
3. Section 1.5: keep project-wide uniqueness; add the sibling invariant sentence (decision 5).
4. Section 2.2: update table (review row per decision 2); replace "cut from its parent intent branch ... the deciding
   node only fixes where the name sits" with "cut from the deciding node's branch and merges back into it"; keep the
   checkpoint-keying sentence. Provenance list: `target_branch` is the deciding node's branch (equal to
   `deciding_branch`); `parent_plan_id` / `parent_execution_id` wording follows decision 2 (a review-decided split
   records the reviewed plan, plus the reviewed execution when it nests under the execution). JSON example:
   `target_branch` becomes `I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/_`. Keep all ids.
5. Section 2.3: no rule change; ensure the "Pattern" sentence does not imply the intent branch is the base.
6. Stage 3 paragraph (~222-240): children target the deciding plan branch (`target_branch`); remove "(for a nested
   parent, see the open question in section 4.2)". Rewrite "Current limitation" per the framing rule: one factual note
   that the current implementation still targets the parent intent branch and leaves child PR merges to the maintainer
   until the separate implementation change lands; keep the sequential-children and summary facts.
7. Section 3.1: immediate parent for the dual rebase rule becomes the deciding node's branch (invert the current
   sentence explicitly). "Building on siblings": siblings build on each other through the deciding node's branch. Update
   section 3.2 commands if they name the intent branch as the rebase source for a sub-intent.
8. Section 4.1: retitle to apply to every sub-intent (e.g. "Sub-Intent → Deciding Node Merge (Automatic)"); remove the
   target-model note about the maintainer and the nested-parent open question; state the agent raises and merges the PR
   automatically, for leaf sub-intents and for nested parents once their own sub-intents have merged. Update the process
   list and command block to merge into the plan branch.
9. Section 4.2: root intent's PR to `main` is the only human stop; replace the nested-parent open-question sentence with
   "a nested parent is a sub-intent and merges automatically as in section 4.1". Keep the current-flow note only as a
   pointer to the Stage 3 note.
10. Section 4.3 constraints: allowed/forbidden lists state merges into the deciding node's branch; "multiple sub-intents
    merging to same deciding node".
11. Sections 5, 6, 7.x: fix "parent" wording where it means the merge target; section 7 lifecycle code comment (~570)
    and section 7.4 (~587-600) to "cut from and merged into the deciding plan branch, automatically"; remove the open
    question.
12. Section 8: tree and merge-order list target the deciding plan branches
    (`I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/_` for children,
    `.../I-1771890392-improve-estimators/P-1771890398-claude-code-claude-opus-4.6/_` for grandchildren); invert line
    ~668 ("Sub-intents merge into the deciding plan branch, not the parent intent branch"); remove the open question at
    ~684-685. Root intent PR to `main` is the last, human step. Keep timestamps causal.
13. Section 9 reactive intent (~716-735): `deciding_branch` and `target_branch` are both the execution branch.
14. Sections 12-13: summary table row "Leaf sub-intent → Parent merge" becomes "Sub-intent → Deciding node merge" with
    no human review; key-insight paragraph states the rule without the open question; section 13.2/13.3 wording on
    nested parents follows decision 4. Remove "(for a nested parent, see section 4.2)".

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (other files mirror its wording; p_success 0.85 is well above 0.6, so no sub-intent).

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 7 (touch set, target-native paths, Australian English);
  `holon-config/world/constraints.md` section 1 (the world file still says plans branch off the intent branch, which
  remains true and is not contradicted).
- **Potential failure modes for this step:** missing one of ~15 sites; contradicting the kept rule "any level may
  decide, in practice the plan"; breaking the section anchor `#22-sub-intent-branch-nested-under-the-deciding-node` used
  by `docs/ledger_schema.md`; non-causal timestamps in new examples.
- **Guardrails and early‑abort checks:** do not rename section 2.2's heading; reuse existing ids only; re-grep the file
  for `open` and `parent intent branch` after editing; every remaining "parent intent branch" occurrence must be one of
  the deliberate ones (plans branch off it; the Stage 3 current-implementation note).

### Success & Discard Criteria

- **Success:** zero "open question" / "is open" / "still open" sub-intent hedges remain in the file; every merge step,
  tree, JSON example and branch-base statement names the deciding node's branch; the sibling invariant and decision 2
  are stated.
- **Discard:** if the edit needs to touch code or files outside the touch set to stay consistent, stop and record it as
  a follow-up instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 30.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 21.89 |

### Step Metrics Rationale

`EV = 0.85 * 30.0 + 0.5 * 1.5 - 0.3 * 1.2 - 4.0 = 25.50 + 0.75 - 0.36 - 4.00 = 21.89`. Highest impact because it is the
canonical source; entropy and cost reflect the number of sites, and p_success is reduced for the risk of a missed
sentence, which Step 6 mitigates.

---

## Step 3: Mirror the rules in README.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** A handful of short paragraphs; low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make the README's git-flow principles, key rules and branch-naming section match the decisions.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Principle 5 (~106-110): "Sub-intents merge only into the branch of the node that decided the split (in practice the
   plan branch)"; "Never directly to `main`"; "Below the root the agent raises and merges every sub-intent PR
   automatically; only the root intent's PR to `main` is presented to a human". Remove the "until aggregation is
   automated, the maintainer also merges" clause.
2. Principle 6 (~114): rebase from the latest remote deciding node branch.
3. Key rules (~198-202): branch from and merge into the deciding node's branch; human review only at the root PR; remove
   the "today the maintainer also merges" clause.
4. Branch naming (~230-236): replace "still cut from its parent intent branch and targets its PR at that branch" with
   "cut from the deciding node's branch and merges back into it automatically"; mention decision 2 (a review-decided
   split nests under the reviewed plan or execution); add one sentence for the sibling invariant if the section
   discusses ids. Check the code-fenced sub-intent branch block comments for a review row and align it.
5. Leave the trust/autonomy sections (~507-613) unchanged unless they state a merge target.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 7; `docs/safety.md` principle 5 (only root intents need human review), which the
  new wording reinforces.
- **Potential failure modes for this step:** contradicting `docs/safety.md` wording; editing unrelated README sections.
- **Guardrails and early‑abort checks:** diff must be confined to the listed sections.

### Success & Discard Criteria

- **Success:** README states the deciding-node merge target and the root-only human stop with no residual parent-intent
  merge-target sentence.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.4   |
| impact_pred         | 12.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 9.67  |

### Step Metrics Rationale

`EV = 0.92 * 12.0 + 0.5 * 0.5 - 0.3 * 0.4 - 1.5 = 11.04 + 0.25 - 0.12 - 1.50 = 9.67`. The README is the most visible
entry point, hence moderate impact; few sites, hence high p_success.

---

## Step 4: Update docs/core_concepts.md and the sub-intent sections of docs/ledger_schema.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, well-bounded edits; the only risk is the concurrent-ownership boundary in the ledger schema,
  handled by a guardrail.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Align the concept definitions, lifecycle transitions and ledger field descriptions with the
  decisions.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/core_concepts.md` 1.3 Parent Intent (~73-79): the parent intent owns the sub-intents through its deciding plan
   (or execution); their PRs are raised and merged automatically into that deciding branch; remove the open question.
2. 1.4 Sub-Intent (~81-91): merges only into its deciding node's branch, automatically, whether leaf or nested parent;
   remove the open question; keep "in practice the plan decides" and the depth cap.
3. 5.2 Transitions (~261-263): "Completed → Merged: automatically for sub-intents (into the deciding node's branch); by
   the maintainer's PR merge only for the root intent". Remove the open-question pointer.
4. Summary table (~311-312): "Receives its sub-intents' merges through its deciding plan or execution branch" and "its
   PR is merged automatically into the deciding node's branch". Re-run Prettier later to realign the table.
5. `docs/ledger_schema.md` child intent rows (~122-136): `target_branch` is the deciding node's branch (equal to
   `deciding_branch`); `parent_plan_id`/`parent_execution_id` wording follows decision 2; add the sibling invariant in
   one clause; keep `sub_intent_depth` text. Add the framing-rule note that current flow rows record the parent intent
   branch until the implementation follows (historic rows are never rewritten).
6. Validation rule 9 (~718): "If a sub-intent merges, the target must be its deciding node's branch (no merges to `main`
   from sub-intents)".
7. Do not edit any paragraph about append-only guarantees, locking, atomic writes or durability (the ledger
   write-guarantee wording), even if adjacent.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 7 (sub-intent sections only in `docs/ledger_schema.md`; exactly three
  append-only ledgers); `holon-config/world/constraints.md` section 3.
- **Potential failure modes for this step:** a diff hunk in `docs/ledger_schema.md` overlapping the write-guarantee
  wording (merge conflict with the concurrent change); accidentally implying a fourth ledger.
- **Guardrails and early‑abort checks:** after editing, `git diff docs/ledger_schema.md` hunks must fall only inside the
  child-intent-row paragraphs and validation rule 9; otherwise revert the stray hunk.

### Success & Discard Criteria

- **Success:** no open-question pointers remain in either file; ledger field descriptions match `docs/git_flow.md`
  section 2.2; write-guarantee wording byte-identical.
- **Discard:** if the decision cannot be expressed without touching the write-guarantee wording, leave that sentence
  unchanged and report it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 7.60  |

### Step Metrics Rationale

`EV = 0.90 * 10.0 + 0.5 * 0.5 - 0.3 * 0.5 - 1.5 = 9.00 + 0.25 - 0.15 - 1.50 = 7.60`. Few sites; the concurrency boundary
keeps p_success slightly below the README step.

---

## Step 5: Update trees, ledger rows and merge steps in docs/examples.md

- **Sub‑intent recommendation:** NO
- **Reasoning:** Several examples, but each is mechanical once the canonical rule exists; splitting would only add
  coordination cost.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Make every worked example show branch base and merge target at the deciding node and automatic
  sub-intent merges.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Example 2 intro and note (~637-644): replace the note with the decided rule (sub-intent PRs, including the nested
   parent `I-1771890503-implement-entropy`, are raised and merged automatically into the deciding plan branch; only the
   root PR to `main` stops for a human); drop the "still open" text and the "maintainer merges" clause.
2. Example 2 ledger rows (~771, ~833): `target_branch` becomes the `deciding_branch` value; sub-sub-intent rows (if
   shown) target the nested parent's plan branch.
3. Example 2 merge steps (~801-893): merge commands/descriptions target
   `I-1771890500-implement-metrics-module/P-1771890501-claude-code-claude-sonnet-4.6/_` (and the nested parent's plan
   branch for sub-sub-intents); "After both sub-sub-intents complete" paragraph (~875) states the nested parent merges
   automatically into the root's deciding plan branch, without the open question; "merging to parent" in step 6 and the
   final "All sub-intents merged to parent branch" become "merged into the deciding plan branch", followed by the root
   intent PR to `main` as the human step.
4. Example 3 (~933-1115): `resolve-conflict` is decided by execution `E-1771890605-add-logging`, so its `target_branch`,
   rebase source (~1053) and merge target (~1086-1096) become that execution branch; `add-logging` merges automatically
   into the deciding plan branch `I-1771890600-refactor-metrics/P-1771890601-.../_` (~1098-1113), with no open question;
   fix the "parent intent branch is already contained" comment accordingly. Sibling rebases
   (`add-caching`/`add-logging`, ~949-990) rebase onto the deciding plan branch.
5. Example 10 (~1682-1760): spawn the sub-intent branch from the deciding plan branch tip
   (`I-1771890389-refactor-metrics/P-1771890390-claude-code-claude-sonnet-4.6/_`); rebase onto it; the merge commands
   check out that plan branch and merge with no-fast-forward, performed automatically by the agent; update the "cut from
   the parent intent branch" annotation and the sibling sentence (~1726).
6. Summary table (~1775): wording "automatic sub-intent merges into the deciding node".
7. Keep every existing convention id; any new timestamp must be later than its causes.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (a residual stale example is a quality defect, caught by Step 6, not a plan failure).

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints 6-7 (causal timestamps, convention ids, target-native paths).
- **Potential failure modes for this step:** shell snippets left inconsistent (checkout of intent branch, merge of plan
  branch); a nested parent described as stopping for a human; non-causal ids.
- **Guardrails and early‑abort checks:** for each example, walk the commands in order and check base, rebase source and
  merge target are the same deciding branch.

### Success & Discard Criteria

- **Success:** each example's tree, JSON rows and commands agree with `docs/git_flow.md` sections 2.2, 3.1 and 4.1.
- **Discard:** none expected.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.9   |
| impact_pred         | 15.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 9.98  |

### Step Metrics Rationale

`EV = 0.85 * 15.0 + 0.5 * 1.0 - 0.3 * 0.9 - 3.0 = 12.75 + 0.50 - 0.27 - 3.00 = 9.98`. Many concrete commands and JSON
rows make this the second-largest step; p_success limited by consistency risk inside shell snippets.

---

## Step 6: Cross-document consistency sweep

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-mostly verification; its findings are fixed in place.
- **Step Type:** TEST
- **Exploration level:** BALANCED

- **Hypothesis being tested:** A phrase-based grep (Step 1 list) plus a targeted read of each changed hunk finds every
  remaining stale merge-target, branch-base or open-question statement in the touch set.
- **Learning target:** Whether phrase sweeps are sufficient for doc-consistency intents, or whether stale meaning hides
  in paraphrases (e.g. "merges back into it", "the parent") that the sweep misses; record the misses in the execution
  report.
- **Maximum acceptable cost for this learning:** about 1 cost unit (one grep pass plus reading changed hunks once).

### Intent & Git Integration

- **Step Intent:** Prove the five decisions are applied consistently and no forbidden content was introduced.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Re-run the Step 1 grep over the five files; every hit for `open`, `stops for a human`, `combined-diff`,
   `parent intent branch`, `maintainer merges` must be either removed or an intended keep (plans branch off the intent
   branch; the single current-implementation note; the root PR to `main`).
2. Grep for paraphrases: `merges back`, `into its parent`, `into the parent`, `to parent`, `from parent`, `rebase from`.
3. Check every `target_branch` value in JSON equals the adjacent `deciding_branch` (except intent-stage splits, where
   both are the intent branch).
4. Check every branch ref in edited text ends in `/_`, ids follow convention, and timestamps stay causal.
5. Check Australian spelling in new text (e.g. "behaviour", "organise", "recognise") and that no edited line references
   a coordinating harness, its directories or tracker ids, and that no fourth ledger is mentioned.
6. Check the anchor `git_flow.md#22-sub-intent-branch-nested-under-the-deciding-node` still resolves and any new
   section-number pointers point at the right sections.
7. Confirm `git diff --stat` touches only the five files, and the `docs/ledger_schema.md` hunks avoid the
   write-guarantee wording.

### Dependencies & Criticality

- **Depends on:** Steps 2-5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraints 6-7; `holon-config/world/constraints.md` sections 1 and 3.
- **Potential failure modes for this step:** false confidence from a sweep that misses paraphrases.
- **Guardrails and early‑abort checks:** read every changed hunk once, not only grep output.

### Success & Discard Criteria

- **Success:** zero unintended hits; diff confined to the touch set; findings logged.
- **Discard:** if more than ~10 residual issues appear, return to the owning step rather than patching ad hoc.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.3   |
| impact_pred         | 8.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 7.11  |

### Step Metrics Rationale

`EV = 0.90 * 8.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.0 = 7.20 + 1.00 - 0.09 - 1.00 = 7.11`. Learning value is the main reason
for the balanced label: it calibrates how reliable sweep-based verification is for documentation intents.

---

## Step 7: Format with Prettier and pass the docs lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical, deterministic, seconds of work.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Satisfy the repository formatting and `uv run task lint-docs` gate, then commit.
- **Git branch:** same execution branch as Step 1.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run Prettier in write mode with the repository configuration on the five touched files only.
2. Run `uv run task lint-docs`; it must pass (or show no failures beyond the Step 1 baseline outside the touch set).
3. Re-run the Prettier check a second time to confirm formatting is stable (no oscillation from emphasis-like
   underscores or asterisks; wrap field names such as `deciding_branch` in code spans).
4. No pytest run is needed: no code or tests change, and the intent does not ask for timing measurements. Optionally run
   `uv run task test` once only if the repository's docs tests (for example template or formatting tests) read these
   files; otherwise skip and say so in the execution report.
5. Commit the five files on the execution branch with a descriptive message.

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 7 (Prettier, `lint-docs`); `holon-config/world/constraints.md` section 1 (commit
  only to the execution branch).
- **Potential failure modes for this step:** Prettier reflowing table cells or code fences in unexpected ways; Prettier
  touching files outside the touch set if run repo-wide in write mode.
- **Guardrails and early‑abort checks:** run write mode only on the touched files; inspect `git status` before commit.

### Success & Discard Criteria

- **Success:** `uv run task lint-docs` passes; formatting stable on a second check; commit contains only the five files.
- **Discard:** if lint fails on content Prettier cannot fix after two attempts, report the specific file and line.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.1   |
| impact_pred         | 5.0   |
| cost_pred           | 0.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 4.47  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 0.5 - 0.3 * 0.1 - 0.5 = 4.75 + 0.25 - 0.03 - 0.50 = 4.47`. A gate step with low cost and
near-certain success.

---

### Strategy Rationale

The intent is accepted as is: the maintainer has decided every design question, asked for one intent and for
documentation only, so reframing, deferring or splitting would add cost without increasing value. The strategy is
"canonical first, mirror second, sweep third": `docs/git_flow.md` (Step 2) is the bottleneck and the source of the
reusable wording; README, core concepts, the ledger schema and examples (Steps 3-5) mirror it; Step 6 verifies
cross-file consistency and Step 7 enforces formatting and lint.

The one judgement call is the framing rule. Because the code still targets the parent intent branch and the
implementation is a separate change, removing every "current flow" statement would make the docs describe behaviour the
code does not have. The plan therefore states the decided rules as the model everywhere, removes every open-question
pointer, and keeps exactly one factual current-implementation note (in the Stage 3 paragraph, mirrored in the ledger
schema's child-row payload description). That satisfies decision 4 without misdocumenting the code.

**Aggregation of overall metrics:**

- `entropy_pred` is the sum of step entropies: `0.2 + 1.2 + 0.4 + 0.5 + 0.9 + 0.3 + 0.1 = 3.6`, within the 6.0 budget.
- `cost_pred` is the sum of step costs: `1.0 + 4.0 + 1.5 + 1.5 + 3.0 + 1.0 + 0.5 = 12.5`.
- `p_success_pred = 0.80` is a qualitative judgement just below the bottleneck (Step 2 and Step 5 at 0.85): step
  failures are strongly correlated (same author, same rule set) and Step 6 fixes residual issues in-loop, so a naive
  product (about 0.48) would understate success; the remaining risk is a reviewer disagreeing with the framing rule.
- `impact_pred = 70.0` is qualitative: removing five open questions and a wrong merge target from the project's main
  process documentation removes a source of planner and executor error, but it is still documentation-only.
- `learning_value_pred = 3.0` mostly comes from Step 6's sweep calibration and Step 1's inventory.
- `EV = 0.80 * 70.0 + 0.5 * 3.0 - 0.3 * 3.6 - 12.5 = 56.00 + 1.50 - 1.08 - 12.50 = 43.92`.

**Strong assumptions:** the planning-time line numbers are approximately right on the execution branch base; the
same-second collision and field-name open questions are either absent from the touch set or easily found by Step 1's
grep; the write-guarantee wording in `docs/ledger_schema.md` sits outside the child-intent-row paragraphs.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: work only on the execution branch under this plan; never commit to
    `main` or other intents' branches; the root intent alone goes to `main` with human approval (consistent with
    decision 4).
  - `holon-config/world/constraints.md` section 3: ledgers are not edited; only their documentation changes, and no
    fourth ledger is introduced.
  - `holon-config/world/ruleset.md` section 3: no tests or test assertions are modified.
  - `docs/safety.md` principles 1 and 5: only root intents need human review to merge; the new docs make that explicit
    for nested parents too.
  - Intent constraint 7: touch set of five files, sub-intent sections only in `docs/ledger_schema.md`, no edit to the
    ledger write-guarantee wording, Australian English, target-native paths, no harness references, Prettier and
    `uv run task lint-docs`.
- **Potential violations or edge cases:**
  - A ledger-schema hunk overlapping the concurrently owned write-guarantee wording.
  - Prettier write mode run repo-wide, reformatting files outside the touch set.
  - Docs claiming the current code already merges sub-intents automatically into the deciding node.
  - Contradicting `holon-config/world/constraints.md`, which says plan branches are created off the intent branch (still
    true; only sub-intent bases change).
- **Mitigations built into the plan:** hunk-location check in Steps 4 and 6; Prettier on touched files only (Step 7);
  the framing rule with a single current-implementation note; the Step 6 sweep with a paraphrase grep; stable section
  anchors.
- **Residual risk accepted (and why):** a reviewer may prefer no current-implementation note at all; it is one sentence
  in two places and easy to remove in review, while omitting it would misdocument the code.
- **Allocated Entropy Budget:** 6.0
- **Predicted Plan Entropy:** 3.6
- **Budget Compliance:** The strategy fits within budget (3.6 of 6.0) and is executed as one plan; there is no
  sub-intent list, matching the intent's explicit instruction not to split.
