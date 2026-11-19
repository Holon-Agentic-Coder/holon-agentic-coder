# Plan for I-1794391536-refcap

- **Plan ID:** P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-sonnet-5-5 (version: 2.1.302)
- **Created At:** 2026-11-11T10:05:45.544Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** None. One refinement: the existing child branch builder is `_child_intent_branch`
  in `flow/child_progress.py` (`{parent}--{n}-{slug}`), so the plan routes that builder through the new function instead
  of inventing a new nesting.
- **Exploration stance:** balanced. The limit, the segment order and the digest rule are fully specified, so most steps
  are exploit. One step explores how many creation sites bypass a shared builder.
- **Safety priority level:** elevated
- **Priority Justification:** The change touches every branch-name creation site and the append-only ledger rows
  (`holon-config/world/constraints.md` section 1 Git Flow and section 3 Ledger Immutability). A name that changes for an
  input that already fits would break resume, checkpoints and ledger lookups, so the unchanged-if-fits invariant is
  guarded explicitly.

## Exploration

- **Proportion of steps that are exploratory:** 0.15
- **Justification:** The design is prescribed by the intent. Only the call-site audit (Step 1) is exploratory, because
  name creation is spread over `entrypoint/intent_creator.py`, `entrypoint/planner.py`, `entrypoint/executor.py`,
  `flow/stages/intent.py`, `flow/stages/plan.py`, `flow/stages/execute.py`, `flow/stages/calibrate.py`,
  `flow/stages/children.py`, `flow/child_progress.py` and `calibration.py`.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 8.6   |
| impact_pred         | 85.0  |
| cost_pred           | 24.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 42.7  |

### Strategy Rationale

The intent is high value (the largest, decomposed work is exactly the work that fails at its final push) and bounded
(one pure naming module plus call-site rewiring). I accept it as one plan rather than a sub-intent list: the predicted
entropy is 8.6, inside the 15.0 budget, and the steps share one new module, so splitting would add rebase overhead for
no isolation gain.

Overall metrics: p_success is the product over the two bottleneck steps (Step 2 at 0.90 and Step 3 at 0.85) tempered by
the independent steps, which gives about 0.78. Entropy is the sum of step entropies (0.8 + 1.8 + 2.2 + 1.2 + 1.4 + 0.8
\+ 0.4 = 8.6). Cost is the sum of step costs. Impact is the qualitative judgement of Step 3, as it removes a
flow-stopping failure. EV uses `EV = P * Impact + mu * LearningValue - lambda * Delta_S - Cost` with `mu = 0.5` and
`lambda = 0.3`: `EV = 0.78 * 85.0 + 0.5 * 6.0 - 0.3 * 8.6 - 24.0 = 66.3 + 3.0 - 2.58 - 24.0 = 42.72`. The overall table
uses 42.7.

Assumptions: `holon-config/world/constraints.md` section 1 forbids nothing here; the branch grammar is unchanged for
names that fit. The calibration branch is written as `{exec}/calibrated`, so every name is measured as if it ended in
`/calibrated` (9 characters longer than `/_`), giving a bare maximum of 255 - 11 = 244 characters including that suffix
check, as the intent states.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:** `holon-config/world/constraints.md` section 1 (branch prefix
  grammar `I-/P-/E-` with `/_`), section 3 (append-only ledgers); `docs/safety.md` (git is the safety boundary; only
  humans merge); `scripts/check_flow_module_budgets.py` and `flow_module_budgets.json` (line budgets).
- **Potential violations or edge cases:**
  - Changing a name that already fits (breaks resume, checkpoints, ledger joins).
  - Digest collision between long slugs that share a prefix.
  - Resume recomputing a name with a different rule and orphaning a branch.
  - Suffix logic scattered as `removesuffix("/_")` / `rstrip("/_")` (the latter strips characters, not a suffix).
  - Ledger rows must stay append-only; no rewriting of historical rows to add original text.
  - `ledger.py` is 1028 lines and `executor.py` is large; growth must be avoided.
- **Mitigations built into the plan:** new focused module `flow/ref_names.py` (pure, no I/O); golden tests that fit
  names are byte-identical; resume reads recorded names from checkpoint and ledger and never calls the shortener;
  original text is added only as new optional fields on newly appended rows; budget entry for the new module added at
  creation size.
