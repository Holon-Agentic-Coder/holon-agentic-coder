# Plan for I-1792988604-touch-set-estimate-and-overlap-admission

- **Plan ID:** P-1792988757-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-26T04:25:58.412Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS (with two small clarifying refinements, not a reframe)
- **Reframed intent (if applicable):** Not reframed. Refinements: (1) symbols in the touch set are informational (shown
  in admission messages and calibration) while blocking stays file/glob level, which is the conservative choice; (2)
  "flow metadata" for touch sets is `plans/`, `executions/`, `intents/` and `holon-knowledge/ledger/`, which is wider
  than today's `git.EMPTY_DIFF_EXCLUDES` (it lacks `intents/`), so a dedicated `TOUCH_SET_EXCLUDES` constant extends it
  rather than silently changing the empty-diff guard.
- **Exploration stance:** balanced. The design is well specified and mostly exploit work on known modules, but one cheap
  exploratory step back-tests the overlap matcher on local git history to measure the false-block rate before the hard
  block becomes the default.
- **Safety priority level:** elevated
- **Priority Justification:** The change adds a hard gate in front of Stage 3 and new `gh` read calls; it touches the
  ledger append path (`append_action_record()` under `LedgerLock`, `world/constraints.md` §3 append-only ledgers) and
  must never let the test-suite reach real `gh` (the suite guard only blocks `gh` write verbs, so `gh pr list` and
  `gh pr diff` are reads that must be mocked explicitly). A wrong gate either blocks all work (fail closed) or silently
  re-enables the 10M-token re-review loop.

## Exploration

- **Proportion of steps that are exploratory:** 0.11 (1 of 9 steps)
- **Justification:** The intent is concrete and the cost of the problem is already measured (141 review passes). Only
  the false-block rate of glob matching is genuinely unknown, so a single small back-test step is worth its cost.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 12.6  |
| impact_pred         | 95.0  |
| cost_pred           | 44.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 19.82 |

## Touch Set

Dogfooding the format this plan introduces (paths are repository-relative; symbols are informational).

- create `apps/sandbox-executor/src/sandbox_executor/touch_set.py`
- create `apps/sandbox-executor/src/sandbox_executor/admission.py`
- create `apps/sandbox-executor/tests/test_touch_set.py`
- create `apps/sandbox-executor/tests/test_admission.py`
- modify `apps/sandbox-executor/src/sandbox_executor/flow.py` — symbols: `run_execute_stage`, `FlowContext`,
  `run_flow_pipeline`
- modify `apps/sandbox-executor/src/sandbox_executor/cli.py` — symbols: `main` (execute and flow parsers and handlers)
- modify `apps/sandbox-executor/src/sandbox_executor/git.py` — symbols: new `gh` read helpers, `TOUCH_SET_EXCLUDES`
- modify `apps/sandbox-executor/src/sandbox_executor/artefacts.py` — symbols: `PLAN_SPEC`, `ArtefactSection`
- modify `apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py` — symbols: `main`
- modify `apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py` — symbols: effective-diff guard
- modify `apps/sandbox-executor/src/sandbox_executor/calibration.py` — symbols: `generate_calibration`,
  `format_markdown_report`, `record_calibration_action`
- modify `apps/sandbox-executor/tests/test_planner.py`
- modify `apps/sandbox-executor/tests/test_flow.py`
- modify `apps/sandbox-executor/tests/test_cli.py`
- modify `apps/sandbox-executor/tests/test_calibration.py`
- modify `apps/sandbox-executor/tests/test_executor.py`
- modify `holon-config/prompts/planner.template.md`
- modify `docs/ledger_schema.md`
- modify `docs/git_flow.md`
- modify `docs/planner/planning_architecture_specification.md`
- modify `docs/executor/execution_architecture_specification.md`

### Strategy Rationale

The work is decomposed into a pure, dependency-free core (`touch_set.py`: parse, normalise, exclude metadata, glob
match, overlap, precision/recall) that every later step reuses, then the three integration points in pipeline order:
planner (estimate), pre-Stage-3 admission, post-Stage-3 enforcement, Stage 5 calibration, and finally docs and a
validation gate. Pushing all logic into two new modules (`touch_set.py`, `admission.py`) keeps the edits inside the
4,575-line `flow.py` and 2,922-line `cli.py` small and surgical, which is itself the lesson of PRs #94 to #96.

Steps are executed **sequentially on execution branches under this plan**, not as parallel sub-intents: parallel
sub-intents would all touch `flow.py` and `cli.py` and reproduce exactly the overlap this intent is meant to prevent.

