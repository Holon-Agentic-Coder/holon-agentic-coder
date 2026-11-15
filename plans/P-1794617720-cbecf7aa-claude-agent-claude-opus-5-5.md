# Plan for I-1794617704-touchset

- **Plan ID:** P-1794617720-cbecf7aa-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-11-14T00:55:21.366Z

## Planner Autonomy Summary

- **Intent handling:** SPLIT
- **Reframed intent (if applicable):** Same goals, delivered as two sequential child intents: (1) the touch-set
  estimate, its ledger field, its documentation and its calibration (precision and recall); (2) the Stage 3 admission
  gate with `--allow-overlap` and the post-Stage 3 out-of-set enforcement. The declared scope (`flow/`, `tests/flow/`,
  `docs/ledger_schema.md`) is widened by the minimum the goals require: the planner prompt template, the planner
  entrypoint that writes the `plans.jsonl` row, the `holon flow` CLI option, the calibration module and the flow module
  line budgets file (see `## Touch Set`).
- **Exploration stance:** balanced. The pure overlap logic is plain exploitation; the discovery of in-flight flows (open
  flow pull requests plus local checkpoints across git worktrees) is genuinely uncertain and gets one bounded
  exploratory step.
- **Safety priority level:** elevated
- **Priority Justification:** The change adds a hard gate in front of Stage 3 and a new failure path after it
  (`docs/safety.md` "Failure containment" and "Human review is the final gate"), writes a new kind of row to an
  append-only ledger (`holon-config/world/constraints.md` section 3, Ledger Immutability) and must never call real `gh`
  from tests. A false block halts every concurrent flow; a false pass silently loses the 10M-token saving.

## Exploration

- **Proportion of steps that are exploratory:** 0.11 (1 of 9 steps, Step 5; Step 1 is balanced info gathering)
- **Justification:** Only the in-flight discovery has unknowns worth learning (how reliably flow branches, checkpoints
  and plan rows identify a running flow). Everything else follows established seams (`_append_ledger_row`,
  `is_metadata_path`, the `run_gh` runner seam, `FakeGitHub`).

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 17.0  |
| impact_pred         | 85.0  |
| cost_pred           | 28.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 27.7  |

### Strategy Rationale

The intent bundles two separable deliverables: a measurement (the touch-set estimate, carried on the plan row and scored
at calibration) and a control (admission before Stage 3 plus enforcement after it). The control cannot work without the
measurement, and the measurement is useful on its own (it starts producing precision and recall data on the very next
flow). Summing step entropies gives `0.5 + 1.5 + 2.5 + 1.5 + 3.5 + 2.5 + 2.0 + 2.0 + 1.0 = 17.0`, above the budget of
15.0, so the plan is decomposed into two child intents that run one after another (which is itself the
sequential-by-default behaviour this intent asks for). Child 1 covers Steps 1 to 4 plus their tests (entropy about 7.5);
Child 2 covers Steps 5 to 7 plus their tests (entropy about 9.5). Steps 8 and 9 are split across both children.

Aggregation: `p_success` is the bottleneck-weighted product of the two risky steps (Step 5 at 0.70 and Step 6 at 0.75)
softened because the remaining steps are well understood and retried by the review loop: roughly `0.70 * 0.75 = 0.53`
raised to 0.68 by the bounded rework the review loop provides. Impact is the maximum step impact (the admission gate,
85.0), cost is the sum of step costs (`28.0`), learning value is the maximum step learning value (Step 5, 6.0).
`EV = 0.68 * 85.0 + 0.5 * 6.0 - 0.3 * 17.0 - 28.0 = 57.8 + 3.0 - 5.1 - 28.0 = 27.7`.

Design decisions taken against alternatives:

- **Machine-readable section:** a level-2 heading `## Touch Set` whose top-level bullets each hold one backticked repo
  relative path or glob, optionally followed by `symbols:` and a comma list for very large modules. This mirrors the
  existing `## Sub-intent list` convention the planner already follows, so no new parser style is introduced.
