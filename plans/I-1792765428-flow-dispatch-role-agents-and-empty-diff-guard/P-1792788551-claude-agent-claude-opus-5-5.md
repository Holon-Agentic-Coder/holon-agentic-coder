# Plan for I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard

- **Plan ID:** P-1792788551-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-23T20:49:12.230Z

## Planner Autonomy Summary

- **Intent handling:** REFRAME (minor; scope is accepted in full)
- **Reframed intent (if applicable):** Accept all six goals, with three clarifications grounded in the current code: (a)
  the empty-diff guard lives mainly in the executor entrypoint
  (`apps/sandbox-executor/src/sandbox_executor/entrypoint/executor.py`), because once the flow dispatches the same
  executor container as `holon execute`, one guard covers both paths. The flow adds a host-side check that reuses the
  same helper and blocks Stage 4 PR creation. (b) `holon intent` currently launches `holon/orchestrator` with role
  `intent-creator` and a hard-coded `agent_id="antigravity"`. "The same code path" for intent therefore means that image
  and role, with the resolved runner and model forwarded instead of hard-coded; plan and execute use
  `holon/agent-<runner>`. (c) Containers clone from `HOLON_REPO_URL` and push their branches, so the flow has to fetch
  and discover what they produced rather than assume a plan ID it picked itself.
- **Exploration stance:** balanced. Most of the work is a known refactor, but the hand-off between container and host
  (branch discovery after a dispatched container exits, and how this interacts with `--skip-push`) is unverified and
  decides whether the flow can be equivalent to the manual invocation at all. A short spike up front is cheap insurance.
- **Safety priority level:** elevated
- **Priority Justification:** `holon-config/world/constraints.md` §3 (Ledger Immutability: append-only, no rewriting
  rows) applies directly, because the guard must record a failure without editing the earlier rows written by the
  container. §4 (Model Routing) requires a deep model for refactoring core modules (`flow.py`, `cli.py`).
  `docs/safety.md` Invariant 2 (all execution sandboxed) is the reason for the change: today the flow commits code
  without any sandboxed agent. The PR gate also touches the human-review boundary (Bean 0034 halt in Stage 4).

## Exploration

- **Proportion of steps that are exploratory:** 0.25 (Step 1 EXPLORATORY, Step 4 BALANCED, out of 8 steps)
- **Justification:** The single real unknown is how the host flow sees branches and ledger rows that containers produce.
  That is resolved by a time-boxed spike (Step 1) and validated inside the bottleneck step (Step 4). Everything else is
  exploitation of patterns already in the codebase: `run_docker_container`, `AGENT_IMAGE_MAPPING`, `get_diff_numstat` in
  `git.py`, and the superseding-row (`ledger_revision >= 2`) convention in `executor.py`.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.66  |
| entropy_pred        | 13.5  |
| impact_pred         | 90.0  |
| cost_pred           | 33.0  |
| learning_value_pred | 6.5   |
| ev_pred             | 25.60 |

### Strategy Rationale

The intent is high value and low ambiguity: `flow.py` currently writes a placeholder plan (`run_plan_stage`, the
`1. Implementation step for {slug}.` template) and an execute stage that only runs
`uv run pytest apps/sandbox-executor/tests/test_cli.py` and commits the tree with `git add -A`. It then records
`test_pass_rate 1.0` and `status success`, which feeds fabricated provenance into calibration and opens PRs for empty
work. Rejecting or deferring would leave the flow actively harmful, so the intent is accepted.

The ordering is: spike (1) → behaviour-preserving extraction of shared dispatch functions in `cli.py` (2) → shared diff
helper in `git.py` (3) → rewiring the flow stages (4, bottleneck) → empty-diff guard in the executor entrypoint, the
flow and the PR path (5) → tests (6) → docs (7) → verification gate (8).

Aggregation:

- **p_success:** Steps can be retried inside the plan, so the naive product of step probabilities (`≈ 0.25`) understates
  success. The bottleneck is Step 4 (`p = 0.70`), lowered slightly for the coupled test rewrite in Step 6:
  `p_overall ≈ 0.66`.
- **Entropy:** sum of steps, `0.6 + 2.0 + 0.8 + 4.5 + 2.8 + 2.0 + 0.6 + 0.2 = 13.5`.
- **Cost:** sum of steps, `2 + 4 + 2 + 9 + 6 + 6 + 2 + 2 = 33.0`.
- **Impact:** the intent-level value of restoring provenance integrity, judged qualitatively at 90 rather than the sum
  of step impacts.
- **Learning value:** 6.5, dominated by Step 1 and Step 4.
- **EV:** `EV = 0.66 * 90.0 + 0.5 * 6.5 - 0.3 * 13.5 - 33.0 = 59.40 + 3.25 - 4.05 - 33.0 = 25.60`, with lambda and mu
  taken from `holon-config/metrics/ev_config.json`.

Strong assumptions:

- Role containers push their branches to `origin`, so the host flow can fetch them.
- `--dry-run` keeps skipping all external dispatch.
- Existing tests that depend on the template plan may be rewritten. This is declared here, as `ruleset.md` §3 requires
  for any change to tests.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1: branch prefixes `I-.../P-.../E-.../_`; never commit to `main`.
  - `constraints.md` §2: entropy 13.5 is in the 10–30 band, so Container Sandbox.
  - `constraints.md` §3: ledgers are append-only.
  - `constraints.md` §4: deep model required for refactoring the core modules.
  - `holon-config/world/ruleset.md` §1: `uv`, no new dependencies, Python `==3.13.*`.
  - `ruleset.md` §2: typed public functions with docstrings.
  - `ruleset.md` §3: pytest only; test changes must be declared in the plan.
  - `docs/safety.md` Invariants 2 (sandboxing), 4 (human review; Bean 0034 halt) and 6 (immutability).
  - Intent constraint 6: Australian English; only `intents.jsonl`, `plans.jsonl` and `executions.jsonl`; paths stay
    target-native.
- **Potential violations or edge cases:**
  - Double ledger rows. The flow's intent, plan and execute stages currently append their own rows. Once they dispatch
    containers that also append, each stage would produce duplicates.
  - The guard firing after the container has already appended a `success` row. That row cannot be edited.
  - Circular import: `cli.py` imports `flow.py` at module level.
  - `--skip-push` combined with dispatch. A container cannot fetch an intent or plan branch that was never pushed.
  - Status vocabulary. The executor entrypoint writes `"failure"`; the flow and the intent require `"failed"`.
  - The PR path. `create_or_update_pr` is reachable from `run_review_loop`, so an empty execution could still get a PR
    through the review stage or `holon review-loop`.
- **Mitigations built into the plan:**
  - Drop the flow-local ledger appends for stages that a container now owns.
  - Record a guard failure as a superseding row (`ledger_revision: 2`) that repeats all authoritative fields, with
    `status: "failed"`, `failure_reason: "empty_diff"`, and no `test_pass_rate` (or `0.0`).
  - Avoid the import cycle with a lazy `from sandbox_executor import cli` inside the flow stages; this lazy-import
    pattern already exists for `get_host_proxy_envs`.
  - If `skip_push` is set and dispatch is needed, fail fast with a clear message.
  - Normalise guard rows to `"failed"`. `knowledge/records.py` already accepts both forms.
  - Gate both Stage 4 and `run_review_loop` before `create_or_update_pr`.
- **Residual risk accepted (and why):** Real container runs (Docker plus network plus credentials) are not exercised in
  CI. The dispatcher is stubbed in tests, so the end-to-end equivalence claim rests on both paths sharing the same
  function, which is the design goal of the intent. A manual smoke run is recommended but is not a merge gate.
- **Allocated Entropy Budget:** 14.0
- **Predicted Plan Entropy:** 13.5
- **Budget Compliance:** The strategy fits within budget, with 0.5 headroom. If Step 1 shows a different hand-off model
  (for example, containers do not push), Step 4 entropy could rise above budget; that is handled by Step 1's discard
  criterion.

## Plan Description & Strategy

The goal is one shared implementation per role, used by both `holon <stage>` and `holon flow`:

- **Dispatch.** `cli.py` gains a typed public function
  `dispatch_role_agent(role, runner, model, positional_args, *, repo_dir, intent_file, token_reduce, mitm_web) -> int`.
  It resolves the image through `AGENT_IMAGE_MAPPING` (`holon/agent-<runner>` for planner and executor,
  `holon/orchestrator` for intent-creator), normalises `agent_id` the same way `main()` does today, and calls
  `run_docker_container`.
  - The three `args.command` branches in `main()` become thin wrappers around it.
  - Flow Stages 1–3 call the same function through a `dispatcher` callable on `FlowContext`. It defaults to
    `cli.dispatch_role_agent`, is not serialised into checkpoints, and is what tests stub.
- **Discovery.** After a container exits, the flow fetches `origin` and finds the produced branch under the parent
  prefix: the newest `P-*` under the intent and the newest `E-*` under the plan. It then reads `plans/P-*.md` from that
  branch.
  - There is no template fallback. If nothing is found, Stage 2 fails with a message naming the intent branch and the
    expected `plans/P-*.md` path.
- **Guard.** `git.py` gains `has_effective_diff(repo_dir, base, head, exclude=("holon-knowledge/ledger/", "plans/"))`.
  - The executor entrypoint calls it before writing its summary row. An empty diff gives `status: "failed"`, no code
    push, and no `test_pass_rate`.
  - The flow re-checks after fetching. If a `success` row exists but the diff is empty, it appends a superseding failed
    row; either way it marks Stage 3 FAILED, so Stage 4 (and `create_or_update_pr`) never runs.
  - `run_review_loop` also refuses to create or update a PR for an empty diff, which covers manual `holon review-loop`.