Aggregation: `p_success` is a qualitative judgement anchored on the bottleneck Step 3 (admission data collection,
`p = 0.75`) and Step 4 (wiring, `p = 0.78`), lowered for the chain of dependencies but raised because every step is
retryable and independently testable, giving `0.68` rather than the naive product. Entropy is the sum of step entropies
(`1.2 + 2.0 + 2.4 + 2.0 + 1.6 + 1.2 + 0.6 + 1.0 + 0.6 = 12.6`). Cost is the sum of step costs
(`5 + 7 + 8 + 7 + 5 + 4 + 2.5 + 2.5 + 3 = 44.0`). Impact is the maximum value of the whole feature (avoiding roughly 10M
tokens of re-review per overlapping batch), `95.0`. Learning value `6.0` reflects the new calibration signal on
estimation quality plus the back-test. Overall
`EV = 0.68 * 95.0 + 0.5 * 6.0 - 0.3 * 12.6 - 44.0 = 64.60 + 3.00 - 3.78 - 44.00 = 19.82`.

Strong assumptions: (a) `gh pr list --json number,headRefName` and `gh pr diff <n> --name-only` are available to the
host CLI (they are already used elsewhere in `flow.py`); (b) other flows' declared touch sets are readable from their
own branch's `holon-knowledge/ledger/plans.jsonl` via the existing `_read_ledger_rows` / `git show` path; (c) legacy
plans without a `touch_set` field still exist in flight, so admission must degrade to "actual PR diff only" for them.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: execution branches only, rebase before execution, no commits to `main`.
  - `holon-config/world/constraints.md` §3: append-only ledgers; ACT-OVERLAP and out-of-set failure records are appends
    (superseding rows), never edits.
  - `holon-config/world/ruleset.md` §2 and §3: typed public functions with docstrings, tests in `tests/` matching the
    module, pytest only; test changes are declared in this plan.
  - `docs/safety.md` "Git is the safety boundary" and "Human review is the final gate": admission only refuses, it never
    merges, closes or edits PRs.
  - Intent constraints: Australian English, no new dependencies, `uv run task test` / `uv run task lint`, no auxiliary
    ledgers, unchanged ledger layout.
- **Potential violations or edge cases:**
  - Tests accidentally reaching real `gh` read commands (not blocked by `gh_write_guard.py`).
  - Fail-closed admission blocking every flow when `gh` is not authenticated.
  - Self-overlap: the flow's own intent/plan PR (or a re-execution of the same plan) counted as a conflict.
  - Over-broad globs (for example `apps/**`) blocking everything; under-declared touch sets causing enforcement failures
    after tokens were spent.
  - A new local in-flight registry drifting into an "auxiliary ledger".
- **Mitigations built into the plan:**
  - All `gh`/`git`/`docker` calls go through injectable runners; every new test patches them and an autouse fixture in
    the new test modules asserts no unpatched `gh` call happens.
  - Clear fail-closed message naming the failing command plus the `--allow-overlap` escape hatch, recorded as
    ACT-OVERLAP.
  - Exclude branches sharing the current plan prefix (`I-.../P-...`) from conflict candidates.
  - Step 8 back-test measures false-block rate; planner prompt forbids repository-wide globs and the parser rejects `**`
    at the root or bare `*`.
  - Local in-flight detection uses live process state (docker container labels) and existing `.holon/flow/` checkpoints,
    never a new jsonl file.
- **Residual risk accepted (and why):** Enforcement fires after Stage 3 tokens are spent when the agent under-declares;
  this is intended by the intent (it makes estimation errors visible and calibratable) and Stage 5 precision/recall will
  drive estimation quality up over time.
- **Allocated Entropy Budget:** 16.0
- **Predicted Plan Entropy:** 12.6
- **Budget Compliance:** The strategy fits within budget (12.6 of 16.0, leaving 3.4 headroom for rework in Steps 3–4).

## Plan Description & Strategy

Build a shared touch-set core, make the planner emit and persist it, gate Stage 3 on overlap with in-flight flows
(declared and actual), enforce the declared set after Stage 3, and measure prediction quality in Stage 5. Every
integration reuses existing helpers: `changed_paths()` and `EMPTY_DIFF_EXCLUDES` in `git.py`, the artefact
`PLAN_SPEC`/re-prompt machinery in `artefacts.py`/`turns.py`, `_read_ledger_rows` and
`_append_superseding_execution_row` in `flow.py`, `append_action_record()`/`LedgerLock`/`generate_action_id()` in
`ledger.py`, and the calibration report writer in `calibration.py`.