- **Residual risk accepted (and why):** The real GitHub limit is not re-measured in tests (hermetic, no network); the
  intent supplies the measured value and the docs record it as measured.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 8.6
- **Budget Compliance:** The strategy fits within budget.

## Plan Description & Strategy

Add one pure module, `flow/ref_names.py`, that owns `MAX_REF_LENGTH = 255`, `REF_PREFIX = "refs/heads/"`,
`CALIBRATION_SUFFIX = "/calibrated"`, a `measure` helper and a `fit_branch_name` builder. Route every creation site
through it, add original-text fields to the ledger rows, add named failure at Stage 1 and a named push failure, then add
tests and docs. Order: audit, module, wiring, failure paths, ledger fields, tests, docs and budgets.

---

## Step 1: Audit every branch-name creation site and fix the segment model

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only and cheap; its output is the exact call-site list that sizes Step 3.
- **Step Type:** INFO_GATHERING
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Name creation sites number about ten and all derive from `intent_branch`, `plan_prefix`
  and `exec_id` strings, so one builder can cover them.
- **Learning target:** Whether any site (for example `entrypoint/executor.py` around line 1300, or `calibration.py`
  line 1211) builds names independently of `flow/stages`, and how plan and execution ids embed the agent and model
  labels (`ledger.py` lines 90 and 107).
- **Maximum acceptable cost for this learning:** about 10 percent of total plan cost.

### Intent & Git Integration

- **Step Intent:** Produce a verified list of creation sites and the segment grammar before writing code.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-audit/\_ (or part of the
  main execution branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Grep for branch construction in `entrypoint/intent_creator.py`, `entrypoint/planner.py`, `entrypoint/executor.py`,
  `flow/stages/{intent,plan,execute,calibrate,children}.py`, `flow/child_progress.py` and `calibration.py`.
- Record the segment grammar: intent `I-{ts}-{slug}`, child `{parent}--{n}-{slug}`, plan
  `P-{ts}[-{8hex}]-{agent}-{model}`, execution `E-{ts}[-{8hex}]-{agent}-{model}`, plus `/_` or `/calibrated`.
- Note which sites persist names (checkpoint, ledger) so resume can reuse them.
- Confirm the depth limit of two below the root and that a child's plan segments nest in the child intent branch.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (git boundary), world constraints section 1.
- **Potential failure modes for this step:** missing a site that builds a name by string concatenation.
- **Guardrails and early‑abort checks:** a test in Step 6 greps the package for `"/_"` constructions outside the new
  module and allow-lists the rest.

### Success & Discard Criteria

- **Success:** a complete site list with file and line, and the segment grammar confirmed.
- **Discard:** none; if more than 15 sites exist, re-plan Step 3 as a sub-intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.8   |
| impact_pred         | 20.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 7.0   |
| ev_pred             | 20.7  |

### Step Metrics Rationale

`EV = 0.97 * 20.0 + 0.5 * 7.0 - 0.3 * 0.8 - 2.0 = 19.4 + 3.5 - 0.24 - 2.0 = 20.66`. Read-only, low risk, high learning.

---

## Step 2: Add the pure ref-name module with the limit, measurement and deterministic shortening

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure functions with no I/O and an exact specification; one focused module, testable in isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Give the engine one documented limit and one function that fits a name under it.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-refnames/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Create `flow/ref_names.py` with documented constants: `MAX_REF_LENGTH = 255`, `REF_PREFIX = "refs/heads/"`, the
  `/calibrated` suffix, and the derived `MAX_BRANCH_LENGTH = 244` (255 minus 11 prefix characters), with a docstring
  stating the 255 accepted and 256 rejected measurement.
- Define a small immutable structure for a branch's segments: root intent segment (timestamp and slug), a list of child
  segments (index and slug), then plan and execution segments each with timestamp, optional 8-character id, agent label
  and model label.
- `measured_length(segments)` renders the name with the `/calibrated` suffix and the prefix and returns the length, so
  every name is checked as if it ended in `/calibrated` for the full-ref comparison. State the effective rule in the
  docstring: the rendered name is measured with `/calibrated`, and the bare name with the `/_` suffix is also within
  limit by construction.
- `fit_branch_name(segments)`: if the measured length is within the limit, return the unshortened rendering untouched.
  Otherwise shorten in this order, stopping as soon as it fits: (a) each child slug, deepest first, (b) agent and model
  labels in the nested plan and execution segments, (c) the root intent slug. A cut part is replaced by a prefix of the
  original text plus a 6-character hexadecimal digest of the full original text (sha256, first 6 hex characters); a
  segment is cut only as far as needed, and never below `digest-only` length.
- Timestamps and 8-character identifiers are never touched. If the name still exceeds the limit with every shortenable
  segment at its minimum, raise `RefTooLongError` carrying the limit, computed length and the name of the segment that
  cannot be shortened.
- Return a result object holding the final name, and a mapping of original text per shortened segment, for ledger
  recording.
- Keep the module under about 200 lines; add its entry to `flow_module_budgets.json` at creation size.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step depends on it).

