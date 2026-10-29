# Plan for I-1793246459-plain-language-entropy-and-expected-value-explainer

- **Plan ID:** P-1793246475-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-29T04:01:16.029Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The planner makes the one choice the intent leaves open: the explainer goes
  on a separate page, `docs/metrics-explained.md`, linked from the top of `docs/metrics.md`, with back-links from the
  formal sections.
- **Exploration stance:** balanced, leaning conservative. The deliverable is documentation only, with a fixed touch set
  and a strict entropy budget of 3.5, so almost all of the work is exploit. One cheap exploratory step, a cold-read
  comprehension test, checks the intent's acceptance criterion ("a reader ... must be able to answer, after one read")
  directly instead of assuming it.
- **Safety priority level:** standard
- **Priority Justification:** No code, config, test or ledger content changes. The relevant constraints are
  `holon-config/world/constraints.md` §3 (ledgers are append-only: the explainer only _cites_ an existing `plans.jsonl`
  row and never edits it), §1 (commit boundaries: only the files in the touch set), and the intent's touch set (only
  `docs/metrics.md` and one new `docs/metrics-explained.md`). None of these raises the risk above standard.

## Exploration

- **Proportion of steps that are exploratory:** 0.25 (1 of 4 steps)
- **Justification:** The main uncertainty is not whether the files can be written but whether the text actually makes
  sense to a reader who only knows arithmetic. A cold read by a fresh agent with no context gives cheap evidence (about
  1.0 cost unit) on that, and the result can be reused when planning later "explainer" intents. Everything else is plain
  exploitation of known facts.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.6   |
| impact_pred         | 60.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 45.32 |

`EV = 0.88 * 60.0 + 0.5 * 2.0 - 0.3 * 1.6 - 8.0 = 52.80 + 1.00 - 0.48 - 8.00 = 45.32`

### Strategy Rationale

**Choice of layout (separate page, not a lead-in section).** The intent lets the planner choose. A separate page is
better for these reasons:

1. The `docs/metrics.md` diff stays tiny and purely additive: one pointer paragraph under the title and three one-line
   back-links. The 600-line formal reference is left exactly as it is, so "keep the existing formal reference intact" is
   easy to check with `git diff`. A smaller diff also lowers Conflict Likelihood if a concurrent change touches the same
   file.
2. The rule "no LaTeX in the plain-language part" is checked at file level (the new page has no `$` characters at all).
   In the other layout, a lead-in section would sit just above `$$` blocks in the same file.
3. A reader is sent to the simple page first. They are not made to scroll past it on every visit to the formal page.
4. It costs at most one new file, which the touch set allows explicitly.

**Primary-source facts the explainer must state (all checked during planning):**

- `holon-config/metrics/ev_config.json`: `lambda` 0.3, `mu` 0.5. The planner entrypoint loads this file (with defaults
  if it is missing).
- `holon-config/metrics/entropy_config.json`: weights 0.30 / 0.25 / 0.20 / 0.15 / 0.10 for SSA / IRR / CL / SER / NOV.
  Its `_note` says "Reserved for future entropy computation integration. Not yet loaded by the runtime." The explainer
  must say this plainly: today the entropy number is the planner's own overall estimate, informed by these five
  ingredients, not a weighted sum the runtime computes.
- `apps/sandbox-executor/src/sandbox_executor/budgets.py`: `DEFAULT_ENTROPY_BUDGET = 15.0`, used when neither the plan
  row nor the intent row carries a budget. `MAX_REPLAN_ATTEMPTS = 2`: an over-budget plan with no sub-intent list is
  sent back for re-planning and fails after the bounded attempts. `MAX_SUB_INTENT_DEPTH = 2`.
- `holon-config/prompts/planner.template.md` (Entropy Budget and Decomposition Contract): a plan at or below its budget
  with no `## Sub-intent list` runs as one plan. A plan over the budget must add that list, and each child runs through
  the full pipeline one at a time.