Execution branches (one per step group) are created under
`I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5/`. Step groupings
marked as sub-intent candidates are recommendations only; under baseline trust (`docs/safety.md` trust table) the agent
cannot spawn sub-intents, so a human operator would have to create them.

---

## Step 1: Spike — confirm the container-to-host hand-off contract

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only, cheap and time-boxed, but it settles the riskiest assumption in Step 4, so it runs first and
  stays inside the plan branch.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** The planner, executor and intent-creator entrypoints clone from `HOLON_REPO_URL`, create
  branches named `{intent}/P-{ts}-{agent}-{model}/_` and `{plan}/E-{ts}-.../_`, and push them to `origin`. A host-side
  `git fetch origin` followed by a prefix scan therefore finds them reliably.
- **Learning target:** For each role:
  - the exact branch-naming and push behaviour;
  - whether `--skip-push` is forwarded into containers;
  - what `intent_creator.py` expects for `intent_file` and agent ID;
  - which ledger rows each container appends, which tells us which flow-local appends must be removed;
  - whether the review loop or any other code path calls `gh pr create`.
- **Maximum acceptable cost for this learning:** about 2 cost units (no code changes, reading only).

### Intent & Git Integration

- **Step Intent:** Produce a short written hand-off contract (kept in the execution log, not as a new ledger) that Steps
  2, 4 and 5 implement against.
- **Git branch:** none (read-only on the plan branch)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `entrypoint/planner.py` (around line 239: `plan_md_rel`, branch creation, push), `entrypoint/executor.py`
   (`exec_branch` derivation, `exec_status` set at lines ~1321/1324/1482, summary row near line 1515, commit and push
   near line 1608) and `entrypoint/intent_creator.py`.
2. Read `run_docker_container` in `cli.py` (line ~926) to see which env vars are forwarded (`HOLON_*`, `HOLON_REPO_URL`)
   and how `intent_file` is mounted.
3. Grep for `create_or_update_pr` and `"gh", "pr", "create"` callers. Known so far: `flow.py:2582` in `run_review_loop`.
4. Record the decisions:
   - discovery rule: newest timestamp under the prefix, with ties broken by agent/model match;
   - `--skip-push` policy: fail fast;
   - status vocabulary: `"failed"` for guard rows.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §1 (branch prefixes), `safety.md` Invariant 2
- **Potential failure modes for this step:** Containers might not push (they might, for example, rely on a shared
  mount), which would invalidate the fetch-and-discover design.
- **Guardrails and early‑abort checks:** Read-only. No Docker invocation and no network.

### Success & Discard Criteria

- **Success:** A contract stating, for each role, the branch pattern, the ledger rows it appends and its push behaviour,
  plus the three decisions above.
- **Discard:** If containers do not push and there is no shared mount, stop and escalate. Step 4 would need a different
  hand-off design whose entropy would likely exceed the 14.0 budget.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 7.0   |
| ev_pred             | 9.82  |

### Step Metrics Rationale

`EV = 0.85 * 10.0 + 0.5 * 7.0 - 0.3 * 0.6 - 2.0 = 8.50 + 3.50 - 0.18 - 2.0 = 9.82`. Learning value is high because the
result determines the design of the bottleneck step. Entropy is near zero because nothing is modified.

---

## Step 2: Extract shared role-dispatch functions in `cli.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A behaviour-preserving refactor of about 60 lines that existing CLI tests already cover; it is small
  and reusable, so an execution branch is enough.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One public, typed and documented entry point per role that both `holon intent|plan|execute` and the
  flow call. This satisfies "shared functions, not duplicated logic".