### Safety & Constraint Considerations

- **Relevant rules:** world constraints section 1; the repository ruff limits (`C901 <= 10`, `PLR0913 <= 5`).
- **Potential failure modes for this step:** off-by-one at 244/245; digest collisions if the digest is computed from the
  shortened text rather than the original; non-determinism from set ordering.
- **Guardrails and early‑abort checks:** boundary tests at 243/244/245 and 254/255/256 written alongside; digest taken
  from the original text only; keep the helper functions small to stay inside complexity limits without `# noqa`.

### Success & Discard Criteria

- **Success:** the module passes its unit tests and ruff, and fitting names are byte-identical.
- **Discard:** if the shortening cannot guarantee fit for the real failing case with the specified order, stop and
  revisit the minimum segment lengths.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.8   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 51.1  |

### Step Metrics Rationale

`EV = 0.90 * 60.0 + 0.5 * 4.0 - 0.3 * 1.8 - 4.0 = 54.0 + 2.0 - 0.54 - 4.0 = 51.46`, rounded to 51.5 in the table below.
Specification is exact and pure, so risk is low.

---

## Step 3: Route every branch-name creation site through the builder

- **Sub‑intent recommendation:** YES
- **Reasoning:** Widest blast radius and the bottleneck for correctness. It touches about ten modules and decides
  whether resume keeps its names. If the Step 1 audit shows more than ten sites it should become its own child intent.
- **Step Type:** REFACTOR
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Existing ids are already carried in checkpoints and ledger rows, so resume can reuse
  recorded names with no recomputation.
- **Learning target:** Where a name is first persisted, and whether any lookup relies on a name suffix (`rstrip("/_")`
  call sites) that would break on a digest.
- **Maximum acceptable cost for this learning:** 1.2 times the step cost_pred.

### Intent & Git Integration

- **Step Intent:** Make the root, plan, execution, calibration and child (to depth three) names go through
  `fit_branch_name`, with resume keeping stored names.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-wiring/\_
- **Sub‑intent:** NEW (conditional on the Step 1 audit)

### Implementation Details (No code blocks, only logic/steps)

- Root intent: `entrypoint/intent_creator.py` and `flow/stages/intent.py` build `I-{ts}-{slug}` through the builder; an
  existing recorded `branch` in `intent_data` is used as is.
- Plan: `entrypoint/planner.py` and `flow/stages/plan.py` build `{intent}/{plan_id}/_` through the builder. Plan ids
  built in `ledger.py` keep their form when they fit; shortening applies to the label portion at the builder, with the
  original labels recorded.
- Execution: `entrypoint/executor.py` and `flow/stages/execute.py` build `{plan}/{exec_id}/_` through the builder; the
  partial-branch resume path (`_resumable_partial_branch`) keeps the stored name.
- Calibration: `flow/stages/calibrate.py` and `calibration.py` build `{exec}/calibrated` through the builder; because
  the exec name was already measured with `/calibrated`, this never fails after execution.
- Children: `flow/child_progress.py::_child_intent_branch` shortens the child slug through the builder; the progress
  entry already persists `intent_branch`, so a resumed run keeps it. Child depth three nests parent segments and is
  measured with the same function.
- Replace the character-set `rstrip("/_")` uses on touched paths with `removesuffix("/_")` where a digest could end in
  an underscore-like character; the digest is hexadecimal so this is a safety tidy rather than a fix.
- Keep `flow/__init__.py`, `stages/execute.py` (396 lines) and `stages/children.py` within their budgets; move any new
  logic into `ref_names.py`.
- Add no imports from `entrypoint/` into `flow/`; the entrypoints import the builder from `flow.ref_names`.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES. If a site is missed the original failure can recur.

### Safety & Constraint Considerations