- Worked example, ledger row verified (exactly one row matches in `holon-knowledge/ledger/plans.jsonl`):
  `p_success 0.7, impact 90.0, learning_value 6.0, entropy 10.6, cost 21.5, ev 41.32, entropy_budget 14.0`. Arithmetic:
  `0.7 * 90 = 63`; `0.5 * 6 = 3`; `0.3 * 10.6 = 3.18`; `63 + 3 - 3.18 - 21.5 = 41.32`; `10.6 <= 14.0`. The row's other
  fields (intent branch, dates, plan file) must **not** be quoted. The example is identified only by its plan id.

**Aggregation of metrics.**

- `entropy_pred` is the sum of the step entropies: `0.9 + 0.5 + 0.1 + 0.1 = 1.6`. That is well inside the budget of 3.5,
  with more than 50 % headroom.
- `cost_pred` is the sum of the step costs: `4.5 + 1.5 + 1.0 + 1.0 = 8.0`.
- `p_success_pred` is a qualitative judgement anchored on the product of the gating steps 1 to 3
  (`0.90 * 0.95 * 0.95 = 0.81`), raised to 0.88. Failures in steps 2 and 3 are almost always prettier wrapping or a
  wording fix that can be repaired in the same run, so they are rarely terminal. Step 4 is advisory and not a gate.
- `impact_pred` 60 is a qualitative judgement. This is the only document that makes the flow's accept, refuse and split
  decisions readable without maths, which matters for onboarding and for reviewing plans. It is not a behaviour change,
  so it is scored below the 90+ given to runtime fixes.
- `learning_value_pred` 2.0 comes mostly from the cold-read test (step 4) and from confirming that the
  weights-not-loaded caveat can be stated without confusion.
- **Bottleneck step:** Step 1, the explainer page. It carries most of the impact and most of the risk: a wrong number,
  an overstatement of the runtime, or a leaked identifier for the example.

**Why there is no sub-intent list.** The intent's own Q&A says "One intent". The predicted entropy of 1.6 is far under
3.5, and every step is small and touches at most one file. Splitting would add pipeline cost with no reduction in risk.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 Commit Boundaries: commit only to this intent's own execution branch and only
    the files in the touch set.
  - `holon-config/world/constraints.md` §3 Ledger Immutability: the example row is read and cited, never edited. Only
    the flow itself appends its own rows.
  - `holon-config/world/ruleset.md` §3 Testing Constraints: no test files are touched. Tests run only via
    `uv run task test` from the repository root.
  - `docs/safety.md` principle 4 (entropy is a safety signal): the plan stays well within its budget of 3.5.
  - Intent constraints: Australian English; no new dependencies; target-native paths only (no harness directories, bean
    ids or review ids); `uv run task lint` and `uv run task lint-docs` must pass; the touch set is limited to
    `docs/metrics.md` and `docs/metrics-explained.md`.
- **Potential violations or edge cases:**
  - Accidentally naming the example by its intent branch or slug, which appear in the same ledger row.
  - Overstating the runtime, for example writing "the runtime computes entropy from the weights".
  - Copying the formula with LaTeX (`$$`) into the explainer, or stating the EV formula more than once in each form.
  - US spellings slipping in ("behavior", "normalize", "learned").
  - Prettier `proseWrap: always` at 120 columns reflowing the text. This is harmless, but `lint-docs` fails if the
    formatter was not run.
  - Wanting to "also fix" `holon-config/metrics/README.md`, `README.md` or `docs/budgets.md`. These are out of scope:
    they may be linked to but must not be edited.
  - Editing or reformatting existing lines of `docs/metrics.md`, which risks a conflict with concurrent work and breaks
    the "formal reference intact" rule.