- **Overlap unit:** file paths and globs, compared after normalisation and after dropping flow metadata
  (`is_metadata_path` from `sandbox_executor.calibration`, which already covers `plans/`, `executions/` and
  `holon-knowledge/`). Symbols are recorded and reported but never block on their own (a symbol clash implies a file
  clash anyway); this keeps false blocks low.
- **Glob semantics:** a declared directory (trailing slash) matches everything below it; `*` and `**` use
  `fnmatch`-style matching on POSIX paths. Two globs overlap when either matches the other's literal prefix or a
  concrete changed file matches both; when undecidable the check is conservative (reports overlap) and names both globs.
- **Lineage exclusion:** a flow never conflicts with its own intent lineage (same root intent prefix, so parent and
  child intents, re-plans and resumes are excluded); children already run sequentially.
- **Enforcement action:** fail Stage 3 with a resumable, clearly worded `touch_set_violation` payload naming the
  out-of-set files, and let the pipeline return to Stage 2 once with the widened touch set as re-plan feedback (the same
  bounded mechanism as `_replan_after_over_budget`), re-running admission before the next dispatch.
- **Override record:** `--allow-overlap` writes one `executions.jsonl` row (no new ledger) of kind `admission_override`
  naming the conflicting branches or pull requests and the files, appended through the existing `_append_ledger_row`
  helper on the plan branch before dispatch.
- **Deferred (explicitly):** a provisional pre-Stage 2 check using the intent `scope` would save planner tokens too, but
  the intent only requires the block before Stage 3; it is recorded as a follow-up, not built.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 3: ledgers are append-only; the override and the touch set are new
    fields or rows, never rewrites; exactly the three ledgers remain.
  - `holon-config/world/constraints.md` section 1: commits stay on the plan and execution branches; merging stays with
    the human maintainer.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed, docstringed public functions; tests under
    `apps/sandbox-executor/tests/flow/`; test changes declared here.
  - `docs/safety.md` "Human review is the final gate" and "Failure containment": the gate fails closed with a clear
    message, never merges, never deletes branches.
  - Intent constraints: Australian English, no new dependencies, `uv run pytest` only, hermetic (no real `gh`).
  - Repository limits: ruff `C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`, no new inline `noqa`;
    `flow_module_budgets.json` line budgets; no new imports from entrypoints into the flow engine.
- **Potential violations or edge cases:**
  - `stages/execute.py` (416 of 416) and `pipeline.py` (395 of 395) are at their line budgets; any hook raises them.
  - The planner entrypoint must import the touch-set parser from the flow package (flow to entrypoint direction is the
    forbidden one; entrypoint importing `sandbox_executor.flow.touch_set` is allowed but must not pull heavy imports).
  - Historic plan rows have no `touch_set`; admission must treat a missing declared set as "unknown" and fall back to
    the open pull request's real diff, never crash or block on absence alone.
  - `gh` unavailable or unauthenticated on the host: admission cannot see open pull requests.
  - Glob-against-glob overlap can produce false blocks.
- **Mitigations built into the plan:**
  - New logic lives in two new small modules (`flow/touch_set.py`, `flow/admission.py`); existing files get call sites
    only, with budget raises documented in the budgets file description.
  - `gh` failure makes admission fail closed with a message that suggests `--allow-overlap` (recorded), so the gate
    cannot be bypassed silently.
  - Metadata exclusion reuses `is_metadata_path`, so calibration, review and admission agree on what metadata is.
  - Every `gh` call goes through an injectable runner; tests use `FakeGitHub` or a stub runner and the existing network
    and `gh` write guards.
- **Residual risk accepted (and why):** planners will under-estimate touch sets at first (low recall). Enforcement turns
  that into a re-plan instead of a silent overlap, and calibration measures it so the estimate improves; the cost of an
  occasional re-plan is far below one more 141-pass review storm.
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 17.0
- **Budget Compliance:** The strategy exceeds budget (17.0 above 15.0) because admission, enforcement and estimation
  together span the planner, Stage 3, Stage 5 and the CLI; it is therefore decomposed in `## Sub-intent list` into two
  children of about 7.5 and 9.5, each within budget.