---

## Step 1: Touch-set core module (parse, normalise, glob match, overlap, precision/recall)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Pure functions in a new file with no I/O; low risk, high reuse by Steps 2–6, and it touches no shared
  hot file, so it cannot conflict with other flows.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide one authoritative implementation of the touch-set data model and matching semantics.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-touch-set-core/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/touch_set.py` with a frozen dataclass `TouchEntry` (path or glob,
   action one of create/modify/delete/unknown, tuple of symbols) and `TouchSet` (entries, `to_ledger()` returning the
   `touch_set` field shape: list of objects with `path`, `action`, optional `symbols`; `from_ledger()` accepting that
   shape and tolerating a plain list of strings).
2. Add `TOUCH_SET_EXCLUDES` in `git.py` as `EMPTY_DIFF_EXCLUDES` plus `intents/` (do not change `EMPTY_DIFF_EXCLUDES`,
   so the empty-diff guard and its tests stay identical) and an `is_flow_metadata(path)` helper in `touch_set.py` built
   on it.
3. `parse_touch_set_section(markdown)`: locate the `## Touch Set` heading (reuse `artefacts._find_heading` semantics),
   read bullets until the next `##` heading; each bullet is a dash, an action word (create, modify or delete), then the
   path or glob in a code span, optionally followed by an em dash, the word symbols and a comma-separated list of code
   spans. Paths: strip `./`, reject absolute paths, `..` segments, a bare `*`/`**` or any entry whose first segment is a
   glob (repository-wide), drop metadata paths with a recorded note. Return `(TouchSet, errors)`; an empty entry list
   after exclusion is an error ("no source paths declared").
4. Glob-aware matching: `path_matches(pattern, path)` using `fnmatch`-style segment matching where `*` stays inside one
   segment and `**` spans segments (implemented with `PurePosixPath.full_match` on Python 3.13, no new dependency); a
   directory entry ending in `/` matches everything below it.
5. `patterns_overlap(a, b)` for pattern-versus-pattern (declared versus declared): literal-literal is equality;
   literal-glob is `path_matches`; glob-glob is conservative overlap (true when the literal prefixes before the first
   wildcard segment are prefix-related and the shapes can intersect), documented as possibly over-blocking.
6. `find_overlaps(mine, other_patterns_or_paths)` returning sorted overlapping pairs; always ignores metadata.
7. `out_of_set(touch_set, changed_paths)` returning changed non-metadata paths not matched by any entry.
8. `precision_recall(touch_set, actual_paths)` returning precision, recall, `missed` (actual paths no entry matches) and
   `unused` (entries that match no actual path); returns None for a ratio whose denominator is zero (not measured, never
   claimed as 0 or 1).
9. Tests in `apps/sandbox-executor/tests/test_touch_set.py`: parsing of all bullet variants, rejection cases, metadata
   exclusion (including `intents/`), `*` versus `**`, directory entries, glob-glob overlap, out-of-set, precision and
   recall including empty denominators.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** YES (every later step uses it; p is high so no sub-intent needed)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §2 (typing, docstrings), §3 (tests location).
- **Potential failure modes for this step:** `PurePosixPath.full_match` semantics differ from expectations for trailing
  `/` or `**` at the end; glob-glob overlap too lax (missed conflict) or too strict.
- **Guardrails and early‑abort checks:** Table-driven tests for every matching rule; when uncertain, glob-glob overlap
  returns true (fail towards sequential execution).

### Success & Discard Criteria

- **Success:** New module with 100 % of its public functions covered by `test_touch_set.py`;
  `uv run pytest apps/sandbox-executor/tests/test_touch_set.py` passes; lint passes.
- **Discard:** If `full_match` proves unusable, fall back to a small hand-written segment matcher; discard the step if
  cost exceeds `1.5 * 5 = 7.5`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.2   |
| impact_pred         | 70.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 59.14 |

### Step Metrics Rationale

Self-contained pure code with clear semantics.
`EV = 0.90 * 70.0 + 0.5 * 3.0 - 0.3 * 1.2 - 5.0 = 63.00 + 1.50 - 0.36 - 5.00 = 59.14`.

---

## Step 2: Planner estimate — template section, plan validation with targeted re-prompt, `touch_set` ledger field