- **Relevant rules:** world constraints section 1 (branch grammar), section 3 (ledgers); the flow line budgets.
- **Potential failure modes for this step:** a fitting name changes; resume recomputes; a lookup that parses the branch
  by splitting on `/` misreads a shortened segment; budgets exceeded.
- **Guardrails and early‑abort checks:** run `uv run pytest` on the affected test files after each site; a fitting-name
  golden test; the full suite once at the end.

### Success & Discard Criteria

- **Success:** all sites route through the builder; existing tests pass unchanged.
- **Discard:** if existing tests need their expected names changed for fitting inputs, stop; that violates the unchanged
  invariant.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.2   |
| impact_pred         | 80.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 64.7  |

### Step Metrics Rationale

`EV = 0.85 * 80.0 + 0.5 * 6.0 - 0.3 * 2.2 - 6.0 = 68.0 + 3.0 - 0.66 - 6.0 = 64.34`, rounded to 64.3 in the table.
Cross-module work with resume semantics is where most residual risk sits.

---

## Step 4: Early named failure at Stage 1 and a named push rejection

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small and contained once the builder raises `RefTooLongError`.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Fail before any branch exists when a name cannot fit, and report an over-length push rejection by
  name without discarding work.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-failure/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Stage 1 (`flow/stages/intent.py`) computes the worst-case names up front: the root intent, the longest plan and
  execution segments from the configured agent and model labels, and the calibration suffix. On `RefTooLongError` it
  returns a failed stage result before any branch or worktree is created, with a message naming the limit (255), the
  computed length and the segment that cannot be shortened.
- In `entrypoint/executor.py`, where the push is attempted, detect the `ref too long` and `pre-receive hook declined`
  output (case-insensitive) or a name over the limit, and report `ref too long: branch <name> (<n> characters)` as the
  failure reason. Keep the existing `UNPUSHED EXECUTION PATCH` printing so the work is not discarded.