## Plan Description & Strategy

Build the touch set as data first, then put the gate on it. Child 1 teaches the planner to declare a touch set, puts it
on the plan row, documents it and scores it at Stage 5. Child 2 reads declared sets of in-flight flows plus the real
diffs of open flow pull requests, refuses Stage 3 on overlap (unless `--allow-overlap`, which is recorded), and after
Stage 3 checks the real diff stays inside the declared set. Each child is tested hermetically and passes
`uv run task lint`, `uv run task lint-docs` and the suite on its own.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/flow/touch_set.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/admission.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stages/execute.py` symbols: run_execute_stage
- `apps/sandbox-executor/src/sandbox_executor/flow/pipeline.py` symbols: \_replan_after_over_budget
- `apps/sandbox-executor/src/sandbox_executor/flow/context.py` symbols: FlowContext
- `apps/sandbox-executor/src/sandbox_executor/flow/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` symbols: main
- `apps/sandbox-executor/src/sandbox_executor/calibration.py` symbols: parse_predicted_metrics,
  format_calibration_report
- `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/flow/test_touch_set.py`
- `apps/sandbox-executor/tests/flow/test_admission.py`
- `apps/sandbox-executor/tests/flow/stages/test_execute_touch_set.py`
- `apps/sandbox-executor/tests/test_calibration_touch_set.py`
- `holon-config/prompts/planner.template.md`
- `docs/ledger_schema.md`

## Sub-intent list

1. Add the planner touch-set estimate: a `## Touch Set` plan section parsed by a new `flow/touch_set.py`, a `touch_set`
   field on the `plans.jsonl` row documented in `docs/ledger_schema.md`, and touch-set precision and recall in the Stage
   5 calibration report and metrics, with hermetic tests (Steps 1 to 4 and their parts of Steps 8 and 9).
2. Add touch-set admission before Stage 3 and enforcement after it: a new `flow/admission.py` that compares the plan's
   touch set with declared sets of in-flight flows and open flow pull request diffs, a hard refusal naming branches,
   pull requests and files, a recorded `--allow-overlap` override, and out-of-set diff enforcement that fails the stage
   and returns once to re-planning, with hermetic tests (Steps 5 to 7 and their parts of Steps 8 and 9).

---

## Step 1: Freeze the touch-set format and metadata rules

- **Sub‑intent recommendation:** NO
- **Reasoning:** Short, read-only design confirmation that both children depend on; cheap and low risk.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The `## Sub-intent list` parsing convention and `is_metadata_path` are a sufficient basis
  for a `## Touch Set` section without new parsing styles.
- **Learning target:** Exact metadata prefixes (`METADATA_PATH_PREFIXES`), how the planner reads its own plan file
  (`PLAN_FILE_PATTERN`, `parse_metrics`) and where the row dict is assembled in `entrypoint/planner.py`.
- **Maximum acceptable cost for this learning:** 1.5 cost units; no code changes.

### Intent & Git Integration

- **Step Intent:** Pin the format (heading, bullet syntax, optional `symbols:` suffix, directory and glob semantics,
  placeholder handling for `None`) and the metadata exclusion list before writing code.
- **Git branch:** Child 1 execution branch (no commit for this step).
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `calibration.py` `METADATA_PATH_PREFIXES` and `is_metadata_path`; confirm `plans/`, `executions/` and
   `holon-knowledge/` are covered, and decide that admission and enforcement import this function rather than copy it.
2. Read the sub-intent list parser in `decomposition_rule.py` and mirror its rules: top-level bullets only, indented
   bullets are details, placeholder first words (`none`, `n/a`) mean an empty set.