- **Sub‑intent recommendation:** NO
- **Reasoning:** Moderate change confined to the planner entrypoint, artefact spec and prompt; reuses the existing
  re-prompt path so risk is contained.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every new plan declares a parseable touch set and the plans.jsonl row carries it.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-planner-touch-set/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `holon-config/prompts/planner.template.md`: add a mandatory `## Touch Set` section to the plan format (placed right
   after `## Overall Plan Metrics` so it is written early under the write protocol), with the bullet grammar from Step
   1, rules (repository-relative, list every file to create, modify or delete including tests and docs, prefer literal
   paths, globs only below a concrete directory, never flow metadata, list main symbols for very large modules such as
   `flow.py` and `cli.py`), and add it to the write-protocol ordering.
2. `artefacts.py`: extend `ArtefactSection` with an optional `validator` callable (content to error string or None) used
   by `present_in`; add a `## Touch Set` section to `PLAN_SPEC` whose validator calls `parse_touch_set_section` and
   fails on errors. Its label carries the parse error (for example "`## Touch Set` section (unparseable: no source paths
   declared)") so `build_reprompt` produces a targeted re-prompt through the existing `supervise_authoring_turn`
   missing-section follow-up. Confirm in `turns.py` that missing sections already trigger the re-prompt for a usable
   artefact; if they only trigger when metrics are missing, add the touch-set section to the follow-up condition without
   changing other behaviour.
3. `entrypoint/planner.py`: after the existing metrics and size checks, parse the touch set; if still unparseable after
   the re-prompt, print a targeted error and `sys.exit(1)` before any ledger append or commit (same fail-before- append
   rule as metrics). Otherwise add `touch_set` (Step 1 `to_ledger()` shape) to `plan_entry`.
4. `docs/ledger_schema.md`: document `touch_set` on plans.jsonl rows (shape, optional `symbols`, metadata always
   excluded, absent on legacy rows).
5. Tests in `tests/test_planner.py` (declared test change): plan with valid section writes the field; missing section
   triggers re-prompt and then succeeds; unparseable after re-prompt exits 1 with no ledger append; metadata-only
   section is rejected. Update existing planner fixtures that build minimal plan files to include a touch set (declared
   test change, assertions unchanged in meaning).

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (admission and enforcement have nothing to compare without it)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (test changes declared here); intent constraint "reuse the
  existing missing-section re-prompt path".
- **Potential failure modes for this step:** Many existing planner/integration fixtures lack the new section and start
  failing; changing `ArtefactSection` breaks the executor spec.
- **Guardrails and early‑abort checks:** `validator` defaults to None so `EXECUTION_SPEC` is unaffected; run the full
  planner and artefact test modules before moving on.

### Success & Discard Criteria

- **Success:** New plans.jsonl rows include `touch_set`; targeted re-prompt observed in tests; all planner tests pass.
- **Discard:** If the re-prompt path cannot be reused without restructuring `turns.py`, stop and split that refactor
  into its own sub-intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.0   |
| impact_pred         | 75.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 54.40 |

### Step Metrics Rationale

Fixture churn is the main risk. `EV = 0.80 * 75.0 + 0.5 * 4.0 - 0.3 * 2.0 - 7.0 = 60.00 + 2.00 - 0.60 - 7.00 = 54.40`.

---

## Step 3: Admission data collection — in-flight flow discovery and overlap evaluation

- **Sub‑intent recommendation:** YES (run sequentially after Step 2, never in parallel with Step 4)
- **Reasoning:** Highest-uncertainty step (external `gh` behaviour, branch parsing, legacy plans, self-exclusion) and
  reusable by future tooling; its own evaluation keeps the risky logic reviewable before it is wired into Stage 3.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** Declared touch sets plus `gh pr diff --name-only` for open flow PRs are sufficient to
  detect every overlap of the #94/#95/#96 kind before tokens are spent.
- **Learning target:** How often in-flight flows lack a declared touch set (legacy rows) and how many `gh` calls an
  admission check costs.
- **Maximum acceptable cost for this learning:** `1.5 * 8 = 12` cost units.

### Intent & Git Integration

