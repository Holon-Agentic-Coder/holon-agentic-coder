# Plan for I-1794891078-s5rerun

- **Plan ID:** P-1794891094-eede5d91-claude-agent-claude-fable-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-fable-5 (version: 2.1.302)
- **Created At:** 2026-11-17T04:51:36.057Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A — the intent is well-posed, the evidence (PR #193, PR #190) is concrete, and
  every building block a rerun needs already exists in the codebase. The planner's delegated decision (where the trigger
  lives) is resolved below: detection and rerun orchestration live in one new module, `flow/calibrate_rerun.py`, invoked
  from two sites — the pipeline's Stage 5 report-back block and the review loop's pre-posting gate. No long-lived
  watcher process is introduced.
- **Exploration stance:** balanced. The rerun path (`run_calibrate_stage` is already idempotent-by-construction via
  `sync_execution_ref` and `rebuild_calibrated_branch`) is exploit territory; the head-move semantics ("a calibration
  commit is not a move") need one deliberately exploratory verification step because getting them wrong creates an
  infinite rerun loop.
- **Safety priority level:** standard
- **Priority Justification:** No constraint in `holon-config/world/constraints.md` or `docs/safety.md` is elevated by
  this work: the change never merges anything (merging stays with the human maintainer, honouring the "root intent merge
  is the only human step" boundary in safety.md), touches only `flow/`, `calibration` modules and `tests/` within the
  declared scope, and appends — never rewrites — ledger rows (constraints.md §3 Ledger Immutability). Predicted plan
  entropy (12.5) sits in the container-sandbox tier (10–30) of constraints.md §2, which is the standard tier for this
  repository's flow work.

## Exploration

- **Proportion of steps that are exploratory:** 0.17 (1 of 6 steps BALANCED; the rest EXPLOIT)
- **Justification:** The failure mode this intent fixes was discovered in production (two drifted PRs), so the fix
  itself should be low-entropy exploitation of existing seams. The one genuinely open question — exactly which commit
  shapes count as "calibration commits only" between the evaluated SHA and the PR head (merge commits from a conflict
  merge, resolver commits, Stage 5's own three-commit sequence) — is probed first with cheap repository archaeology
  before any implementation, because a wrong answer either loops Stage 5 forever or never reruns it.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 12.5  |
| impact_pred         | 88.0  |
| cost_pred           | 50.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 19.2  |

### Strategy Rationale

**Intent assessment.** Accepted as-is. The exploration pass confirmed the intent's own diagnosis: the flow already owns
every mechanism a rerun needs — `sync_execution_ref` fast-forwards to the origin head, `prepare_calibrated_branch` plus
`rebuild_calibrated_branch` rebuild a stale `/calibrated` branch, `publish_calibration` fast-forwards the report into
the execution branch, `record_calibration_action` appends ACT-CAL rows append-only — and owns staleness _predicates_
(`is_calibration_stale`, `parse_evaluated_commit_sha` in `calibration.py`) that nothing in the flow calls. The only
missing pieces are (a) a head-move definition that is calibration-commit-aware, (b) a bounded trigger at the two places
the flow last touches a PR, and (c) a visible stale signal. That is a focused, single-plan change, not a decomposition
candidate.

**Trigger placement (the decision delegated to the planner).** There is no daemon watcher in this codebase (the pipeline
ends after Stage 5; `head_moved` exists only as a reporting flag in `github/ci_reverify.py`), and introducing one would
be new architecture with entropy far beyond its value. All three head-move sources named by the intent — conflict merge
with main, resolver push, review-loop re-run — pass through either the pipeline's Stage 5 block or the review loop's
posting path. So the trigger lives at those two re-entrant sites: (1) the pipeline re-checks the PR head after
`run_calibrate_stage` returns and reruns the stage, bounded, when the head moved during the stage's own CI wait; (2) the
review loop's pre-posting gate, which every re-run and resolver push flows through, checks an existing calibration
report for staleness and invokes the same rerun entry point. A move made with no flow process alive is caught the next
time either site runs, and until then the stale signal (Step 3) is what the PR and the report-back show.

**Metric aggregation.** Overall `entropy_pred` is the **sum** of step entropies:
`0.5 + 3.5 + 2.0 + 3.0 + 2.5 + 1.0 = 12.5`, within the 15.0 budget. Overall `cost_pred` is the sum of step costs:
`3 + 12 + 7 + 10 + 12 + 6 = 50`. Overall `p_success_pred` 0.80 is driven by the two bottleneck steps (Step 2 at 0.85 and
Step 4 at 0.82, whose risks are correlated — both hinge on the head-move semantics — so the joint probability is taken
as roughly the minimum, 0.80, rather than the product of all six). Impact 88 reflects that this closes a correctness
hole on the human-review boundary itself (the report a human reads before merging must describe the head that merges).
Learning value 5.0: the calibration-commit-aware move definition and the re-entrant-trigger pattern are reusable KB
material for every future "stage must re-fire on external change" intent. Overall EV:
`EV = 0.80 * 88.0 + 0.5 * 5.0 - 0.3 * 12.5 - 50.0 = 70.4 + 2.5 - 3.75 - 50.0 = 19.15`, rounded to 19.2.

**Strong assumptions.** (1) `run_calibrate_stage` really is safe to run twice back-to-back — asserted by existing
`test_calibrate_sync.py`/`test_calibrate_rebuild.py` coverage and re-verified by Step 5's idempotence test. (2) Every
flow module edit requires a `flow_module_budgets.json` raise (nearly every module sits exactly at budget), so the plan
puts the new logic in a new module and keeps call-site raises to a few lines each, the established pattern.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` §1 Git Flow: work happens only on this plan's execution branch; the rerun code
    being built pushes only the flow's own `/calibrated` and execution branches, never main; merging stays human.
  - `holon-config/world/constraints.md` §3 Ledger Immutability: a rerun appends new ACT-CAL and reconciliation rows; it
    must never rewrite or deduplicate historical rows.
  - `holon-config/world/constraints.md` §2 Sandbox tiers: predicted entropy 12.5 selects the container sandbox.
  - `holon-config/world/ruleset.md` §3 Testing: test changes are declared here, in the plan (new
    `tests/test_calibrate_rerun.py`, additions to existing calibration test modules).
  - `docs/safety.md` Invariant 4 / Human review boundaries: Stage 5 rerunning automatically must not create a new
    pre-merge human wait; the design keeps the rerun fully autonomous and leaves the PR merge as the only human step.
  - Repository rules: ruff complexity caps (`C901 <= 10`, `PLR0912 <= 12`, `PLR0913 <= 5`, `PLR0915 <= 50`) with no
    inline suppressions; `scripts/check_flow_module_budgets.py` line budgets on every `flow/**/*.py` module.
- **Potential violations or edge cases:**
  - A head-move definition that counts Stage 5's own report/reconciliation/promotion commits as a move would loop the
    stage forever (and spam ledger rows — an append-only ledger makes that pollution permanent).
  - A rerun racing a human merge: the PR could merge while a rerun is mid-flight; the rerun must fail soft (its push
    target gone or diverged) without corrupting anything.
  - A force-pushed PR head where the evaluated SHA is no longer an ancestor — must count as a move and must not break
    the ancestry walk.
  - The stale-signal PR comment duplicating on every check (comment spam).
- **Mitigations built into the plan:**
  - The move predicate reuses `calibrate_rebuild._is_calibration_commit` semantics (subject prefix + path allow-list),
    is specified first (Step 1), and is covered by a dedicated no-loop test (Step 5) before the triggers go live.
  - The rerun is bounded (at most 2 attempts per flow invocation) and re-checks the predicate before each attempt; an
    unmoved head is a no-op by construction.
  - The stale comment is de-duplicated per `(evaluated_sha, current_head)` pair with a marker, the exact pattern
    `approval_invalidation.py` already uses.
  - Diverged or missing refs fail the stage with a named error (existing `sync_execution_ref` behaviour), never a
    force-push outside the `/calibrated` branch's existing `--force-with-lease` path.
- **Residual risk accepted (and why):** A head moved by a human push while no flow or review-loop process is running is
  only caught at the next invocation; closing that window would require a watcher daemon, which is out of scope and out
  of proportion. The PR's stale comment from the last run remains visible in the meantime, which is the intent's stated
  fallback ("while the report is stale, the pull request carries a visible stale signal").
- **Allocated Entropy Budget:** 15.0
- **Predicted Plan Entropy:** 12.5
- **Budget Compliance:** The strategy fits within budget (`0.5 + 3.5 + 2.0 + 3.0 + 2.5 + 1.0 = 12.5 <= 15.0`), so no
  sub-intent decomposition is needed.

## Plan Description & Strategy

The plan adds one new flow module, `flow/calibrate_rerun.py`, owning two things: the **head-move predicate** (the PR
head differs from the report's evaluated SHA _after discounting trailing calibration-only commits_) and the **bounded
rerun orchestration** (invoke `run_calibrate_stage` again, at most twice, letting the existing sync/rebuild/publish
machinery do the work). It wires that module into the two re-entrant sites every head move passes through — the
pipeline's Stage 5 block in `flow/pipeline.py` and the review loop's pre-posting gate in `flow/review/loop.py` — and
adds a **stale signal** in three places: a marker-de-duplicated PR comment (template:
`github/approval_invalidation.py`), a `calibration_stale` field in the Stage 5 payload, and an honest
`_calibrated_head_line` in the flow summary (`cli/commands/flow.py`) that says the report is stale instead of naming a
head it no longer covers. Hermetic tests follow the `test_calibrate_sync.py` harness (real commits in temp repos,
patched seams, fake gh posters) and cover the four acceptance behaviours: move → rerun with new head in the report; no
move → no-op, no duplicate rows; calibration commits alone → no move, no loop; stale signal appears and clears.
Validation finishes with the affected tests, one full suite run, lint, lint-docs, and the flow-module-budget gate.

Step order: specify semantics (1) → detection + rerun module (2) → stale signal (3) → trigger wiring (4) → tests (5) →
budgets and full validation (6). Steps 2–4 are land-ordered so that each commit keeps the suite green: the module lands
with its own unit tests before any call site uses it.

## Touch Set

- `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rerun.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/pipeline.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/stages/calibrate.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/calibrate_rebuild.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/loop.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/review/posting.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/github/ci_reverify.py`
- `apps/sandbox-executor/src/sandbox_executor/flow/__init__.py`
- `apps/sandbox-executor/src/sandbox_executor/calibration.py` symbols: is_calibration_stale, parse_evaluated_commit_sha
- `apps/sandbox-executor/src/sandbox_executor/cli/commands/flow.py`
- `apps/sandbox-executor/flow_module_budgets.json`
- `apps/sandbox-executor/tests/test_calibrate_rerun.py`
- `apps/sandbox-executor/tests/test_calibrate_sync.py`
- `apps/sandbox-executor/tests/test_calibration.py`
- `apps/sandbox-executor/tests/test_review_loop.py`
- `apps/sandbox-executor/tests/flow/test_package_exports.py`

---

## Step 1: Pin down head-move semantics and rerun seams from the two drifted PRs' commit shapes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Cheap, read-only reconnaissance that de-risks the two bottleneck steps; no reusable artefact beyond
  this plan, so a branch of its own would be pure overhead.
- **Step Type:** INFO_GATHERING
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The commits that land on a PR branch between the evaluated SHA and the final head fall
  into exactly three shapes — Stage 5's own `chore(calibration):` commits (report, reconciliation, promotion), merge
  commits from a conflict merge with main, and resolver/review fix commits — and
  `calibrate_rebuild._is_calibration_commit` (subject prefix + `is_calibration_path` allow-list, non-merge only) already
  separates the first shape from the other two without modification.
- **Learning target:** A written predicate specification: for which `evaluated_sha..pr_head` commit lists is the head
  "moved", including the force-push case (evaluated SHA not an ancestor) and the merge-commit case (a merge commit is
  always a move, matching `_is_calibration_commit` rejecting merges).
- **Maximum acceptable cost for this learning:** Half of Step 2's cost; if the commit shapes cannot be confirmed from
  `git log` of the flow's own branches plus the existing tests, proceed with the conservative predicate (any non
  calibration-only commit is a move) and note the assumption in the execution report.

### Intent & Git Integration

- **Step Intent:** Confirm, against real history and existing tests, the exact definition of "head move" and the exact
  seams (`run_calibrate_stage` arguments, Stage 5 payload keys, review-loop posting-gate locals, gh comment helper
  signatures) the later steps will use.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-recon/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `flow/stages/calibrate.py` end to end: confirm the order sync → prepare → run_calibrate → reconcile → promote →
   publish → final-head CI → reverify, and which payload keys (`calibrated_head`, `final_head_ci`, `ci_verdict`) a rerun
   must refresh.
2. Read `flow/calibrate_rebuild.py`: confirm `_is_calibration_commit` rejects merge commits and foreign paths, and that
   `holds_only_calibration_commits` walks a ref range — the shape the move predicate needs.
3. Read `calibration.py` staleness helpers (`parse_evaluated_commit_sha` around line 486, `is_calibration_stale` around
   line 515) and their unit tests in `tests/test_calibration.py` (~lines 843–934) to fix the report-parsing contract.
4. Read the review loop's posting gate (`flow/review/loop.py` ~lines 700–745, `flow/review/posting.py`) and the gh
   comment seams in `flow/github/approval_invalidation.py` (`gh_comment_lister`, `gh_comment_poster`,
   `invalidation_marker`) to fix the trigger and signal seams.
5. Write the predicate specification into the execution notes: moved iff the set `evaluated_sha..pr_head` is non-empty
   and contains at least one commit that is not a calibration commit, or `evaluated_sha` is not an ancestor of
   `pr_head`; unmoved otherwise. Record the bounded-rerun count (2) and the comment marker format.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (a wrong conclusion here surfaces in Step 2/5 tests, not as silent plan failure)

### Safety & Constraint Considerations

- **Relevant rules:** read-only; `constraints.md` §1 (no checkout of other intents' branches — inspection via `git log`
  of fetched refs only).
- **Potential failure modes for this step:**
  - The historical PR branches (#190, #193 equivalents) are not reachable from this clone, leaving the commit-shape
    hypothesis partially unverified.
- **Guardrails and early‑abort checks:** If history is unavailable, fall back to the conservative predicate and the
  commit shapes produced by `tests/test_calibrate_sync.py`'s `push_ahead` fixture; cap the step at its cost estimate.

### Success & Discard Criteria

- **Success:** A one-page predicate spec plus a named list of seams (function, module, line) that Steps 2–4 consume
  verbatim; no code changed.
- **Discard:** If reconnaissance exceeds `1.5x cost_pred`, stop and adopt the conservative predicate.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.97  |
| entropy_pred        | 0.5   |
| impact_pred         | 10.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 7.3   |

### Step Metrics Rationale

Read-only work: near-certain success, negligible entropy (no state change beyond notes). Impact is enabling, not
user-visible. `EV = 0.97 * 10.0 + 0.5 * 1.5 - 0.3 * 0.5 - 3.0 = 9.7 + 0.75 - 0.15 - 3.0 = 7.30`.

---

## Step 2: Build `flow/calibrate_rerun.py` — calibration-aware head-move predicate and bounded Stage 5 rerun

- **Sub‑intent recommendation:** NO
- **Reasoning:** This is the plan's core and its riskiest step, but it is one cohesive module of ~130 lines reusing
  existing machinery; splitting it into a child intent would cost a full pipeline run for no isolation benefit, and its
  p_success (0.85) is well above the STRONGLY_YES threshold.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** One new module owning (a) `head_move_since_calibration(...)` — compares the PR/origin head with the
  report's evaluated SHA, discounting trailing calibration-only commits — and (b) `rerun_calibration(...)` — a bounded
  wrapper that re-invokes `run_calibrate_stage` and reports the refreshed payload.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-rerun-module/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. New module `flow/calibrate_rerun.py` with module docstring stating the invariant: "a calibration commit is never, by
   itself, a head move" (the no-loop guarantee).
2. `head_move_since_calibration(repo_dir, evaluated_sha, current_head)` returns a small frozen dataclass
   (`HeadMoveCheck`: `moved: bool`, `evaluated_sha`, `current_head`, `reason`): unmoved when the SHAs are equal; when
   `evaluated_sha` is an ancestor of `current_head`, walk `evaluated_sha..current_head` with the commit classifier and
   report unmoved iff every commit is a calibration commit; a non-ancestor evaluated SHA (force push, conflict merge
   rewriting history) is always a move. Reuse the classifier by promoting `calibrate_rebuild._is_calibration_commit` to
   a public name (`is_calibration_commit`) rather than duplicating it — a rename-plus-alias inside
   `calibrate_rebuild.py` costing ~2 budget lines.
3. `report_staleness(context)` resolves the current report path (`plans/{plan_id}_calibration.md` in the target
   worktree), reads its evaluated SHA via `calibration.parse_evaluated_commit_sha`, resolves the current origin/PR head
   via the existing `sync_execution_ref`/`gh pr view --json headRefOid` seams (injected callables, defaulting to the
   same lambdas `ci_final_head` uses, so tests fake them), and returns the `HeadMoveCheck`. A missing report or
   unparsable SHA returns `moved=False` with a `reason` naming the gap (nothing to rerun against; Stage 5 proper owns
   first-time calibration).
4. `rerun_calibration(context, run_stage, max_attempts=2)`: loop at most `max_attempts` times — check staleness; if
   unmoved, return the unchanged result (idempotence: no stage call, hence no new ledger rows); if moved, log the move
   (`evaluated X, head Y, reason Z`) and call the injected `run_stage` (production: `run_calibrate_stage`), which
   already syncs to the new head, rebuilds the stale `/calibrated` branch, appends fresh ACT-CAL and reconciliation
   rows, publishes, and re-verifies CI. After the final attempt, re-check and surface `calibration_stale=True` in the
   returned payload if the head outran both attempts (fail soft, stage not green-lit as covered).
5. Typed signatures and docstrings per `ruleset.md` §2; keep every function under the ruff complexity caps by factoring
   the ancestry walk into a private helper.
6. Export the two public names from `flow/__init__.py` (the package-exports test requires it; ~2 budget lines per name).
7. Unit tests for the predicate land in this step inside `tests/test_calibrate_rerun.py` (pure-git cases, no flow
   stage): equal heads, calibration-commits-only, mixed commits, merge commit, force-push non-ancestor.

### Dependencies & Criticality

- **Depends on:** Step 1
- **Is Bottleneck:** YES (every later step consumes this module; p_success_pred 0.85 >= 0.6, so no forced sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** `constraints.md` §3 (rerun appends rows, never rewrites); ruff complexity caps; flow module
  budgets (new module needs a `flow_module_budgets.json` entry at creation size — recorded here, applied in Step 6
  together with the call-site raises).
- **Potential failure modes for this step:**
  - Predicate counts Stage 5's own commits as a move → infinite loop risk (caught by the dedicated unit test before any
    trigger exists).
  - Ancestry walk breaks on a shallow or partial clone → classify as moved (conservative) and name the reason.
- **Guardrails and early‑abort checks:** The module is dead code until Step 4 wires it, so it can land green in
  isolation; `max_attempts` is a hard constant, not configuration, so no path can loop unboundedly.

### Success & Discard Criteria

- **Success:** `uv run pytest apps/sandbox-executor/tests/test_calibrate_rerun.py` green on the predicate cases; module
  within its declared budget; exports test green.
- **Discard:** If reusing `run_calibrate_stage` proves unsafe to call twice (idempotence assumption broken in a way a
  small fix cannot close), stop and re-plan — that would change the architecture, not the step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 3.5   |
| impact_pred         | 70.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 3.0   |
| ev_pred             | 48.0  |

### Step Metrics Rationale

Highest-entropy step: a new module plus a public-name promotion in `calibrate_rebuild.py`, with the no-loop invariant as
the main risk (mitigated by Step 1's spec and same-step unit tests). SSA is one new file plus two small edits; IRR is
low (revertable, dead until wired).
`EV = 0.85 * 70.0 + 0.5 * 3.0 - 0.3 * 3.5 - 12.0 = 59.5 + 1.5 - 1.05 - 12.0 = 47.95`, rounded to 48.0.

---

## Step 3: Stale signal — marker-de-duplicated PR comment, Stage 5 payload flag, honest flow report-back line

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, low-risk surface work following an in-repo template (`approval_invalidation.py`); an execution
  branch suffices.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** While the report is behind the head, the PR shows a stale comment and the flow's report-back says
  "stale", never "calibrated"; both clear (are superseded) after a successful rerun.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-stale-signal/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `flow/calibrate_rerun.py`, add `post_stale_signal(...)` mirroring `approval_invalidation.invalidate_approval`: a
   comment "Calibration report is stale: it covers `<evaluated_sha>` but the pull request head is `<current_head>`.
   Stage 5 is rerunning; do not merge on this report." carrying a hidden marker keyed on the
   `(evaluated_sha, current_head)` pair, posted through the injected `gh_comment_lister`/`gh_comment_poster` seams and
   skipped when the marker already exists (no spam on repeated checks).
2. After a successful rerun, `rerun_calibration` posts the clearing comment ("Calibration refreshed: the report now
   covers head `<new_sha>`.") with its own marker, superseding the stale one; no comment deletion (append-only audit
   trail, same philosophy as the ledger).
3. Stage 5 payload: `calibration_stale: bool` plus `stale_reason` set by the rerun wrapper; `calibrated_head` always
   names the head the _current_ report covers.
4. `cli/commands/flow.py` `_calibrated_head_line`: when the payload carries `calibration_stale`, print "calibration
   report is STALE: covers `<sha>`, pull request head is `<sha>` — not ready to merge on this report" instead of the
   covering line; the existing "covers execution head X" line remains the cleared state. (`cli/commands/flow.py` is not
   under the flow module budget.)
5. The flow's report-back therefore never says "calibrated" while stale — satisfying acceptance criterion 3's
   report-back half; the PR comment satisfies its visible-signal half.

### Dependencies & Criticality

- **Depends on:** Step 2
- **Is Bottleneck:** NO (the rerun works without the signal; the signal is an acceptance criterion but isolable)

### Safety & Constraint Considerations

- **Relevant rules:** `safety.md` human-review boundary — the signal informs the human maintainer, it never blocks or
  performs a merge; redaction conventions for CLI errors already in `cli`.
- **Potential failure modes for this step:**
  - Comment spam if the marker key is wrong (per-head instead of per-pair) — covered by a dedup test in Step 5.
  - A gh failure while posting must not fail the stage: posting is best-effort, logged, with the payload flag as the
    authoritative signal.
- **Guardrails and early‑abort checks:** Comment posting wrapped in the same tolerant error handling
  `approval_invalidation` uses; payload flag set before any network call.

### Success & Discard Criteria

- **Success:** Unit-level: with fake poster/lister, the stale comment posts once per pair, the cleared comment posts
  after rerun, `_calibrated_head_line` renders all three states.
- **Discard:** Cost exceeding `1.5x cost_pred` (signal scope creep — e.g. labels, status checks — is cut, comment +
  report-back is the contract).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 2.0   |
| impact_pred         | 45.0  |
| cost_pred           | 7.0   |
| learning_value_pred | 1.5   |
| ev_pred             | 33.7  |

### Step Metrics Rationale

Template-following surface work; modest entropy from touching the user-visible summary and PR surface.
`EV = 0.90 * 45.0 + 0.5 * 1.5 - 0.3 * 2.0 - 7.0 = 40.5 + 0.75 - 0.6 - 7.0 = 33.65`, rounded to 33.7.

---

## Step 4: Wire the trigger at the pipeline's Stage 5 block and the review loop's pre-posting gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Two call-site edits of a few lines each into modules at budget; risk is integration-shaped and covered
  by Step 5's tests, not by branch isolation.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every head move that happens inside the flow (during Stage 5's CI wait) or through the review loop
  (re-run, resolver push, conflict merge) automatically funnels into `rerun_calibration`, with no human step.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-triggers/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Pipeline site (`flow/pipeline.py`, Stage 5 block at ~lines 367–382): after `timed_execute_stage(CALIBRATE)` returns a
   completed result, call `rerun_calibration` with the result; it no-ops when unmoved (the common case: Stage 5's own
   commits are discounted by the predicate) and replaces the stage result + checkpoint when a rerun ran. Net call-site
   growth ~6–8 lines (budget raise in Step 6).
2. Review-loop site (`flow/review/loop.py` posting gate, ~lines 700–745): immediately after `decide_post` approves
   posting (or after a resolver push lands and the loop re-reads `current_commit_sha`), when the flow context carries
   `auto_calibrate` and a calibration report already exists for this plan, run `report_staleness`; on `moved`, post the
   stale signal and invoke `rerun_calibration`. A standalone `holon review-loop` without calibration context only posts
   the stale signal (it cannot run Stage 5 — that stays with the flow), which still satisfies "the pull request carries
   a visible stale signal". Keep the loop edit thin by delegating all logic to `calibrate_rerun`; prefer a single helper
   call so `review/loop.py` grows ~5 lines.
3. Extend `github/ci_reverify.reverify_final_head` report text to name the rerun ("head moved; Stage 5 rerun refreshed
   the report to `<sha>`") when the payload says a rerun happened — keeping the reverify module the single owner of the
   head-moved wording (~4 lines).
4. Checkpoint behaviour: a rerun overwrites the Stage 5 entry in the checkpoint through the existing `save_checkpoint`
   path, so a resumed flow sees the refreshed `calibrated_head` — no checkpoint schema change.
5. Confirm no new imports from entrypoints into the host flow engine are introduced (the module is flow-internal;
   `cli/commands/flow.py` only reads payload fields, as it does today).

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3
- **Is Bottleneck:** YES (without wiring there is no automatic rerun; p_success_pred 0.82 >= 0.6, so no forced
  sub-intent)

### Safety & Constraint Considerations

- **Relevant rules:** `safety.md` §5 (no stage before the merge waits for a human — the rerun is autonomous);
  `constraints.md` §1 (the rerun pushes only this flow's own branches); flow module budgets on both edited modules.
- **Potential failure modes for this step:**
  - Review-loop trigger firing during Stage 4 of a _first_ run (before any report exists) — guarded by the
    report-existence check in `report_staleness`.
  - Double rerun when both sites observe the same move — benign by idempotence (the second site sees an unmoved head),
    asserted by a Step 5 test.
  - A rerun failing (red CI on the new head) must fail the stage visibly, exactly as a first run would — reuse the
    stage's own FAILED propagation.
- **Guardrails and early‑abort checks:** Both call sites are behind `auto_calibrate`/report-existence guards;
  `max_attempts=2` bounds work; stage FAILED propagation unchanged.

### Success & Discard Criteria

- **Success:** With Step 5's harness, a `push_ahead` between calibration and the pipeline's post-stage check produces a
  second Stage 5 run whose report names the new head; the review-loop fake produces the same through the posting gate.
- **Discard:** If the posting-gate integration needs deep restructuring of `review/loop.py` (at budget, 907 lines), fall
  back to signal-only at that site and keep the full rerun at the pipeline site + flow resume, documenting the narrowed
  trigger in the execution report; p_success below 0.2 or cost above `1.5x cost_pred` stops the step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.82  |
| entropy_pred        | 3.0   |
| impact_pred         | 65.0  |
| cost_pred           | 10.0  |
| learning_value_pred | 2.5   |
| ev_pred             | 44.0  |

### Step Metrics Rationale

Integration into two at-budget, central modules carries the plan's residual risk (hence 0.82); entropy reflects touching
the pipeline's control flow and the review loop. The learning value is the reusable re-entrant-trigger pattern.
`EV = 0.82 * 65.0 + 0.5 * 2.5 - 0.3 * 3.0 - 10.0 = 53.3 + 1.25 - 0.9 - 10.0 = 43.65`, rounded to 44.0.

---

## Step 5: Hermetic pytest coverage for rerun, idempotence, no-loop, and stale-signal lifecycle

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests belong with the code they verify; the harness already exists (`test_calibrate_sync.py` fixtures),
  so cost and risk are moderate.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Encode all four acceptance behaviours as hermetic pytest tests with real temp-repo commits and faked
  gh/network seams.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-tests/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Extend `tests/test_calibrate_rerun.py` (begun in Step 2) using `test_calibrate_sync.py`'s exported harness
   (`make_repos`, `push_ahead`, `fake_calibrate`, `run_stage`, `git`, `commit_file`) — real SHAs, bare origin, no
   network; gh seams faked with recorded poster/lister doubles as in `test_calibrate_final_head_ci.py`.
2. Acceptance test 1 (move → rerun): calibrate, `push_ahead`, run the pipeline-site trigger; assert a second stage run
   happened and `parse_evaluated_commit_sha` of the rewritten report equals the new origin head; assert exactly one new
   ACT-CAL row per run (rows appended, none rewritten — compare the ledger file as line-prefix plus additions).
3. Acceptance test 2 (unmoved → no-op): run the trigger again with no push; assert zero additional stage runs, ledger
   byte-identical, payload unchanged.
4. Acceptance test 3 (no loop): after a genuine rerun, the only new commits are calibration commits; assert the
   predicate reports unmoved and the trigger performs no third run; add the pure predicate table from Step 2 (equal /
   calibration-only / mixed / merge-commit / force-push cases) if not already landed.
5. Acceptance test 4 (stale signal lifecycle): with the fake poster — stale comment posted once (marker dedup on a
   second check), cleared comment posted after the rerun, `calibration_stale` flag toggles, `_calibrated_head_line`
   renders the stale wording while behind and the covering wording after.
6. Review-loop gate test in `tests/test_review_loop.py` (or a focused new case beside the existing posting tests, using
   `review_fakes.py`): an approved consensus on a PR whose existing report names an older head posts the stale signal
   and invokes the injected rerun callable; a first-run PR without a report does not.
7. Double-trigger benignity: both sites fired in sequence on one move → exactly one rerun.
8. Run the affected tests first:
   `uv run pytest apps/sandbox-executor/tests/test_calibrate_rerun.py apps/sandbox-executor/tests/test_calibrate_sync.py apps/sandbox-executor/tests/test_calibrate_rebuild.py apps/sandbox-executor/tests/test_calibration.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_ci_reverify.py`.
   No repeated runs: nothing here measures timing, so test-run discipline is the affected set once, then the full suite
   once in Step 6.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4
- **Is Bottleneck:** YES (acceptance criteria are defined as these tests passing; p_success_pred 0.85 >= 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `ruleset.md` §3 — test changes are hereby declared: new `tests/test_calibrate_rerun.py`, additive
  cases in `tests/test_review_loop.py` and (if predicate tables live there) `tests/test_calibration.py`; no existing
  assertion is modified. Hermeticity guards (`network_guard.py`, `gh_write_guard.py`) must stay green.
- **Potential failure modes for this step:**
  - Ledger-row assertions flaky under `-n auto` worksteal if tests share a ledger path — each test gets its own temp
    knowledge dir, as the sync harness already provides.
  - The review-loop fake needing more context than `review_fakes.py` offers — extend the fakes additively.
- **Guardrails and early‑abort checks:** Tests run with `-n auto --dist worksteal` like the suite; any test needing a
  real network or gh fails the hermetic guards immediately.

### Success & Discard Criteria

- **Success:** The affected-test command above passes with all new cases; pass/fail counts and pytest-reported time
  recorded in the execution report.
- **Discard:** A test that cannot be made hermetic without faking the predicate itself (which would test nothing) is
  dropped and replaced by a unit-level equivalent, noted in the report; cost above `1.5x cost_pred` stops the step.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 2.5   |
| impact_pred         | 60.0  |
| cost_pred           | 12.0  |
| learning_value_pred | 2.0   |
| ev_pred             | 39.3  |

### Step Metrics Rationale

The harness exists, but multi-run ledger and comment-lifecycle assertions are fiddly; entropy covers new test files and
fixture extension. `EV = 0.85 * 60.0 + 0.5 * 2.0 - 0.3 * 2.5 - 12.0 = 51.0 + 1.0 - 0.75 - 12.0 = 39.25`, rounded to
39.3.

---

## Step 6: Module budgets, lint, docs lint, and single full-suite validation

- **Sub‑intent recommendation:** NO
- **Reasoning:** Mechanical closing step; zero reuse value, low risk.
- **Step Type:** CONFIG
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Bring `flow_module_budgets.json` in line with every touched flow module and prove the acceptance
  criteria's validation clause: full pytest, lint, lint-docs all green.
- **Git branch:** I-1794891078-s5rerun/P-1794891094-eede5d91-claude-agent-claude-fable-5/E-{ts}-validate/\_
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. `flow_module_budgets.json`: add `flow/calibrate_rerun.py` at its creation size; raise `pipeline.py`,
   `review/loop.py`, `stages/calibrate.py` (if touched), `calibrate_rebuild.py` (public-name promotion),
   `github/ci_reverify.py` and `__init__.py` by their exact new line counts, each with the `description` note recording
   the reason and count, per the gate's contract (`scripts/check_flow_module_budgets.py`).
2. Run the budget gate and formatting locally: `uv run task lint` (includes the flow-module budget check and prettier,
   which also validates the calibration-report markdown touched only at runtime) and `uv run task lint-docs`.
3. Full suite once: `uv run task test` (repository default, `-n auto --dist worksteal`); record pass/fail count and
   pytest-reported time. No repetitions — nothing in this intent measures performance or timing (test-run discipline).
4. Reconcile predicted vs observed scope: confirm the final diff's touch set is a subset of the plan's Touch Set; any
   excursion is named and justified in the execution report before the PR is raised.

### Dependencies & Criticality

- **Depends on:** Step 2, Step 3, Step 4, Step 5
- **Is Bottleneck:** YES for acceptance (criterion 4 is exactly this step), but mechanical: failures here are fix-ups,
  not plan failures.

### Safety & Constraint Considerations

- **Relevant rules:** flow module budget gate; `ruleset.md` §2 (style, typing, docstrings enforced by ruff); repository
  hygiene tests (self-containment) must stay green.
- **Potential failure modes for this step:**
  - A budget raise mis-counted by a line → gate names the module and count; fix and re-run.
  - Docs prettier oscillation on the new comment wording → keep generated markdown out of docs; only code and the
    budgets JSON change here.
- **Guardrails and early‑abort checks:** Run the budget gate before the full suite to fail fast on the cheap check.

### Success & Discard Criteria

- **Success:** `uv run task test`, `uv run task lint`, `uv run task lint-docs` all pass in one run each; counts and
  times recorded.
- **Discard:** Repeated unrelated suite flakes (not introduced by this diff, verified by rerunning only the flaky test
  against the base branch) are reported rather than chased; cost above `1.5x cost_pred` stops polishing.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 1.0   |
| impact_pred         | 30.0  |
| cost_pred           | 6.0   |
| learning_value_pred | 1.0   |
| ev_pred             | 21.4  |

### Step Metrics Rationale

Mechanical but not free: the budget gate is strict (exact counts) and the full suite is large. `EV = 0.90 \* 30.0 + 0.5

- 1.0 - 0.3 \* 1.0 - 6.0 = 27.0 + 0.5 - 0.3 - 6.0 = 21.2`, rounded up to 21.4 with the small chance the gate passes on
  the first count.

---