3. Record the format in the step notes for Step 2 and Step 3: one backticked POSIX path or glob per bullet; trailing `/`
   means the whole directory; optional text `symbols: a, b` after the path.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` section 2 (conventions).
- **Potential failure modes for this step:** choosing a format the planner model fills inconsistently.
- **Guardrails and early‑abort checks:** reuse an existing, already-followed convention.

### Success & Discard Criteria

- **Success:** Format and exclusions written down and consistent with existing parsers.
- **Discard:** If no reusable convention exists, fall back to a plain bullet list without symbols.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.95  |
| entropy_pred        | 0.5   |
| impact_pred         | 20.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 4.0   |
| ev_pred             | 19.35 |

### Step Metrics Rationale

`EV = 0.95 * 20.0 + 0.5 * 4.0 - 0.3 * 0.5 - 1.5 = 19.0 + 2.0 - 0.15 - 1.5 = 19.35`. Read-only, so near-certain success.

---

## Step 2: Add the pure touch-set module `flow/touch_set.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small pure module with no I/O; reused by planner, admission, enforcement and calibration.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One owner for parsing, normalising, matching and scoring touch sets.
- **Git branch:** Child 1 execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Define a frozen `TouchSet` dataclass holding a tuple of entries (`path_or_glob`, `symbols`), with `to_row()`
   returning the JSON shape for the ledger (a list of objects with `path` and optional `symbols`) and `from_row()`
   accepting both that shape and a plain list of strings (tolerant reader).
2. `parse_touch_set_section(plan_markdown)` returns a `TouchSet` or `None` when the heading is absent; placeholder items
   give an empty set.
3. `normalise_path` strips `./`, leading `/`, rename arrows (reuse the same rule as `is_metadata_path`), and converts to
   POSIX; `without_metadata(paths)` drops paths for which `is_metadata_path` is true.
4. `matches(entry, path)`: directory prefix match for trailing `/`, `fnmatch` otherwise (with `**` treated as any
   depth).
5. `overlapping_files(declared, concrete_paths)` and `overlapping_entries(declared_a, declared_b)` (glob against glob:
   equal entries, or the literal prefix of one matched by the other; conservative on doubt).
6. `precision_recall(declared, actual_paths)`: precision = matched declared entries over declared entries, recall =
   actual non-metadata files covered by any entry over actual non-metadata files; both `None` when the denominator is
   zero.
7. Keep every function under the ruff complexity limits; add a budgets entry at creation size in Step 9.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** ruleset section 2 (typing, docstrings); no new dependencies (standard library `fnmatch`, `pathlib`
  only).
- **Potential failure modes for this step:** glob edge cases causing false blocks; importing `calibration.py` pulls a
  large module into the planner container.
- **Guardrails and early‑abort checks:** table-driven tests in Step 8; if importing `calibration.py` is heavy, move only
  `METADATA_PATH_PREFIXES` lookup through a function argument instead.

### Success & Discard Criteria

- **Success:** Module under 200 lines, fully typed, tests green.
- **Discard:** If glob-against-glob logic exceeds complexity limits, restrict declared entries to files and directory
  prefixes only.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.5   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 51.55 |

### Step Metrics Rationale

`EV = 0.90 * 60.0 + 0.5 * 2.0 - 0.3 * 1.5 - 3.0 = 54.0 + 1.0 - 0.45 - 3.0 = 51.55`. Pure logic, well-trodden.

---

## Step 3: Teach the planner to declare the touch set and record it on the plan row

- **Sub‑intent recommendation:** NO
- **Reasoning:** Touches the planner prompt and the row writer; moderate risk, part of Child 1.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every new plan file carries `## Touch Set` and every new `plans.jsonl` row carries `touch_set`.
- **Git branch:** Child 1 execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `holon-config/prompts/planner.template.md`, add a short instruction block: list every file or glob the plan
   expects to create, modify or delete under a `## Touch Set` heading (format from Step 1), add `symbols:` for very
   large modules, exclude `plans/`, `executions/` and `holon-knowledge/`. Keep Australian English.
2. In `entrypoint/planner.py`, where the row dict is built (near `entropy_budget` and `_plan_decision_fields`), call
   `parse_touch_set_section` on the plan markdown and set `touch_set` to `to_row()` output; a missing section records
   `touch_set: null` plus a log warning (never fails the plan, so older models keep working).
3. Update `docs/ledger_schema.md`: add `touch_set` to the Stage 2 row in the Flow Stage Record Matrix and a paragraph
   (shape, metadata exclusion, null on historic rows, who reads it: admission, enforcement, calibration).