- **Step Intent:** A side-effect-free `evaluate_admission()` that returns either "admitted" or a list of conflicts,
  failing closed on discovery errors.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-admission-core/_`
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. `git.py` (the git/gh helper layer): add `gh_list_open_flow_prs(repo_dir, runner)` running
   `gh pr list --state open --json number,headRefName,url --limit 200` and filtering head branches with the flow regex
   (`I-.../_`, `I-.../P-.../_`, `I-.../P-.../E-.../_`); and `gh_pr_changed_files(repo_dir, number, runner)` running
   `gh pr diff <n> --name-only`. Both raise a typed `GhUnavailableError` when `gh` is missing, exits non-zero or returns
   unparseable JSON. Runners are injectable for tests.
2. New `apps/sandbox-executor/src/sandbox_executor/admission.py`:
   - `InFlightFlow` dataclass (source "pr" or "local", branch, PR number/URL, declared `TouchSet` or None, actual
     changed paths).
   - `discover_open_pr_flows()`: for each open flow PR, read the declared touch set from that branch's plans.jsonl
     (fetch head branch refs via the existing `_fetch_refspecs_error`-style helper, then
     `git show origin/<head>:holon-knowledge/ledger/plans.jsonl`, matching the plan id parsed from the branch), plus the
     actual changed files from `gh_pr_changed_files`. Intent-only branches have no plan, so they use actual diff only.
   - `discover_local_flows()`: running executor containers found with
     `docker ps --filter label=holon.role=executor --format` reading a `holon.plan_branch` label (Step 4 adds the label
     at container start), plus `.holon/flow/` checkpoints whose current stage is execute and not finished. Docker absent
     means no local containers (not an error). Declared touch sets come from the local plans.jsonl rows of those
     branches.
   - Self-exclusion: drop candidates sharing the current plan prefix (same `I-.../P-...`), and the current intent's own
     intent-only PR.
   - `evaluate_admission(plan_branch, own_touch_set, repo_dir, runners)` returns `AdmissionResult` (admitted flag,
     conflicts as list of branch/PR plus overlapping files and whether each came from a declared set or the real diff,
     `error` for fail-closed). Own plan lacking `touch_set` (legacy) is a fail-closed error with "re-plan" advice.
   - `format_admission_refusal(result)`: one message naming each PR/branch and its overlapping files, telling the
     operator to rerun after the blocking PR merges and to re-plan on the new main, and mentioning `--allow-overlap`.
3. Tests `tests/test_admission.py` with fully mocked runners (autouse fixture fails any unpatched `gh` call): overlap
   against declared touch sets, against PR diffs, against local in-flight executions, glob overlap, metadata-only
   overlap admitted, self-exclusion, legacy in-flight plan uses diff only, `gh` missing / non-zero / bad JSON fails
   closed.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2 (ledger field shape)
- **Is Bottleneck:** YES (p 0.75 ≥ 0.6, so sub-intent is YES rather than STRONGLY_YES)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §1 (read-only against other branches; never edit or close PRs);
  `apps/sandbox-executor/tests/gh_write_guard.py` (reads are not blocked, so mocks are mandatory).
- **Potential failure modes for this step:** `gh pr list` pagination (>200 open PRs); forks with head branches not on
  origin; slow N-PR loops.
- **Guardrails and early‑abort checks:** Cap and report when the limit is hit (treat as fail closed); bound each call
  with the existing `run_git` timeout pattern; no network in tests.

### Success & Discard Criteria

- **Success:** All listed admission scenarios pass with mocks; a refusal message names PR, branch and files.
- **Discard:** If declared touch sets of other branches cannot be read reliably, ship admission on actual PR diffs plus
  local declared sets only and record the gap as a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.75  |
| entropy_pred        | 2.4   |
| impact_pred         | 85.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 5.0   |
| ev_pred             | 57.53 |

### Step Metrics Rationale

External-tool behaviour and branch discovery make this the least certain step.
`EV = 0.75 * 85.0 + 0.5 * 5.0 - 0.3 * 2.4 - 8.0 = 63.75 + 2.50 - 0.72 - 8.00 = 57.53`.

---

## Step 4: Wire admission into `holon execute` and `holon flow` Stage 3, `--allow-overlap` and ACT-OVERLAP record

- **Sub‑intent recommendation:** NO (sequential execution branch; touches `flow.py` and `cli.py`, so it must not run
  concurrently with anything)
- **Reasoning:** Surgical edits in hot files using Step 3's API; risk is in placement (before any worktree/container)
  and checkpoint persistence.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Stage 3 refuses to start on overlap before any agent tokens are spent, with an audited override.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-admission-wiring/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `cli.py`: add `--allow-overlap` (store_true) to the `execute` and `flow` parsers with help text explaining the block,
   the override and the ACT-OVERLAP record. In the `execute` branch of `main`, call a new host-side
   `admission.run_admission_gate(plan_branch, repo_dir, allow_overlap, log_fn)` before `dispatch_role_agent`; on refusal
   print the message to stderr and exit non-zero.
2. `flow.py`: add `allow_overlap: bool = False` to `FlowContext` (`to_dict`/`from_dict` with default False so old
   checkpoints load unchanged); pass it from the `flow` CLI handler. In `run_execute_stage`, call the gate after the
   `skip_push` check and before `check_and_resolve_stage_conflicts`, `_snapshot_child_branches` and `_dispatch_role`;
   refusal returns `_stage_failure` with the formatted message and payload `admission_conflicts`. In dry-run mode run
   the gate read-only and log the would-be refusal without failing (validation mode).
3. `run_admission_gate`: reads the own plan's `touch_set` from plans.jsonl (local, then plan branch on origin), calls
   `evaluate_admission`; if refused or fail-closed and `allow_overlap` is set, append an action record via
   `append_action_record()` under `LedgerLock` to `holon-knowledge/ledger/executions.jsonl` with `entry_type` 'action',
   `action_id` from `generate_action_id("ACT-OVERLAP")`, plan branch, overlapping files and conflicting branches/PRs (or
   the fail-closed reason), then admit. The record is committed with the execution's normal ledger commit path (no new
   ledger file, no layout change).
4. `cli.py` `run_docker_container` / `dispatch_role_agent`: add `--label holon.role=<role>` and
   `--label holon.plan_branch=<branch>` for executor containers so Step 3 can see local in-flight executions.
5. Tests (declared test changes) in `tests/test_cli.py` and `tests/test_flow.py`: refusal stops before dispatcher is
   called (dispatcher mock asserted not called); `--allow-overlap` admits and appends exactly one ACT-OVERLAP row with
   files and branches; fail closed on `gh` error without the flag; checkpoint round-trip of `allow_overlap`; docker
   label arguments present.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only, via `append_action_record`); intent
  constraint "no auxiliary ledgers".
- **Potential failure modes for this step:** Gate placed after the conflict-resolution step that already touches the
  worktree; existing flow tests break because the gate calls `gh`.
- **Guardrails and early‑abort checks:** Inject the admission runner through `FlowContext` (like `dispatcher`) so
  existing tests get a default "admitted" stub via `conftest.py`; assert ordering with a call-order mock.

### Success & Discard Criteria

- **Success:** Stage 3 and `holon execute` both refuse on overlap before dispatch; override writes ACT-OVERLAP; whole
  test suite green.
- **Discard:** If more than about 20 existing flow tests need edits, stop and introduce the default stub in
  `conftest.py` first as a separate commit.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 2.0   |
| impact_pred         | 80.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 56.30 |

### Step Metrics Rationale

Hot-file edits with many existing tests around them.
`EV = 0.78 * 80.0 + 0.5 * 3.0 - 0.3 * 2.0 - 7.0 = 62.40 + 1.50 - 0.60 - 7.00 = 56.30`.

---

## Step 5: Post-Stage-3 enforcement of the declared touch set

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small addition next to the existing empty-diff guard, reusing `changed_paths()` and the superseding-row
  writer.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** An execution that changes files outside its declared touch set fails Stage 3 and the failure is
  recorded in executions.jsonl.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-touch-set-enforcement/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow.py` `run_execute_stage`: after the empty-diff guard (non-empty `changed`) and before the success checks, load
   the plan's touch set and compute `out_of_set(touch_set, changed)` (metadata ignored, including `intents/`). If not
   empty, append a superseding row through `_append_superseding_execution_row` with status 'failed', `failure_reason`
   'out_of_touch_set' and the listed paths in `summary` and an `out_of_set_paths` field, then return `_stage_failure`
   with a message listing the paths and advising to re-plan with an updated touch set. Mirror the empty-diff guard's
   handling when the container already recorded a failure or the write fails.