- **Git branch:**
  `I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5/E-{ts}-shared-dispatch/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `normalise_agent_id(agent: str | None) -> str`, lifting the expression at `cli.py:2075`
   (`replace("-agent", "").replace("agent-", "")`, defaulting to `antigravity`).
2. Add `resolve_role_image(role: str, agent_id: str) -> str`:
   - `intent-creator` maps to `holon/orchestrator`, unchanged;
   - `planner` and `executor` map to `AGENT_IMAGE_MAPPING.get(agent_id, f"holon/agent-{agent_id}")`.
3. Add
   `dispatch_role_agent(role, runner, model, positional_args, *, repo_dir=None, intent_file=None, token_reduce=False, mitm_web=False) -> int`.
   - It builds the kwargs exactly as the three branches do today and calls `run_docker_container`.
   - For intent-creator it now forwards the resolved runner as `agent_id` and exports the model through `HOLON_MODEL`
     instead of hard-coding `antigravity`. Before relying on this, check against the Step 1 contract that
     `intent_creator.py` tolerates it.
   - Docstring in Australian English: "normalises", "initialises".
4. Rewrite the `intent`, `plan` and `execute` branches of `main()` as `sys.exit(dispatch_role_agent(...))`, with the
   same positional args (`[intent_branch, agent, model]` and `[plan_branch, agent, model]`).
5. Keep `run_docker_container` in `cli.py`, so existing `patch("sandbox_executor.cli.run_docker_container")` tests keep
   working.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §2 (typing, docstrings), `constraints.md` §4 (deep model for core modules), intent
  constraint 6 (no new dependencies)
- **Potential failure modes for this step:** Silently changing the per-stage commands' image or args. The intent-creator
  agent ID change could alter credential-bundle selection in `role_dispatcher.sh`.
- **Guardrails and early‑abort checks:** Run `uv run pytest tests/test_cli.py tests/test_container_images.py` before and
  after; their results must be identical apart from new tests.

### Success & Discard Criteria

- **Success:** The three per-stage commands produce the same `docker run` argv as before (asserted in Step 6), and
  `ruff check` is clean.
- **Discard:** If keeping intent-creator's argv identical needs special cases in more than three places, keep
  `antigravity` as the intent default and record the deviation in the docs (Step 7).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 20.0  |
| cost_pred           | 4.0   |
| learning_value_pred | 3.0   |
| ev_pred             | 14.90 |

### Step Metrics Rationale

`EV = 0.90 * 20.0 + 0.5 * 3.0 - 0.3 * 2.0 - 4.0 = 18.00 + 1.50 - 0.60 - 4.0 = 14.90`. This is a well-trodden refactor
with existing test coverage; entropy comes from touching a hot file (`cli.py`, about 2.1k lines).

---

## Step 3: Shared `has_effective_diff` helper in `git.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** A small, pure helper with low risk that is used in three places (the executor entrypoint, flow Stage 3
  and the review-loop PR gate).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One definition of an "empty execution": no changed paths between the plan branch and the execution
  head once `holon-knowledge/ledger/` and `plans/` are excluded.
- **Git branch:** same execution branch as Step 2 (`.../E-{ts}-shared-dispatch/_`)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add `EMPTY_DIFF_EXCLUDES: tuple[str, ...] = ("holon-knowledge/ledger/", "plans/")`.
2. Add `changed_paths(repo_dir, base, head, exclude=EMPTY_DIFF_EXCLUDES) -> list[str]`, built on `run_git`.
   - It runs `git diff --name-only {base}...{head} -- . ':(exclude)holon-knowledge/ledger/' ':(exclude)plans/'`.
   - When `head` is the working tree, it also includes staged changes (`--cached` against `base`). This lets the
     executor entrypoint check before committing.
   - It returns sorted paths. It raises `RuntimeError` on git failure, so the guard fails closed: an unknown diff is
     treated as failure, not success.
3. Add `has_effective_diff(...) -> bool` as a thin wrapper around `changed_paths`.
4. Keep the exclusions relative and target-native. Do not add `.holon/` to the exclusions, because the intent does not
   ask for it.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 6 (target-native paths), `ruleset.md` §2
- **Potential failure modes for this step:** Pathspec exclude syntax on old git versions. Renames counting as changes.
  The three-dot versus two-dot range picking the wrong base after a rebase.
- **Guardrails and early‑abort checks:** Unit-test against a real temporary git repo (the pattern already used in
  `tests/test_git.py`), covering ledger-only, plans-only, mixed and rename cases.

### Success & Discard Criteria

- **Success:** All `test_git.py` cases pass, including the four new ones.
- **Discard:** If pathspec excludes behave inconsistently, fall back to filtering `--name-only` output in Python. This
  does not change entropy materially.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.93  |
| entropy_pred        | 0.8   |
| impact_pred         | 15.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 12.71 |

### Step Metrics Rationale

`EV = 0.93 * 15.0 + 0.5 * 2.0 - 0.3 * 0.8 - 2.0 = 13.95 + 1.00 - 0.24 - 2.0 = 12.71`. This is a deterministic utility on
top of an existing wrapper, so p is high.

---

## Step 4: Rewire flow Stages 1–3 to dispatch role agents; remove the template-plan writer

- **Sub‑intent recommendation:** YES
- **Reasoning:** This is the bottleneck: it touches the largest file (`flow.py`, about 3.4k lines), changes stage
  semantics, and invalidates several existing tests. A separate evaluation branch would isolate it, but
  `p_success = 0.70 >= 0.6`, so STRONGLY_YES is not required. Under baseline trust a human must create the sub-intent;
  otherwise it runs as an execution branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** If Stages 1–3 dispatch and then fetch and discover, the flow becomes equivalent to the
  manual sequence `holon intent` → `holon plan <intent_branch>` → `holon execute <plan_branch>`, with no flow-only
  ledger rows.
- **Learning target:** Whether discovery is deterministic when several plans or executions exist under one prefix, and
  how much existing flow behaviour (consult, veto, conflict resolution, checkpoints) survives unchanged.
- **Maximum acceptable cost for this learning:** 1.5 times this step's cost (13.5 units) before escalating.