4. Run `uv run task lint-docs` locally for the documentation change.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (append-only: new field only on new rows); "no new imports from entrypoints
  into the host flow engine" (direction here is entrypoint importing flow, which is allowed).
- **Potential failure modes for this step:** prompt template tests (`tests/test_prompt_templates.py`) pinning exact
  text; `main()` already carries `noqa` complexity markers, so new branches must live in a helper.
- **Guardrails and early‑abort checks:** add a helper `_touch_set_field(plan_md_path)` beside `_plan_decision_fields`
  instead of growing `main()`; run
  `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_planner.py`
  first.

### Success & Discard Criteria

- **Success:** A planner run on a fixture plan writes `touch_set` on the row; docs lint passes.
- **Discard:** If the template is pinned by a contract test that cannot be updated within scope, stop and report.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 70.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.75 |

### Step Metrics Rationale

`EV = 0.85 * 70.0 + 0.5 * 3.0 - 0.3 * 2.5 - 3.5 = 59.5 + 1.5 - 0.75 - 3.5 = 56.75`. Spans three files across the
template, entrypoint and docs, hence higher entropy than Step 2.

---

## Step 4: Score the touch set at Stage 5 (precision and recall)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small addition to an existing report path; the learning loop that keeps estimates honest.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The calibration report and its metrics carry `touch_set_precision`, `touch_set_recall` and the
  out-of-set and unused entries.
- **Git branch:** Child 1 execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `calibration.py`, have `parse_predicted_metrics` keep the row's `touch_set` in the plan metadata it already
   returns; the actual changed files are already collected for actual metrics (diff against the plan branch with
   metadata filtered by `is_metadata_path`).
2. Call `precision_recall` from `flow/touch_set.py` and add the two values plus the lists of unpredicted files and
   unused entries to the calibration data dict and to the markdown report (a short "Touch set" table).
3. Historic plans without a touch set report `n/a` and are omitted from averages.
4. Keep `calibration.py` growth to call sites only (a small helper of under 20 lines); if the module guard complains,
   move the report formatting into `flow/touch_set.py`.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (calibration rows are appended, never rewritten).
- **Potential failure modes for this step:** report format snapshot tests in `tests/test_calibration.py` break.
- **Guardrails and early‑abort checks:** run
  `uv run pytest apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_calibration_touch_set.py`
  before the full suite.

### Success & Discard Criteria

- **Success:** A fixture calibration shows precision and recall values; a historic row shows `n/a`.
- **Discard:** If snapshot churn exceeds three existing tests, put the values in metrics only and leave the markdown
  table for a follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.5   |
| impact_pred         | 40.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 32.55 |

### Step Metrics Rationale

`EV = 0.85 * 40.0 + 0.5 * 3.0 - 0.3 * 1.5 - 2.5 = 34.0 + 1.5 - 0.45 - 2.5 = 32.55`. Lower impact: measurement rather
than the gate itself.

---

## Step 5: Discover in-flight flows and their touch sets in `flow/admission.py`

- **Sub‑intent recommendation:** YES
- **Reasoning:** The riskiest and most reusable piece (it defines what "in flight" means); it opens Child 2.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** Open flow pull requests (head branches matching the `I-.../P-.../E-.../_` shape) plus
  local checkpoints under each git worktree's `.holon/flow/` whose stage is not completed identify every in-flight flow,
  and their plan branches yield the declared touch set through the existing `_read_ledger_rows` helper.
- **Learning target:** False-positive and false-negative rates of this discovery (stale checkpoints, closed but unmerged
  branches, flows with historic rows lacking a touch set).
- **Maximum acceptable cost for this learning:** 5.0 cost units; abort the exploratory part if more than 7.5.

### Intent & Git Integration

- **Step Intent:** Produce a list of `InFlightFlow` records (label naming the pull request number or branch, declared
  touch set or `None`, real changed files or `None`).
- **Git branch:** Child 2 execution branch.
- **Sub‑intent:** NEW (Child 2)