2. `entrypoint/executor.py`: next to its `has_effective_diff` guard, apply the same check so standalone `holon execute`
   records status 'failed' with `failure_reason` 'out_of_touch_set' in its own executions.jsonl row before pushing;
   plans without `touch_set` (legacy) skip enforcement with a logged warning.
3. Add `OUT_OF_SET_FAILURE_REASON` next to `EMPTY_DIFF_FAILURE_REASON` and document it in `docs/ledger_schema.md`.
4. Tests (declared) in `tests/test_flow.py` and `tests/test_executor.py`: out-of-set diff fails with listed paths and
   superseding row; in-set diff passes; only-metadata extra files pass; legacy plan skips enforcement.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (superseding rows, never rewrite).
- **Potential failure modes for this step:** Executor container reading touch set from a plans.jsonl row that differs
  from the host's; formatter-generated incidental changes (lock files) counted as out-of-set.
- **Guardrails and early‑abort checks:** Planner prompt (Step 2) tells agents to declare `uv.lock` and generated files
  when dependencies or formatting can change them; tests cover a lock-file case.

### Success & Discard Criteria

- **Success:** Enforcement tests pass; failure row visible in executions.jsonl in the test repo.
- **Discard:** If the executor-side check proves fragile, keep host-side enforcement only and note the gap in docs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.6   |
| impact_pred         | 60.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 44.52 |