- **Mitigations built into the plan:**
  - Step 3 has explicit grep and diff gates: `git diff --name-only` must list only the two allowed files;
    `git diff docs/metrics.md` must contain only added lines; the new file must contain no `$`, no `.beans`, `todo/` or
    `.agents` strings, and no slug for the example's intent; and it must not contain the forbidden US spellings.
  - Every number in the explainer is checked against the source files named in the Strategy Rationale.
  - Before committing, run prettier with `--write` on only the two files.
- **Residual risk accepted (and why):** Readability is subjective. The cold read (step 4) lowers this risk but cannot
  remove it, and a human reviewer is the final judge at merge to `main`. A small chance of conflict on `docs/metrics.md`
  also remains. It is accepted because the diff is additive and short.
- **Allocated Entropy Budget:** 3.5
- **Predicted Plan Entropy:** 1.6
- **Budget Compliance:** The strategy fits within budget (`1.6 <= 3.5`), so the plan runs as one plan with no sub-intent
  list.

## Plan Description & Strategy

Add a new plain-language page, `docs/metrics-explained.md`, that a reader with high-school arithmetic can follow. It
explains what entropy and EV mean in Holon, how they relate, and what the entropy budget does, with one worked example
taken from the real ledger. Then add a short pointer at the top of `docs/metrics.md` and back-links in its formal EV,
entropy and quick-reference sections, without changing any existing line. Next, check every number against its source
and run the gates (formatter, lint, lint-docs, tests, touch set). Finally, have a fresh agent read only the new page and
answer the intent's three comprehension questions as a cold-read test, and revise the wording if any answer is wrong.

Execution happens on one execution branch under this plan branch. There are four steps and no sub-intents.

---

## Step 1: Write the plain-language explainer page `docs/metrics-explained.md`

- **Sub‑intent recommendation:** NO
- **Reasoning:** One new documentation file, no runtime effect and easily reverted. Turning it into a sub-intent would
  add pipeline cost and no reduction in risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Create the explainer that meets goals 1(a) to 1(d), 2 and 3 of the intent, with every fact matching
  the primary sources.