### Implementation Details (No code blocks, only logic/steps)

1. `open_flow_pull_requests(repo_dir, run_gh)`: `gh pr list --state open --json number,headRefName,baseRefName`; keep
   heads that parse as flow execution branches (reuse `ref_names` helpers); for each, `gh pr diff <number> --name-only`
   gives the real changed files (metadata dropped).
2. Declared set of a pull request: derive its plan branch from the head (strip the `E-.../_` segment), read the newest
   plan row through `_resolve_branch_ref` and `_read_ledger_rows`, take `touch_set` (fetch through `host_remote` only if
   the ref is missing; never in a mounted role container).
3. `local_in_flight_flows(repo_dir)`: `git worktree list --porcelain`; in each worktree read
   `.holon/flow/checkpoint-*.json` with `load_checkpoint`; keep those whose current stage is execute or review (or a
   failed stage with an open execution branch); take the plan branch from the checkpoint context and its row's
   `touch_set`.
4. Exclude the current flow's own lineage: anything sharing the root intent prefix of `context.intent_branch`.
5. Inject the `gh` and `git` runners as parameters (default to the real runners) so tests never call real `gh`.
6. A `gh` failure returns a typed `AdmissionUnavailable` result rather than raising, so Step 6 can fail closed with a
   clear message.

### Dependencies & Criticality

- **Depends on:** Step 2, Child 1 merged into the intent branch
- **Is Bottleneck:** YES (p 0.70; mitigated by being its own child intent and by the fail-closed fallback)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` sandboxing (host-side `gh` only; role containers never call it); network and `gh`
  write guards in tests (`tests/gh_write_guard.py`, `tests/network_guard.py`).
- **Potential failure modes for this step:** stale checkpoints falsely blocking; slow `gh pr diff` across many pull
  requests; heads from forks or non-flow branches.
- **Guardrails and early‑abort checks:** only flow-shaped heads count; checkpoints older than the newest commit on a
  merged or deleted plan branch are ignored; cap at a bounded number of pull requests with a logged warning.

### Success & Discard Criteria

- **Success:** On fixtures (fake `gh`, two worktrees, one stale checkpoint) discovery returns exactly the live flows.
- **Discard:** If local checkpoint discovery proves unreliable, keep only open pull requests plus local branches with a
  running checkpoint in the current worktree, and record the gap in the execution notes.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 3.5   |
| impact_pred         | 85.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 56.45 |

### Step Metrics Rationale

`EV = 0.70 * 85.0 + 0.5 * 6.0 - 0.3 * 3.5 - 5.0 = 59.5 + 3.0 - 1.05 - 5.0 = 56.45`. Highest entropy and learning value:
the definition of "in flight" is the main unknown in the whole intent.

---

## Step 6: Gate Stage 3 on admission and add `--allow-overlap`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Call sites plus a CLI option; risky only through line budgets, so it stays in Child 2.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Stage 3 refuses to dispatch (no worktree, no container, no tokens) on any overlap, unless
  `--allow-overlap`, which is recorded in the ledger.
- **Git branch:** Child 2 execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow/admission.py`, `check_admission(context, run_gh, run_git)` reads the active plan row's `touch_set` (same
   read path as `_plan_budget_decision`), gathers Step 5 records, and returns an `AdmissionDecision` with the conflicts
   (label, overlapping files or entries) and a `format_refusal()` message such as: Stage 3 refused: touch set overlaps
   pull request #N (branch X) on files A, B; finish or merge that flow first, or rerun with `--allow-overlap`.
2. The comparison covers: own declared set against each declared set, and own declared set against each pull request's
   real diff. A plan without a touch set is refused with a message asking for re-planning (the planner now always writes
   one), unless `--allow-overlap`.
3. In `stages/execute.py`, call `check_admission` once, after the plan budget decision and before
   `_snapshot_child_branches` and any dispatch; on refusal return `_stage_failure(..., dispatched=False)` with payload
   `admission_refused` and the conflicts. Skip the check for `dry_run` only after logging what it would decide.