### Step Metrics Rationale

Well-trodden pattern next to an existing guard.
`EV = 0.80 * 60.0 + 0.5 * 4.0 - 0.3 * 1.6 - 5.0 = 48.00 + 2.00 - 0.48 - 5.00 = 44.52`.

---

## Step 6: Stage 5 calibration of predicted versus actual touch set

- **Sub‑intent recommendation:** NO
- **Reasoning:** Additive fields in an existing report generator; low risk, high learning value over time.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Measure touch-set estimation quality (precision, recall, missed and unused paths) on every calibrated
  execution.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-touch-set-calibration/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `calibration.py`: in `parse_predicted_metrics` keep the plans.jsonl row's `touch_set`; in `generate_calibration`
   compute actual changed non-metadata paths with the existing diff logic and call `precision_recall`.
2. `format_markdown_report`: add a `## Touch Set Calibration` section with precision, recall, predicted and actual
   counts, and lists of missed and unused paths; show "not measured" when the plan has no touch set or a denominator is
   zero.
3. Include `touch_set_precision`, `touch_set_recall`, `touch_set_missed` and `touch_set_unused` in the report's
   `to_dict` and in the calibration action record written by `record_calibration_action` (existing ledger, existing
   record type; new optional keys only).
4. Tests (declared) in `tests/test_calibration.py`: exact precision/recall on a fixture diff, glob entries counted as
   used, legacy plan yields "not measured".

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent rule "fixtures only for control flow, never to claim performance"; append-only ledger.
- **Potential failure modes for this step:** Report shape change breaking stale-report detection or existing golden
  tests.
- **Guardrails and early‑abort checks:** Append the section at the end of the report; keep existing headings intact.

### Success & Discard Criteria

- **Success:** Calibration report and metrics carry the four touch-set values; tests pass.
- **Discard:** Cost above `1.5 * 4 = 6`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.2   |
| impact_pred         | 45.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 35.89 |

### Step Metrics Rationale

Additive and well isolated. `EV = 0.85 * 45.0 + 0.5 * 4.0 - 0.3 * 1.2 - 4.0 = 38.25 + 2.00 - 0.36 - 4.00 = 35.89`.

---

## Step 7: Documentation and CLI help

- **Sub‑intent recommendation:** NO
- **Reasoning:** Low-risk prose; can share an execution branch with Step 9 if convenient.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Operators and agents understand touch sets, admission, `--allow-overlap` and enforcement.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-touch-set-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `docs/planner/planning_architecture_specification.md`: Touch Set section grammar, rules, validation and re-prompt.
2. `docs/executor/execution_architecture_specification.md`: admission (sources, matching, fail-closed, self-exclusion,
   override) and out-of-set enforcement.
3. `docs/git_flow.md`: overlapping flows run sequentially; how to proceed after a refusal (wait for merge, re-plan on
   new main) and when `--allow-overlap` is acceptable.
4. `docs/ledger_schema.md`: confirm `touch_set`, ACT-OVERLAP action record and `out_of_touch_set` failure reason.
5. Ensure CLI help strings from Step 4 are complete; format all markdown with the repository prettier configuration;
   Australian English throughout.

### Dependencies & Criticality

- **Depends on:** Steps 2–6
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent constraint 7 (Australian English, markdown formatting).
- **Potential failure modes for this step:** Prettier oscillation on formulas; docs drifting from behaviour.
- **Guardrails and early‑abort checks:** Run the repository formatter twice and confirm no diff on the second run.

### Success & Discard Criteria

- **Success:** All four documents updated; formatter stable; `test_formatting.py` passes.
- **Discard:** Not applicable (low cost); trim scope rather than abandon.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 30.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 25.42 |

### Step Metrics Rationale