- Map that outcome to the existing failed-stage path rather than a bare executor exit code, keeping the executor
  function inside the complexity limits by extracting a helper into `ref_names.py` or a small new module.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` (failures contained to branch); the existing unpushed-patch protections.
- **Potential failure modes for this step:** the early check being stricter than the later builder; swallowing unrelated
  push errors as ref errors.
- **Guardrails and early‑abort checks:** share one function for the Stage 1 check and the builder; match only the
  specific push message text.

### Success & Discard Criteria

- **Success:** the impossible-name test fails at Stage 1 with no branch created; a fake push rejection prints the named
  message and the patch.
- **Discard:** if executor change needs a refactor larger than about 60 lines, move the helper out to a new module.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.2   |
| impact_pred         | 55.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 46.5  |

### Step Metrics Rationale

`EV = 0.88 * 55.0 + 0.5 * 3.0 - 0.3 * 1.2 - 3.0 = 48.4 + 1.5 - 0.36 - 3.0 = 46.54`.

---

## Step 5: Record the original slug and labels in the three ledger rows

- **Sub‑intent recommendation:** NO
- **Reasoning:** Additive optional fields on new rows; no history is rewritten.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The ledger schema tolerates new optional string fields without a schema or version
  change.
- **Learning target:** Whether `ledger.py` validation or `docs/ledger_schema.md` needs an update.
- **Maximum acceptable cost for this learning:** about 1 cost unit.

### Intent & Git Integration

- **Step Intent:** Make the original unshortened text recoverable from the intent, plan and execution rows.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-ledger/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add optional fields written only when a shortening happened: the original `slug` is already stored on the intent row,
  so add `original_slug` only when it differs from the used one, and `original_agent` and `original_model` on plan and
  execution rows. Rows for names that fit are byte-identical to today.
- Keep exactly the three append-only ledgers (`intents.jsonl`, `plans.jsonl`, `executions.jsonl`); add no file.
- Keep the `ledger.py` change minimal (it is already 1028 lines); pass the result mapping from the builder rather than
  adding logic there. Update `docs/ledger_schema.md` for the optional fields.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** world constraints section 3; `tests/test_ledger_append_only.py`.
- **Potential failure modes for this step:** row key order or content changing for fitting names; breaking the
  append-only test.
- **Guardrails and early‑abort checks:** assert fitting-name rows are identical; run `tests/test_ledger_append_only.py`.

### Success & Discard Criteria

- **Success:** shortened-name rows carry the originals; others are unchanged.
- **Discard:** if schema validation rejects the fields and needs a version bump, defer to a sub-intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.4   |
| impact_pred         | 45.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 39.1  |

### Step Metrics Rationale

`EV = 0.90 * 45.0 + 0.5 * 4.0 - 0.3 * 1.4 - 3.0 = 40.5 + 2.0 - 0.42 - 3.0 = 39.08`.

---

## Step 6: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are written step by step beside the code; no network and no new dependencies.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the acceptance criteria with hermetic tests.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add `tests/test_ref_names.py` (and a small flow-level test file if the first nears 400 lines) covering:
  - the real failing case: a 56-character slug, four sub-intents, labels `claude-agent` and `claude-sonnet-5-5`; every
    name at every depth is at most 255 characters with the prefix and 255 with `/calibrated`;
  - boundary cases built by padding a slug: 243, 244 and 245 characters without the prefix and 254, 255 and 256 with it;
    a name that fits is unchanged and the first one over the limit is shortened;
  - determinism (same input, same output across calls); no collision between two long slugs sharing a long prefix;
  - depth three children; the three-segment order (child slug before labels before root slug);
  - a resumed flow with a checkpoint keeps its existing names even if the shortening rule is patched to differ;
  - ledger rows record the original slug and labels, and fitting rows are unchanged;
  - an impossible name fails at Stage 1 with the limit, length and segment named and no branch created (use a git
    fixture from `tests/worktree_fixtures.py` or `hermetic_fixtures.py`);
  - a fake push rejection with `ref too long` is reported by name and the unpushed patch is kept;
  - a static check that no module outside `ref_names.py` builds a branch with a literal `"/_"` suffix except on an
    allow-list.
- Run affected files first with `uv run pytest <paths>`, then the full suite once with `uv run task test`. No repetition
  is needed, since the intent has no timing target.

### Dependencies & Criticality

- **Depends on:** Steps 2 to 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `tests/test_sandbox_hermetic_guard.py`, `tests/gh_write_guard.py`.
- **Potential failure modes for this step:** accidental network or `gh` write; brittle length arithmetic.
- **Guardrails and early‑abort checks:** use the existing hermetic fixtures; compute lengths with the module constants
  rather than literals.

### Success & Discard Criteria

- **Success:** all new tests and the full suite pass.
- **Discard:** none; failures feed back into Steps 2 to 5.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.8   |
| impact_pred         | 50.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 42.3  |

### Step Metrics Rationale

`EV = 0.88 * 50.0 + 0.5 * 5.0 - 0.3 * 0.8 - 4.0 = 44.0 + 2.5 - 0.24 - 4.0 = 42.26`.

---

## Step 7: Documentation, budgets and lint

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, low risk, and it closes the criteria on lint and docs.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Document the limit and rules; keep lint, doc lint and line budgets green.
- **Git branch:** I-1794391536-refcap/P-1794391544-abdc9b6d-claude-agent-claude-sonnet-5-5/E-docs/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

- Add a section to `docs/git_flow.md` (Australian English) giving: the measured limit (255 accepted, 256 rejected,
  prefix counted, so 244 without the prefix), the `/calibrated` rule (9 characters more than `/_`), the shortening
  order, the 6-character digest, which segments are never touched, the resume rule, the ledger fields and the Stage 1
  and push failure messages.
- Add the `flow/ref_names.py` entry to `flow_module_budgets.json`. Do not raise any other budget; if a touched module
  would exceed its budget, move code into `ref_names.py` instead. Record a reason for any raised budget in the JSON
  description or the commit message.
- Run `uv run task lint` and `uv run task lint-docs`, fixing issues; run the full suite once with `uv run task test`.

### Dependencies & Criticality

- **Depends on:** Steps 3 to 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** budget gate `scripts/check_flow_module_budgets.py`; Australian English.
- **Potential failure modes for this step:** prettier or docs lint oscillation; a budget breach.
- **Guardrails and early‑abort checks:** run both lint tasks before finishing.

### Success & Discard Criteria

- **Success:** both lint tasks and the full suite pass.
- **Discard:** none.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.4   |
| impact_pred         | 30.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 27.4  |

### Step Metrics Rationale

`EV = 0.95 * 30.0 + 0.5 * 2.0 - 0.3 * 0.4 - 2.0 = 28.5 + 1.0 - 0.12 - 2.0 = 27.38`.

---