### Intent & Git Integration

- **Step Intent:** The flow never authors content itself; containers author it, and the flow orchestrates, validates and
  records stage results.
- **Git branch:**
  `I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5/E-{ts}-flow-dispatch/_`
- **Sub‑intent:** NEW (recommended; fall back to an execution branch if sub-intent spawning is not permitted)

### Implementation Details (No code blocks, only logic/steps)

1. **`FlowContext`.**
   - Add `dispatcher: Callable[..., int] | None = field(default=None, repr=False, compare=False)`.
   - Exclude it from `to_dict` and `from_dict`. The checkpoint schema version is unchanged because the field is never
     persisted.
   - Add a helper `_get_dispatcher(context)` that returns `context.dispatcher`, or lazily imports
     `sandbox_executor.cli.dispatch_role_agent` to avoid the module-level import cycle.
2. **Stage 1 (`run_intent_stage`).**
   - Keep validation: goal/description, `validate_intent_questions`, slug sanitising, the consult step and
     `check_and_resolve_stage_conflicts`.
   - Replace the local branch creation, `intents.jsonl` append and commit with: write the normalised intent payload to a
     temporary file, then call the dispatcher with `role="intent-creator"`, the resolved runner and model,
     `intent_file`, `repo_dir`, `token_reduce` and `mitm_web`.
   - A non-zero exit fails the stage. On success, `git fetch origin` and verify that `{branch}/_` exists. If it does
     not, fail with a message naming the expected branch.
3. **Stage 2 (`run_plan_stage`).**
   - Delete the template writer: the `plan_content` f-string, the hard-coded default metrics dict used as provenance,
     the local `plans.jsonl` append, the commit and `converge_prettier`. The planner container owns all of these.
   - Dispatch `role="planner"` with positional args `[context.intent_branch, context.agent, context.model]`, exactly as
     `holon plan` does.
   - Then fetch and discover the newest `{intent_prefix}/P-*/_` branch, and read `plans/P-*.md` from it with `git show`.
   - If no branch exists, or it contains no `plans/P-*.md`, return FAILED with the error
     `"Plan stage failed: no planner agent produced plans/P-*.md on a plan branch under {intent_branch}; refusing to fabricate a plan."`
   - Otherwise set `context.plan_branch`, parse the metrics with the existing `parse_metrics` for the payload only (no
     ledger write), and return SUCCESS.
4. **Stage 3 (`run_execute_stage`).**
   - Keep the veto check.
   - Remove the hard-coded `uv run pytest apps/sandbox-executor/tests/test_cli.py` run, the `git add -A` commit and the
     flow-authored `executions.jsonl` summary row.
   - Dispatch `role="executor"` with `[context.plan_branch, context.agent, context.model]`.
   - Then fetch and discover the newest `{plan_prefix}/E-*/_` branch. Read its `executions.jsonl` summary row
     (`execution_id`, `status`) for the payload. The empty-diff check is added in Step 5.
   - Carry `token_reduce` and `mitm_web` through to dispatch. The direct `get_host_proxy_envs` subprocess env is no
     longer needed, because `run_docker_container` handles proxy mounts.
5. **`--skip-push`.** If `context.skip_push` is set and the stage is not a dry run, fail Stages 1–3 with: "holon flow
   dispatches containerised role agents that fetch from origin; --skip-push is incompatible with dispatch." Also update
   the `--skip-push` help text in `cli.py`.
6. **`--dry-run`.** Unchanged: no dispatch, and the branch names are computed as today, for validation only.
7. **Comments and logs.** All new comments and log strings use Australian English ("initialise", "normalise",
   "behaviour").

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES

### Safety & Constraint Considerations

- **Relevant rules:**
  - `constraints.md` §1: the flow checks out only intent, plan and execution branches.
  - `constraints.md` §3: removing flow-local appends prevents duplicate rows; existing rows are never touched.
  - `safety.md` Invariant 2: code is now produced only inside containers.
  - Intent constraint 6: only the three ledgers.
- **Potential failure modes for this step:**
  - Discovery picks a stale branch from an earlier run.
  - A checkpoint resumed from `--from-stage execute` has no dispatcher, which is fine because the default is lazy.
  - The intent-creator container rejects the forwarded runner.
  - Removing the local ledger writes breaks calibration (Stage 5), which reads `plans.jsonl`.
- **Guardrails and early‑abort checks:**
  - Discovery filters for branches newer than the stage start time where the timestamp can be parsed, and fails if the
    result is ambiguous.
  - Re-run the existing Stage 5 tests against ledger rows written by the stubbed containers.
  - Abort and re-plan if Stage 5 needs flow-written rows.

### Success & Discard Criteria

- **Success:**
  - `grep` finds no `Implementation step for` in `src/`.
  - Stages 1–3 call the dispatcher exactly once each, with the resolved runner and model.
  - No flow-authored rows are appended to `intents.jsonl`, `plans.jsonl` or `executions.jsonl`.