`EV = 0.92 * 30.0 + 0.5 * 1.0 - 0.3 * 0.6 - 2.5 = 27.60 + 0.50 - 0.18 - 2.50 = 25.42`.

---

## Step 8: Exploratory back-test of the overlap matcher on local history

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-only measurement whose result is written into the execution notes; no production code.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** File-level blocking would have serialised the #94/#95/#96 cluster while blocking fewer
  than about a third of other historically concurrent flow pairs (acceptable false-block rate).
- **Learning target:** Real-world overlap rate between concurrent flows and how often only metadata overlapped.
- **Maximum acceptable cost for this learning:** 2.5 cost units; abort if local history is insufficient.

### Intent & Git Integration

- **Step Intent:** Get evidence on whether the hard block is too aggressive before it becomes the default.
- **Git branch:**
  `I-1792988604-touch-set-estimate-and-overlap-admission/P-1792988757-claude-agent-claude-opus-5-5/E-{timestamp}-overlap-backtest/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Using local `git log` only (no remotes, no `gh`), list merged flow merge commits on `main`, their branch names, the
   time window between plan creation (plans.jsonl `created_at`) and merge, and their changed files via `changed_paths`.
2. Treat flows with overlapping windows as concurrent; apply `find_overlaps` on actual changed files (proxy for a
   perfect estimate) and report the share of concurrent pairs that would have been blocked, split by "metadata only"
   versus source overlap.
3. Record results in the execution notes (`executions/E-*.md`) as observations, explicitly labelled as a historical
   proxy, not a performance claim; no ledger or code changes.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** Intent rule "never claim performance from fixtures"; no network.
- **Potential failure modes for this step:** Shallow clone or squash merges hide branch history.
- **Guardrails and early‑abort checks:** Abort with a note if fewer than five concurrent pairs can be reconstructed.

### Success & Discard Criteria

- **Success:** A table of concurrent pairs and block decisions in the execution notes.
- **Discard:** Insufficient history or cost above 2.5.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 1.0   |
| impact_pred         | 20.0  |
| cost_pred           | 2.5   |
| learning_value_pred | 7.0   |
| ev_pred             | 14.70 |

### Step Metrics Rationale

Low impact on its own but high learning value about false blocks.
`EV = 0.70 * 20.0 + 0.5 * 7.0 - 0.3 * 1.0 - 2.5 = 14.00 + 3.50 - 0.30 - 2.50 = 14.70`.

---

## Step 9: Validation gate — lint, full test suite, formatter

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mandatory closing check; small and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the integrated change meets the repository's quality gates.
- **Git branch:** Runs on each execution branch above and once more on the intent branch after the last merge.
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Run `uv run task lint` and `uv run task test` (never unittest, never PYTHONPATH).
2. Confirm no test reached real `gh` (new autouse guards pass; grep the new tests for unpatched `subprocess` use).
3. Run the markdown formatter on changed docs, prompt and plan files and confirm convergence.
4. Confirm only `intents.jsonl`, `plans.jsonl` and `executions.jsonl` exist under `holon-knowledge/ledger/` and no new
   file layout appeared; confirm no dependency changes in `pyproject.toml`/`uv.lock`.

### Dependencies & Criticality

- **Depends on:** Steps 1–7
- **Is Bottleneck:** YES (the root PR cannot merge without it)

### Safety & Constraint Considerations

- **Relevant rules:** Intent constraint 7; `holon-config/world/ruleset.md` §3.
- **Potential failure modes for this step:** Flaky long-running tests (workspace survival stress) masking real failures.
- **Guardrails and early‑abort checks:** Re-run a failing test in isolation once before attributing it to the change.

### Success & Discard Criteria

- **Success:** Lint and tests pass; formatter stable; ledger layout unchanged.
- **Discard:** Not discardable; failures route back to the responsible step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 40.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 31.32 |

### Step Metrics Rationale

`EV = 0.85 * 40.0 + 0.5 * 1.0 - 0.3 * 0.6 - 3.0 = 34.00 + 0.50 - 0.18 - 3.00 = 31.32`.

---

## Justification

Accepting the intent as is has clearly positive EV (`19.82`): a single overlapping batch already cost roughly 10M tokens
and 141 review passes, while this plan costs an estimated 44 units and stays within the entropy budget (12.6 of 16.0).
Rejecting or deferring would leave the costliest known failure mode of the flow in place. The design keeps the gate
conservative (fail closed, file-level, glob-glob overlap treated as conflict when uncertain), audits every override in
the existing executions ledger, and measures its own estimation quality so the block can be tuned with evidence.