- **Git branch:**
  `I-1793246459-plain-language-entropy-and-expected-value-explainer/P-1793246475-claude-agent-claude-opus-5-5/E-{timestamp}-docs-explainer/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `docs/metrics-explained.md` with an H1 such as "Entropy and Expected Value, Explained Plainly". Directly under
   it, add one sentence saying this is the plain-language companion to [Metrics](metrics.md), which stays the formal
   reference, and that the page assumes nothing beyond arithmetic.
2. Add a section **"The short version"** with three to five sentences that answer the three target questions up front:
   entropy is a mess-and-risk score; EV is a worth-doing score; entropy is subtracted inside EV, so more mess means a
   lower score.
3. Add a section **"What entropy means here"** (goal 1a):
   - Give the everyday meaning: a score for how much mess and risk a change creates in the shared codebase. Say that it
     is not the physics or information-theory meaning.
   - Analogy: building LEGO in a shared room. Follow it at once with the precise sentence it stands for: "Precisely:
     entropy is a non-negative score for the disorder and risk a plan is expected to add to the repository and to the
     work of other agents."
   - Explain the five ingredients in everyday words, each with its formal name and abbreviation: how much code and
     config it touches (State Surface Area, SSA); how hard it is to undo (Irreversibility, IRR); how likely it is to
     collide with other work (Conflict Likelihood, CL); how likely it is to break out of its safety sandbox (Sandbox
     Escape Risk, SER); how unfamiliar the territory is (Novelty, NOV).
   - State the documented weights in plain words (SSA 30 %, IRR 25 %, CL 20 %, SER 15 %, NOV 10 %). Then say plainly:
     "These weights are documented in `holon-config/metrics/entropy_config.json` but are not yet loaded by the runtime;
     today each plan's entropy number is the planner's own estimate, made with these five ingredients in mind."
   - Say that a higher number means more mess, and that zero is the floor.
4. Add a section **"What Expected Value (EV) means here"** (goal 1b):
   - Give the everyday meaning: a single score for whether a plan is worth doing.
   - List the five parts in plain words: chance of success, the good it does if it works (impact), what the system
     learns even if it fails (learning value), the mess it makes (entropy), and what it costs (time, compute, tokens).
   - State the formula **once in words**: "EV equals the chance of success times the impact, plus half the learning
     value, minus three tenths of the entropy, minus the cost." Then state it **once in symbols** inside a code span:
     `EV = P(success) * Impact + mu * LearningValue - lambda * entropy - Cost`. Say that `mu` is 0.5 and `lambda` is
     0.3, both read from `holon-config/metrics/ev_config.json`. Do not repeat the formula anywhere else on the page.
   - Analogy (for example, deciding whether a weekend project is worth it), followed by its precise sentence:
     "Precisely: when several plans compete for the same intent, the one with the higher EV is preferred."
5. Add a section **"How entropy and EV fit together"** (goal 1c):
   - Entropy sits inside EV as a penalty, so more predicted mess gives a lower EV when everything else is equal.
   - The aim is the most value for the least mess, not zero mess: a plan with some mess and a large impact can still
     win.
   - Valuable but messy work is split into smaller pieces (sub-intents) instead of being abandoned. Give one analogy
     (moving house box by box, not carrying the wardrobe in one go) followed by its precise sentence.
   - Answer "why does high entropy lower EV?" explicitly in one sentence: entropy is subtracted, so every extra point of
     entropy takes 0.3 off EV.
6. Add a section **"What the entropy budget does"** (goal 1d):
   - Each intent carries a mess allowance, `entropy_budget`. If neither the plan nor the intent sets one, the default is
     15.0 (`DEFAULT_ENTROPY_BUDGET` in `apps/sandbox-executor/src/sandbox_executor/budgets.py`).
   - A plan predicted at or below its budget runs as one plan.
   - A plan predicted above its budget must list sub-intents (a `## Sub-intent list` section) before it can execute.
     Without that list it is sent back for re-planning and fails after a small, fixed number of attempts. Each listed
     child then runs through the full pipeline, one at a time, and children nest at most two levels deep.
   - Analogy (a spending allowance), followed by its precise sentence.
   - Link to [Turn and Stage Budgets](budgets.md#entropy-budget) for how the budget is resolved and enforced. This is a
     link only; `docs/budgets.md` is not edited.
7. Add a section **"A worked example from this repository's ledger"** (goal 2):
   - Introduce it as plan `P-1793223729-claude-agent-claude-opus-5-5`, recorded in `holon-knowledge/ledger/plans.jsonl`.
     Identify it by plan id **only**: no intent name, branch, slug, date or description of what it did.
   - Give a small table of its recorded values: chance of success 0.7, impact 90.0, learning value 6.0, entropy 10.6,
     cost 21.5, entropy budget 14.0, recorded EV 41.32.
   - Work it through line by line in code spans: `0.7 * 90 = 63`; `0.5 * 6 = 3`; `0.3 * 10.6 = 3.18`;
     `63 + 3 - 3.18 - 21.5 = 41.32`. Say that this matches the recorded EV.
   - Budget check: entropy 10.6 is within its budget of 14.0, so it could run as a single plan with no sub-intents.
   - Add one sentence on reading it: the 3.18 entropy penalty is small next to the 63 of expected good, so the mess was
     worth it. If the entropy had been above 14.0, the plan would have had to be split before it could execute.
8. Add a section **"Check your understanding"** with the three questions (what is entropy here, what is EV, why does
   high entropy lower EV?) and one-sentence answers. This makes the acceptance criterion visible to reviewers.
9. Add a section **"Where the formal definitions live"** with links back to `metrics.md`: EV
   (`metrics.md#12-expected-value-ev`), per-intent entropy (`metrics.md#21-meaning` and
   `metrics.md#22-predicted-entropy-formula-bootstrap`) and the quick reference (`metrics.md#11-quick-reference`). Link
   the factor definitions in `appendix.md`, but do not edit it.
10. Style rules for the whole page: Australian English ("behaviour", "normalised", "learnt", "favour", "recognise"); no
    `$` or LaTeX anywhere; put every number expression, file path and identifier in code spans; use short sentences, and
    define each term at first use; follow every analogy at once with a sentence beginning "Precisely:" (or similar) that
    states what it stands for. Target length is roughly 120 to 180 lines after prettier.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (it carries the deliverable; `p_success_pred` 0.90 is well above 0.6, so a sub-intent is not
  needed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (cite the ledger, never edit it); the intent's touch set
  and target-native-paths constraint; the intent's "do not overstate the runtime" rule.
- **Potential failure modes for this step:**
  - A number is copied wrongly, or the example is identified by more than its plan id.
  - The text implies the entropy weights are computed at run time.
  - The EV formula is stated twice in either form, or with LaTeX.
  - US spellings are used.
- **Guardrails and early‑abort checks:**
  - Before writing, re-read the five primary sources and copy the numbers from them, not from memory.
  - Do not touch any file other than `docs/metrics-explained.md` in this step.

### Success & Discard Criteria

- **Success:** `docs/metrics-explained.md` exists and covers sections 1a to 1d, the worked example with exact
  arithmetic, an analogy-plus-precise-sentence pair for each analogy, and links back to `metrics.md`. It contains no `$`
  characters and states the formula exactly once in words and once in symbols.
- **Discard:** Discard if a fact cannot be reconciled with a primary source (for example, if the ledger row has changed
  — it cannot, since the ledger is append-only, but if it were missing). Stop and report rather than invent figures.
  Also discard if the cost passes `1.5 * 4.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.9   |
| impact_pred         | 45.0  |
| cost_pred           | 4.5   |
| learning_value_pred | 1.5   |
| ev_pred             | 36.48 |

### Step Metrics Rationale

`EV = 0.90 * 45.0 + 0.5 * 1.5 - 0.3 * 0.9 - 4.5 = 40.50 + 0.75 - 0.27 - 4.50 = 36.48`. Entropy is 0.9: one new file
(small surface area), fully reversible, almost no conflict risk because the file is new, no sandbox risk, and low
novelty. Most of the plan's impact is here. The p_success of 0.90 reflects the risk of a subtle fact error or a leaked
identifier, both of which step 3 catches.

---

## Step 2: Link the explainer from the top of `docs/metrics.md` and add back-links from the formal sections

- **Sub‑intent recommendation:** NO
- **Reasoning:** A few added lines in one file; trivial and reversible.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Meet goal 4 (cross-link both ways and keep the formal reference intact) and the "linked from the top
  of `docs/metrics.md`" half of goal 1.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Directly after the `# metrics.md` title (before "This document defines the **bootstrap (day-zero) metrics**"), add
   one short paragraph, for example: "New to these ideas? Read
   [Entropy and Expected Value, Explained Plainly](metrics-explained.md) first. It explains in everyday language, with a
   worked example, what entropy and EV mean, how they relate and what the entropy budget does. This page is the formal
   reference."
2. At the end of §1.2 Expected Value (EV), after its existing Notes list, add one line: "Plain-language walkthrough with
   a worked example: [metrics-explained.md](metrics-explained.md#…)", pointing to the anchor of the worked-example
   section.
3. At the end of §2.1 Meaning (per-intent entropy), add one line linking to the explainer's entropy section.
4. Under §11 Quick Reference, after the "Bootstrap defaults" list, add one line linking to the explainer.
5. Do **not** modify, reorder or reformat any existing line. The diff for this file must contain only added lines.
   Compute the anchor slugs from the final headings written in Step 1, using GitHub's slug rules: lower case,
   punctuation dropped, spaces become hyphens.

### Dependencies & Criticality

- **Depends on:** Step 1 (the anchors come from its headings)
- **Is Bottleneck:** YES (the intent requires a link from the top of the formal page)

### Safety & Constraint Considerations

- **Relevant rules:** the intent's touch set (`docs/metrics.md` is allowed); "keep the existing formal reference
  intact".
- **Potential failure modes for this step:**
  - Prettier reflows a neighbouring existing paragraph. This is unlikely, because blank lines separate the new
    paragraphs.
  - An anchor typo produces a dead link.
- **Guardrails and early‑abort checks:**
  - Check that `git diff docs/metrics.md` shows only lines starting with `+` (apart from context lines).
  - If prettier changes an existing line, restructure the insertion as its own paragraph separated by blank lines.

### Success & Discard Criteria

- **Success:** There are four additive links in `docs/metrics.md` (top, §1.2, §2.1, §11), every link resolves to an
  existing heading, and no existing line has changed.
- **Discard:** Discard if the edit cannot be made without changing existing lines. In that case keep only the top
  pointer and record the dropped back-links in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 0.5   |
| ev_pred             | 8.10  |

### Step Metrics Rationale

`EV = 0.95 * 10.0 + 0.5 * 0.5 - 0.3 * 0.5 - 1.5 = 9.50 + 0.25 - 0.15 - 1.50 = 8.10`. Entropy is 0.5, higher than you
might expect for four lines, because `docs/metrics.md` is an existing, widely referenced file that concurrent changes
might also touch (Conflict Likelihood). Impact is modest on its own: it is the discovery path to Step 1's value.

---

## Step 3: Verify facts and constraints, then run the repository gates

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only checks plus formatter runs on the two touched files; no new risk.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that the change is accurate, inside the touch set and lint-clean before it is committed.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Fact check.** Compare each figure in the explainer with its source:
   - `ev_config.json` (0.3, 0.5).
   - `entropy_config.json` (five weights and the not-yet-loaded note).
   - `budgets.py` (`DEFAULT_ENTROPY_BUDGET = 15.0`).
   - The planner template's decomposition contract.
   - The single `plans.jsonl` row for the example plan id (all seven numbers).

   Recompute `63 + 3 - 3.18 - 21.5` and confirm it gives 41.32.

2. **Identification check.** Search the new page for the example's intent branch or slug, its date and its plan-file
   path. None of them may appear. Only the plan id may.
3. **LaTeX and formula check.** The new page contains no `$`. The symbolic formula appears exactly once, and the worded
   formula appears exactly once.
4. **Harness-path check.** Neither touched file contains `.beans`, `todo/`, `.agents`, a bean id or a review id in the
   added text.
5. **Spelling check.** Search the added text for common US spellings (behavior, normalize, color, favor, analyze,
   learned, center, organization) and replace them with the Australian forms.
6. **Touch-set check.** `git diff --name-only` plus `git status --porcelain` list only `docs/metrics.md` and
   `docs/metrics-explained.md` (the flow's own plan, execution and ledger records excepted).
7. **Formatting and gates.** Run prettier with `--write` on only the two files, then, from the repository root:
   `uv run task lint`, `uv run task lint-docs` and `uv run task test`. All must pass. Docs-only changes should not
   affect tests. If a test fails, check whether it also fails on the parent branch before attributing it to this change.
8. **Link check.** For each relative link in both files, confirm that the target file exists and the anchor matches a
   heading slug.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (a failed `lint-docs` blocks merge)

### Safety & Constraint Considerations

- **Relevant rules:** the intent's test-runner rule (`uv run task test` / `uv run pytest` from the root only, never
  `PYTHONPATH`); `holon-config/world/ruleset.md` §3 (no test edits).
- **Potential failure modes for this step:**
  - `lint-docs` fails on files outside the touch set because of concurrent work. In that case, report it and do not fix
    it.
  - A test fails for reasons that existed before this change.
- **Guardrails and early‑abort checks:**
  - Never run prettier `--write` repo-wide; run it only on the two files.
  - Never edit an off-limits file to make a gate pass. Record the problem in the execution notes instead.

### Success & Discard Criteria

- **Success:** All eight checks pass, and lint, lint-docs and the tests are green.
- **Discard:** Stop if a gate fails because of an off-limits file and cannot be resolved within the touch set. Report
  that in the execution notes rather than widening scope.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.1   |
| impact_pred         | 5.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 0.5   |
| ev_pred             | 3.97  |

### Step Metrics Rationale

`EV = 0.95 * 5.0 + 0.5 * 0.5 - 0.3 * 0.1 - 1.0 = 4.75 + 0.25 - 0.03 - 1.00 = 3.97`. These are mostly read-only checks,
so entropy is close to zero. The value of this step lies in protecting the bottleneck step's correctness more than in
its own impact.

---

## Step 4: Cold-read comprehension test of the explainer (exploratory)

- **Sub‑intent recommendation:** NO
- **Reasoning:** A cheap, read-only probe whose only output is optional wording fixes inside the same new file. It is
  the main source of learning value.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** A reader given only `docs/metrics-explained.md`, with no access to `docs/metrics.md`, the
  config or the code, can correctly answer: (1) what is entropy here, (2) what is EV, (3) why does high entropy lower
  EV, and can also redo the worked example's arithmetic.
- **Learning target:** Whether the acceptance criterion "after one read" holds in practice, and which sentences (if any)
  cause confusion. Examples are the weights-not-loaded caveat, the difference between "budget" and "penalty", and the
  role of `mu`. These findings are reusable for future explainer intents.
- **Maximum acceptable cost for this learning:** 1.0 cost unit (one short, read-only sub-agent run). Skip the step if
  the run budget is nearly exhausted, because it is not a gate.

### Intent & Git Integration

- **Step Intent:** Validate readability against the intent's stated success test, and tighten the wording if it fails.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Give a fresh, read-only sub-agent the text of `docs/metrics-explained.md` only. Ask it to answer the three questions
   in its own words, and to say whether a plan with entropy 16 and budget 15.0 could run as a single plan.
2. Mark each answer right or wrong against the source facts.
3. If any answer is wrong or vague, revise only the relevant sentences in `docs/metrics-explained.md`, then re-run Step
   3's prettier, `lint-docs` and identification checks.
4. Record the outcome (answers correct out of four, sentences revised) in the execution record under `executions/`. Do
   not write it to any other file.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO (advisory; the plan succeeds even if this probe is skipped)

### Safety & Constraint Considerations

- **Relevant rules:** the touch set (revisions are limited to `docs/metrics-explained.md`); never create auxiliary
  ledgers.
- **Potential failure modes for this step:**
  - The probe agent uses outside knowledge and passes too easily (a false positive).
  - Revisions reintroduce a fact error.
- **Guardrails and early‑abort checks:**
  - Tell the probe to rely only on the given text.
  - Any revision re-runs the Step 3 fact, identification and lint-docs checks.

### Success & Discard Criteria

- **Success:** 4/4 answers are correct, either first time or after one round of revision, and the gates are still green.
- **Discard:** If two revision rounds still leave a wrong answer, keep the best version that passes the gates and record
  the open confusion in the execution notes for a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 0.1   |
| impact_pred         | 3.0   |
| cost_pred           | 1.0   |
| learning_value_pred | 2.5   |
| ev_pred             | 2.62  |

### Step Metrics Rationale

`EV = 0.80 * 3.0 + 0.5 * 2.5 - 0.3 * 0.1 - 1.0 = 2.40 + 1.25 - 0.03 - 1.00 = 2.62`. p_success is lower (0.80) because it
is a genuine test that may fail and trigger a revision. Learning value is the highest of any step (2.5), because it
turns the intent's subjective acceptance criterion into an observed result. Entropy is about 0.1, since any change stays
inside a file already created in this plan.

---