4. Add `allow_overlap: bool = False` to `FlowContext` (serialised in `to_dict` and `from_dict`, default false for old
   checkpoints) and `--allow-overlap` to `cli/commands/flow.py` with help text in Australian English.
5. With the flag, write one `executions.jsonl` row through `_append_ledger_row` on the plan branch:
   `kind: "admission_override"`, `plan_id`, `intent_id`, conflicts, `recorded_by: "flow"`, timestamp; then continue.
6. Re-export the public names from `flow/__init__.py` as the package export test requires.

### Dependencies & Criticality

- **Depends on:** Step 5
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** constraints section 3 (append-only, three ledgers); "Human review is the final gate" (refusal
  never merges or closes anything); module budgets.
- **Potential failure modes for this step:** `execute.py` (at 416 of 416) and `context.py` (at 268 of 268) exceed
  budgets; the override row confuses readers of `executions.jsonl` that expect execution rows only.
- **Guardrails and early‑abort checks:** keep the hook to about 6 lines; raise budgets with a documented sentence; check
  `ledger.py` and `ledger_query` readers skip unknown `kind` values (add a filter if not) and document the row in
  `docs/ledger_schema.md`.

### Success & Discard Criteria

- **Success:** With an overlapping fake pull request the executor dispatcher stub is never called and the message names
  the pull request and files; with the flag it is called once and exactly one override row is appended.
- **Discard:** If `executions.jsonl` readers cannot tolerate the new row kind, record the override as fields on the
  Stage 3 summary row instead.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.5   |
| impact_pred         | 80.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.75 |

### Step Metrics Rationale

`EV = 0.75 * 80.0 + 0.5 * 3.0 - 0.3 * 2.5 - 4.0 = 60.0 + 1.5 - 0.75 - 4.0 = 56.75`. The gate delivers most of the
intent's value; risk sits in budgets and ledger readers.

---

## Step 7: Enforce the declared touch set after Stage 3

- **Sub‑intent recommendation:** NO
- **Reasoning:** Reuses the existing execution-diff check and the bounded re-plan path; moderate risk.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** A real diff outside the declared set (ignoring metadata) fails Stage 3 and returns once to Stage 2
  with the widened set as feedback, and admission re-runs before the next dispatch.
- **Git branch:** Child 2 execution branch.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow/admission.py`, `out_of_set_files(touch_set, changed_files)` uses `without_metadata` and `matches`.
2. In `stages/execute.py`, next to the existing empty-diff guard (which already lists the execution diff), call it; on
   violation return a failed result with payload `touch_set_violation` (files) and `replan_requested: true`, and record
   a superseding failed summary row the same way the empty-diff guard does.
3. In `pipeline.py`, generalise the over-budget re-plan to also accept `touch_set_violation` (one shared helper choosing
   the feedback text: widen the touch set to include the listed files), bounded by `MAX_REPLAN_ATTEMPTS`; the next Stage
   3 runs admission again on the new plan.
4. Metadata-only changes never violate; a plan whose declared set is absent is treated as violated only when
   `--allow-overlap` was not given (there is nothing to enforce against otherwise and admission already refused it).

### Dependencies & Criticality

- **Depends on:** Step 6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` failure containment (fail clearly, resumable); `pipeline.py` at 395 of 395.
- **Potential failure modes for this step:** re-plan loops; the execution branch left pushed with out-of-set files.
- **Guardrails and early‑abort checks:** bounded by `MAX_REPLAN_ATTEMPTS`; the failed execution branch is kept (never
  deleted) for the human, and the next execution starts from the new plan branch.

### Success & Discard Criteria

- **Success:** A fake executor that edits an undeclared file fails Stage 3 with the file named and the pipeline returns
  to Stage 2 once; a metadata-only extra file passes.
- **Discard:** If the pipeline change cannot stay within complexity limits, keep fail-only (no automatic re-plan); the
  intent permits either.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 43.4  |

### Step Metrics Rationale

`EV = 0.75 * 60.0 + 0.5 * 4.0 - 0.3 * 2.0 - 3.0 = 45.0 + 2.0 - 0.6 - 3.0 = 43.4`. The fail-only fallback keeps the
downside small.