- **Discard:** If discovery cannot be made deterministic within 1.5 times the cost, or entropy is observed to exceed
  6.0, stop and split into a sub-intent "flow branch discovery contract" for human approval.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 4.5   |
| impact_pred         | 35.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 6.0   |
| ev_pred             | 17.15 |

### Step Metrics Rationale

`EV = 0.70 * 35.0 + 0.5 * 6.0 - 0.3 * 4.5 - 9.0 = 24.50 + 3.00 - 1.35 - 9.0 = 17.15`. This step carries the largest
surface area: about 400 lines changed in a hot file. The conflict likelihood comes from other open plans that also touch
`flow.py`, and novelty from the fetch-and-discover hand-off. Learning value comes from validating that hand-off
contract.

---

## Step 5: Empty-diff guard in the executor entrypoint, flow Stage 3 and the PR path

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is a moderate change that reuses Step 3. It is tightly coupled to Step 4's Stage 3 rewrite, so it
  shares that change's review context, but it lives on its own execution branch for clear provenance.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** An execution with no effective diff can never be recorded as success, never carries
  `test_pass_rate: 1.0`, and never gets a PR, in both `holon execute` and `holon flow`.
- **Git branch:**
  `I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5/E-{ts}-empty-diff-guard/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **Executor entrypoint (`entrypoint/executor.py`).** Run the guard after the agent run sets `exec_status` (around
   line 1321) and before the summary row is built (around line 1505).
   - Stage the agent's changes and call `has_effective_diff(repo_dir, plan_branch_tip, working tree / index)`.
   - If the diff is empty and `exec_status == "success"`:
     - set `exec_status = "failed"`;
     - set `summary` to "Execution produced no changes outside holon-knowledge/ledger/ and plans/ relative to
       {plan_branch}";
     - add `failure_reason: "empty_diff"`.
   - Make sure no `test_pass_rate` key is written on that path.
   - Only the ledger row is committed and pushed, which the existing `exec_status == "success"` gate on `git add -A`
     (line ~1600) already enforces.
   - If the diff computation raises, record `"failed"` (fail closed).
2. **Flow Stage 3**, after discovering the execution branch:
   - Call `has_effective_diff(repo_dir, context.plan_branch, exec_branch)` on the fetched refs.
   - If the diff is empty, append a superseding `executions.jsonl` row with:
     - the same `execution_id` and `ledger_revision: 2`;
     - every authoritative field repeated, following the existing convention in `executor.py`;
     - `status: "failed"` and `failure_reason: "empty_diff"`;
     - no `test_pass_rate`. Do this only if the container's row did not already say failed, so there are no duplicate
       failure rows. Then return StageStatus.FAILED.
   - Commit that single ledger row on the execution branch with the existing Executor commit identity.
   - The flow's `has_failure` logic then stops the pipeline before Stage 4, so `create_or_update_pr` is never reached.
3. **`run_review_loop`** (`flow.py` near line 2582): before `create_or_update_pr`, if the target is an execution branch
   whose parent plan branch is derivable from its prefix and `has_effective_diff` is false, skip PR creation and return
   a failed result with a clear message. This covers manual `holon review-loop` on an empty execution.
4. **Status vocabulary.** Guard rows use `"failed"`, as the intent requires. Leave the existing executor `"failure"`
   rows untouched, since the ledger is append-only and the vocabulary is out of scope. Confirm that
   `knowledge/records.py` and `calibration.py` treat both values as failure; if calibration only accepts one, add
   `"failed"` to its accepted set.

### Dependencies & Criticality

- **Depends on:** Step 3, Step 4
- **Is Bottleneck:** NO (Goal 3 can still land without Step 4 for the per-stage command, so the impact of a failure here
  is partial)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 (append-only: superseding row, never edit), `safety.md` Invariant 4 and Bean
  0034 (no autonomous PR progression), intent constraint 6 (only `executions.jsonl`)
- **Potential failure modes for this step:**
  - The container commits before the guard runs, so the staged diff is empty even though the commit is not.
  - The plan-branch tip is not available inside the container after recovery re-init.
  - The review-loop gate wrongly blocks review of legitimate non-execution branches.
- **Guardrails and early‑abort checks:**
  - Compute the diff against `base_tip`/`FETCH_HEAD`, which the executor already resolves.
  - Gate the review loop only when the branch matches the `/E-` pattern.
  - Fail closed on git errors.

### Success & Discard Criteria

- **Success:** An empty execution produces a `status: "failed"` row without `test_pass_rate: 1.0`. A stubbed
  `gh pr create` is never called. Non-empty executions behave exactly as before.
- **Discard:** If guarding inside the container requires restructuring the recovery path (lines ~1430–1500), keep only
  the flow-side and review-loop guards, and record the per-stage gap as a follow-up intent.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 2.8   |
| impact_pred         | 25.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 15.16 |

### Step Metrics Rationale

`EV = 0.80 * 25.0 + 0.5 * 4.0 - 0.3 * 2.8 - 6.0 = 20.00 + 2.00 - 0.84 - 6.0 = 15.16`. The change touches three hot paths
(the executor entrypoint, flow and review loop). Irreversibility is low because the ledger is only ever appended to.

---

## Step 6: Tests — dispatcher stub, no-plan failure, empty-diff failure, rewritten template-dependent tests

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests are required (Goal 4) and must ship on the same branches as the code they cover. They are
  declared here because `ruleset.md` §3 requires test changes to be declared in the plan.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Turn every guarantee in the intent into a pytest assertion, and rewrite the tests that depended on
  the template plan or the hard-coded pytest run.
- **Git branch:** tests land alongside Steps 2–5 on their respective execution branches
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. **`tests/test_flow.py`, new tests:**
   - **Dispatcher invocation.** A recording stub dispatcher simulates each container by creating the expected branch,
     plan file, change and ledger row in a `tmp_path` git repo with a local bare `origin`. The test runs the pipeline
     (review and calibration skipped) and asserts three calls with roles `intent-creator`, `planner` and `executor`,
     each carrying the resolved runner (for example `claude-agent`) and model.
   - **Silent planner.** A stub planner that writes nothing makes Stage 2 FAILED with "refusing to fabricate a plan". No
     `plans/P-*.md` exists, `plans.jsonl` is unchanged, and Stage 3 is never dispatched.
   - **Empty execution.** A stub executor that commits only a ledger row gives Stage 3 FAILED, and the newest
     `executions.jsonl` row for that `execution_id` has `status: "failed"` and no `test_pass_rate: 1.0`. With
     `shutil.which("gh")` and `subprocess.run` patched, the test asserts no call containing `["gh", "pr", "create"]`
     and, as a second check, no `create_or_update_pr` call.
   - **`--skip-push`.** Combining `--skip-push` with dispatch fails fast with the documented message.
   - **Checkpoints.** A checkpoint round-trip does not serialise `dispatcher`.
2. **`tests/test_flow.py`, rewritten tests** that currently assume the template or the hard-coded pytest run:
   `test_stage2_plan_generation_and_ev`, `test_stage3_execute_success`, `test_stage3_execute_failure`,
   `test_stage2_plan_invokes_converge_prettier`, `test_stage3_execute_invokes_converge_prettier` and
   `test_flow_stage_environment_propagation`.
   - Rewrite them to use the stub dispatcher.
   - Drop the converge_prettier-on-plan assertions from the flow, since the planner container owns formatting.
   - Keep the equivalent assertions in `test_planner.py` where they already exist.
3. **`tests/test_cli.py`:**
   - For each of `intent`, `plan` and `execute`, patch `run_docker_container` and assert the same image, role and
     positional args as before.
   - Assert that `dispatch_role_agent` is the single path, by patching it and checking that `main()` calls it.
4. **`tests/test_git.py`:** the four `has_effective_diff` cases from Step 3, plus fail-closed behaviour on a bad ref.
5. **`tests/test_executor.py`:** an entrypoint run whose agent makes no file changes gives a summary row with
   `status: "failed"` and `failure_reason: "empty_diff"`, and `git add -A` is not called.
6. **`tests/test_review_loop.py`:** `run_review_loop` on an empty `/E-` branch does not call `create_or_update_pr`.
7. Run everything strictly with `uv run pytest` from `apps/sandbox-executor`: never unittest, never `PYTHONPATH`.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4 and 5
- **Is Bottleneck:** NO (but a failure here blocks Step 8)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 (pytest, declared test changes), intent constraint 6 (`uv run pytest` only),
  `tests/hermetic_fixtures.py` and `test_sandbox_hermetic_guard.py` (no real Docker or network in tests)
- **Potential failure modes for this step:**
  - The stub dispatcher's simulated containers drift from real entrypoint behaviour.
  - The hermetic guard rejects the local bare-repo `origin`.
  - Flaky timestamps in discovery.
- **Guardrails and early‑abort checks:**
  - Reuse the existing hermetic fixtures.
  - Use injected or monotonically increasing timestamps in the stubs.
  - Never call real `docker` or `gh`.

### Success & Discard Criteria

- **Success:** The full `uv run pytest` suite is green, and the new tests fail when the corresponding production change
  is reverted (spot-check one guard by temporarily inverting it locally; that change is not committed).
- **Discard:** If the hermetic guard forbids a bare `origin`, stub `run_git` for fetch and discovery instead. Discard
  this approach only if the result can no longer assert real diffs.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 2.0   |
| impact_pred         | 15.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 7.10  |

### Step Metrics Rationale

`EV = 0.78 * 15.0 + 0.5 * 4.0 - 0.3 * 2.0 - 6.0 = 11.70 + 2.00 - 0.60 - 6.0 = 7.10`. Rewriting about six existing tests
plus around ten new ones costs a lot. p is held down by fixture interplay with the hermetic guard.

---

## Step 7: Documentation — state the flow guarantee accurately

- **Sub‑intent recommendation:** NO
- **Reasoning:** A low-risk documentation edit, formatted with the repository Prettier configuration.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** The docs say that `holon flow` is equivalent to running `holon intent`, `holon plan`,
  `holon execute`, `holon review-loop` and `holon calibrate` by hand. They also describe the no-fabrication rule, the
  empty-diff guard and the `--skip-push` incompatibility.
- **Git branch:**
  `I-1792765428-flow-dispatch-role-agents-and-empty-diff-guard/P-1792788551-claude-agent-claude-opus-5-5/E-{ts}-flow-docs/_`
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `apps/sandbox-executor/README.md`: update the `holon flow` section (around lines 40 and 84–99) and the `flow.py`
   description. Remove any implication that the flow authors plans or runs a fixed test file.
2. `docs/sandbox/execute_plan.md` and `docs/sandbox/create_plan.md`: note that the flow dispatches the same container
   per role; document the empty-diff failure (`status: "failed"`, `failure_reason: "empty_diff"`, no PR) and the
   excluded paths.
3. `docs/ledger_schema.md`: document the `failure_reason` field and the superseding-row semantics for guard failures.
4. `docs/architecture.md` or `docs/git_flow.md`, whichever currently describes the flow lifecycle: add one paragraph
   stating the equivalence guarantee and its precise limits (dry-run does not dispatch; `--skip-push` is rejected).
5. Use Australian English throughout. Format with the repository's `.prettierrc.json` via the existing
   `converge_prettier` or `make` target, and do not reformat unrelated documents.

### Dependencies & Criticality

- **Depends on:** Steps 4 and 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 6 (markdown formatted with the repository configuration, Australian English)
- **Potential failure modes for this step:** Overclaiming equivalence (for example, intent image differences), or
  Prettier oscillation on formulas.
- **Guardrails and early‑abort checks:** State the intent-creator image difference explicitly. Keep any arithmetic in
  code spans.

### Success & Discard Criteria

- **Success:** `prettier --check` is clean on the touched docs, and every claim matches the code merged in Steps 2–5.
- **Discard:** Not applicable (a low-cost step); fix forward.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.92  |
| entropy_pred        | 0.6   |
| impact_pred         | 6.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 3.84  |

### Step Metrics Rationale

`EV = 0.92 * 6.0 + 0.5 * 1.0 - 0.3 * 0.6 - 2.0 = 5.52 + 0.50 - 0.18 - 2.0 = 3.84`. This is routine work, but it is
needed for Goal 5 and for human reviewers.

---

## Step 8: Verification gate — tests, lint, format, constraint audit

- **Sub‑intent recommendation:** NO
- **Reasoning:** A mandatory final gate. It is cheap and deterministic.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove that all intent constraints hold before the intent branch is proposed for human review.
- **Git branch:** run on the plan branch after the execution branches are merged into it
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `apps/sandbox-executor`, run `uv run pytest`, `uv run ruff check` and `uv run ruff format --check`.
2. Run Prettier check on the changed markdown files, using the repository config.
3. Audit:
   - `grep` finds no `Implementation step for` and no `apps/sandbox-executor/tests/test_cli.py` invocation in `flow.py`;
   - there are no new entries in `pyproject.toml` or `uv.lock`;
   - no new `*.jsonl` files appear under `holon-knowledge/`;
   - existing ledger lines are byte-identical to the base (the git diff on ledgers is append-only);
   - an American-spelling scan (`initialize`, `normalize`, `behavior`, `serialize`) of new comments and strings finds
     nothing.
4. Optionally, and not as a gate: a manual smoke run of `./holon flow` with a real runner on a throwaway intent. The
   human operator decides whether to run it.

### Dependencies & Criticality

- **Depends on:** Steps 2–7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent constraint 6 in full, `constraints.md` §3, `scripts/pre_commit_check.py`
- **Potential failure modes for this step:** Unrelated pre-existing failures, or formatter drift.
- **Guardrails and early‑abort checks:** Compare against a baseline run on the plan branch before any change, so that
  pre-existing failures are not attributed to this plan.

### Success & Discard Criteria

- **Success:** All commands exit 0 and the audit is clean.
- **Discard:** Not applicable. Fix forward in the owning step's branch.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.2   |
| impact_pred         | 6.0   |
| cost_pred           | 2.0   |
| learning_value_pred | 2.0   |
| ev_pred             | 4.22  |

### Step Metrics Rationale

`EV = 0.88 * 6.0 + 0.5 * 2.0 - 0.3 * 0.2 - 2.0 = 5.28 + 1.00 - 0.06 - 2.0 = 4.22`. This step is deterministic checking;
its value is in catching constraint drift before the human review boundary.

---