---

## Step 8: Hermetic tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Split across the two children alongside the code each covers.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Cover every acceptance criterion without real `gh` or network.
- **Git branch:** Child 1 and Child 2 execution branches.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `tests/flow/test_touch_set.py` (Child 1): parsing (present, absent, placeholder, nested bullets ignored, symbols),
   normalisation, directory and glob matching, glob-against-glob overlap, metadata exclusion, precision and recall
   including zero denominators.
2. Planner row test (Child 1, in the existing planner test module or a new focused file): the row carries `touch_set`; a
   plan without the section records null.
3. `tests/test_calibration_touch_set.py` (Child 1): report values and `n/a` for historic rows.
4. `tests/flow/test_admission.py` (Child 2): overlap against a declared set of an open pull request; against an open
   pull request's real diff (`gh pr diff --name-only` via `FakeGitHub` or a stub runner); against a local in-flight
   checkpoint in a second worktree; own-lineage exclusion; `gh` failure fails closed; metadata-only overlap never
   blocks.
5. `tests/flow/stages/test_execute_touch_set.py` (Child 2): refusal happens before the dispatcher stub is called and the
   message names the pull request or branch and files; `--allow-overlap` dispatches and appends exactly one override
   row; out-of-set diff fails and requests re-plan; metadata-only extra files pass.
6. Run affected tests first, for example
   `uv run pytest apps/sandbox-executor/tests/flow/test_touch_set.py apps/sandbox-executor/tests/flow/test_admission.py`,
   then the full suite once with `uv run task test`. No repeated runs (no timing target in the intent).

### Dependencies & Criticality

- **Depends on:** Steps 2 to 7 (per child)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** ruleset section 3 (tests declared in the plan, pytest only); hermetic guards.
- **Potential failure modes for this step:** a test path that accidentally shells out to `gh`.
- **Guardrails and early‑abort checks:** the existing `gh_write_guard` and `network_guard` fixtures; assert the fake's
  recorded calls.

### Success & Discard Criteria

- **Success:** All new tests pass and the full suite passes once.
- **Discard:** Any test needing real `gh` is rewritten against the fake, never skipped.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.0   |
| impact_pred         | 60.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 44.9  |

### Step Metrics Rationale

`EV = 0.80 * 60.0 + 0.5 * 3.0 - 0.3 * 2.0 - 4.0 = 48.0 + 1.5 - 0.6 - 4.0 = 44.9`. Fixtures (`FakeGitHub`, worktree
fixtures) already exist, which keeps cost moderate.

---

## Step 9: Budgets, lint and verification

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical closure in each child.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Keep `uv run task lint`, `uv run task lint-docs` and the module budget check green.
- **Git branch:** Child 1 and Child 2 execution branches.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add budget entries at creation size for `touch_set.py` and `admission.py` in
   `apps/sandbox-executor/flow_module_budgets.json`; raise `stages/execute.py`, `context.py`, `pipeline.py` and
   `__init__.py` by the exact lines added, with one explanatory sentence each appended to the description.
2. Run `uv run task lint` and `uv run task lint-docs`; fix without `noqa`.
3. Check spelling is Australian English (normalise, behaviour, licence) in new text.
4. Confirm the diff stays within `## Touch Set` (dogfooding the enforcement rule by hand).

### Dependencies & Criticality

- **Depends on:** Steps 2 to 8
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** repository lint rules; no new dependencies (`uv.lock` unchanged).
- **Potential failure modes for this step:** prettier formatting churn in docs.
- **Guardrails and early‑abort checks:** run lint-docs after each docs edit.

### Success & Discard Criteria

- **Success:** Both lint tasks and the suite pass.
- **Discard:** Not applicable; failures are fixed in place.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.0   |
| impact_pred         | 40.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 33.2  |

### Step Metrics Rationale

`EV = 0.85 * 40.0 + 0.5 * 2.0 - 0.3 * 1.0 - 1.5 = 34.0 + 1.0 - 0.3 - 1.5 = 33.2`. Routine but mandatory.

---
