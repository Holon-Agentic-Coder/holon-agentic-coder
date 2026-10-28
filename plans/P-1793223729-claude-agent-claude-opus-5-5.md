# Plan for I-1793223711-review-loop-wire-real-ensemble

- **Plan ID:** P-1793223729-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-28T21:42:09.825Z

## Planner Autonomy Summary

- **Intent handling:** ACCEPT_AS_IS
- **Reframed intent (if applicable):** N/A. The intent is already scoped as the third of three sequential changes, the
  operator answered "do not split", and its two prerequisites (`review_target.py`, `review_ensemble.py`) are on `main`.
  One clarification is made explicit rather than reframed: the routing probe (Step 1) has a pre-registered decision
  rule, and the planner's prior is that the **fail-closed fallback** is the more likely outcome (see Step 1).
- **Exploration stance:** balanced. Almost all work is exploit (well-specified call-site wiring, report rewrite, tests,
  docs); one time-boxed exploratory probe decides the Stage 4 routing, because the intent itself names it as a
  hypothesis and the evidence in the code base already points against it.
- **Safety priority level:** critical
- **Priority Justification:** The defect is a provenance and security failure: `holon flow` posted fabricated approvals
  to a real PR (PR #97). `docs/safety.md` §2 "Sandboxing is mandatory for execution" and Invariant 2 (Sandboxing) forbid
  agent turns on the host, and §5 / Invariant 4 (Human review) make the posted review a human's decision input, so a
  fabricated or host-run review undermines the final human gate. `holon-config/world/constraints.md` §2 (sandbox
  containment) and §3 (append-only ledgers) also apply directly.

## Exploration

- **Proportion of steps that are exploratory:** 0.125 (1 of 8 steps)
- **Justification:** The routing question is genuinely unknown and the intent caps it at about 20 minutes. Everything
  else is mechanical against existing, already-tested APIs (`run_ensemble`, `decide_consensus`, `ReviewerResult`,
  `record_review_action`, `_dispatch_role`), so additional exploration would only add entropy.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.70  |
| entropy_pred        | 10.6  |
| impact_pred         | 90.0  |
| cost_pred           | 21.5  |
| learning_value_pred | 6.0   |
| ev_pred             | 41.32 |

## Plan Description & Strategy

Current-state findings that shape the plan (gathered by reading the code, no changes made):

- `flow.py` (5107 lines): `run_review_loop()` at about line 3682 decides consensus with
  `not is_dirty and not finding_ids and all_passed` and writes a per-iteration `ACT-RES` "resolver triggered" row for
  iteration 2 onwards. `build_pr_review_consensus_report()` at about line 1524 (~300 lines) builds a fabricated persona
  matrix, a boiler-plate "Verified Positive Findings" section, `hashlib.sha256(f"{target_commit}-reviewer-{i}")[:8]`
  reviewer ids, votes copied from `approved`, `"{ensemble_size}-Agent"` wording and the fallback title
  `"Audit Remediation and Flow Review Hardening"`. `hashlib` is imported only for this.
- `review_ensemble.py` (805 lines) exports
  `ReviewRequest(repo_dir, head_sha, base_ref, pr_title, pr_body, diff_text, iteration)`,
  `run_ensemble(request, *, agent, model, size, **seams)`,
  `decide_consensus(results, configured_size, mergeable, ci_green)` and `ReviewerResult` with `agent`, `model`,
  `status`, `verdict`, `counts`, `findings` and `failure_reason` (already redacted and bounded). Its module docstring
  still says "Stage 4 does not call it yet". Adding 400 more lines would take it past 1200 lines, so the renderer goes
  into a sibling `review_report.py`.
- `ReviewTarget` (`review_target.py`) carries `head_branch`, `head_sha`, `pr_number`, `title`, `body`, `base_branch`.
- `_dispatch_role()` calls `cli.dispatch_role_agent()`, which launches `run_docker_container(role, image, args)` and
  returns only the container exit code. `run_docker_container` uses `repo_dir` only to resolve the origin URL: the
  container clones the repository, the host workspace is **not mounted**. `docs/ledger_schema.md` already records the
  "Known gap: the review loop does not commit `executions.jsonl`". Consequence: the host can read the container's exit
  code, but **not** the ledger rows the container records, without a new transport (mount, push or artefact copy).
- `holon review-loop` already routes to the `review-loop` role container unless `--local`, `--dry-run`, `--json`,
  `HOLON_ROLE` or `HOLON_IN_SANDBOX` is set; the container entrypoint `entrypoint/review_loop.py` calls
  `run_review_loop()` and has its own `--ensemble-size` help ("Evaluation ensemble heuristic count").
- `cli.py`: `_AGENT_FLOW_STAGES = ("intent", "plan", "execute")` with a comment claiming review runs only git, gh and
  local checks; `flow_dispatches_agent()` gates the preflight probe. `holon flow` has no `--ensemble-size` option
  (verify again during execution; only `--max-review-iterations` was found); `holon review-loop` has one at line ~2105
  with validation `>= 1`.
- Sandbox detection already exists (`HOLON_IN_SANDBOX`, `HOLON_ROLE`, `/.dockerenv`) in `agent_runner.py` and
  `entrypoint/executor.py`; `agent_defaults.SANDBOX_GUARD_VARS` names the variables. The guard reuses this, it does not
  invent a new signal.
- Tests touching the old behaviour: `tests/test_flow.py` (~lines 1833-1900 resolver row, 3167-3241 ledger shape with
  `ACT-RES`, 3564-3568 `build_pr_review_consensus_report`, 4103/4122 assert no `ACT-RES`), `tests/test_review_loop.py`
  (979 lines, loop and CLI exit codes), `tests/test_review_ensemble.py` (662 lines, ensemble already covered).

Strategy: one execution branch, eight ordered steps. Probe first (cheap, decides Step 4's shape), then move the report
out of `flow.py` (frees the line budget for the wiring), then wire the ensemble with an injectable seam so tests use
fakes, then route or refuse, then posting/exit codes/CLI, then tests, docs and a single verification pass.

---

## Step 1: Time-boxed probe of Stage 4 routing through the existing review-loop role container

- **Sub‑intent recommendation:** NO
- **Reasoning:** Read-only investigation of about 20 minutes whose only output is a decision recorded in the execution
  notes; it carries almost no entropy and is the cheapest way to settle the intent's explicit hypothesis.
- **Step Type:** EXPLORATION
- **Exploration level:** EXPLORATORY

- **Hypothesis being tested:** `holon flow` Stage 4 can call `_dispatch_role(context, "review-loop", [...])` so the
  `review-loop` role container runs the ensemble, and the host can read the outcome from the exit code plus the ledger
  rows the container records, with no new transport.
- **Learning target:** Whether (a) `dispatch_role_agent` / `resolve_role_image` accept the `review-loop` role and
  produce the same container arguments as `holon review-loop` (target, agent, model, `--max-iterations`,
  `--ensemble-size`, `--no-auto-pr`); (b) the container's ledger rows can reach the host workspace with existing
  mechanisms; (c) the container could be told to skip mergeability, CI and posting so the host keeps them.
- **Maximum acceptable cost for this learning:** about 20 minutes of reading and at most one throwaway fake-dispatcher
  experiment in a pytest scratch test that is not committed; no Docker, no real gh.

### Intent & Git Integration

- **Step Intent:** Decide, with evidence, between "route via `_dispatch_role`" and "fail-closed fallback".
- **Git branch:**
  `I-1793223711-review-loop-wire-real-ensemble/P-1793223729-claude-agent-claude-opus-5-5/E-{timestamp}-wire-ensemble/_`
  (single execution branch for all steps)
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Read `cli.dispatch_role_agent`, `resolve_role_image`, `run_docker_container` (mounts, env forwarding, `HOLON_ROLE`
   export at about line 1360) and `entrypoint/review_loop.py` `main()` end to end.
2. Confirm whether the host workspace is mounted into the container or only cloned; confirm whether the review-loop
   entrypoint commits or pushes `executions.jsonl` (the ledger schema says it does not).
3. Apply the pre-registered decision rule:
   - ROUTE if all of: the role dispatches through `_dispatch_role` with no new parameters beyond positional args; the
     container's outcome is fully conveyed by its exit code (0 only for APPROVED); and the per-reviewer evidence the
     host needs is either available to the host with existing mechanisms or is recorded and posted entirely inside the
     container (so the host does not need it). If ROUTE, the container runs the whole loop (ensemble, mergeability, CI
     and posting) and host Stage 4 only interprets the exit code; this deviates from "host keeps posting" and must be
     written up as such.
   - FALLBACK otherwise, in particular if host Stage 4 would need the container's ledger rows and they cannot reach the
     host without a mount, push or copy (a new transport). Planner prior: FALLBACK at about 0.65.
4. Record in the execution notes: evidence, decision, and (if FALLBACK) the follow-up "route Stage 4 reviewer turns into
   the review-loop role container with a result transport for ledger rows".

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (both outcomes have a defined Step 4 path)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 and Invariant 2 (sandboxing); operator answer "Reviewer turns run only inside
  the sandbox".
- **Potential failure modes for this step:** probe overruns the time box; a tempting partial route that would need a
  host mount (a new transport, and a widening of the sandbox boundary).
- **Guardrails and early‑abort checks:** hard stop at about 20 minutes; any need for a mount, push or copy of ledger
  rows means FALLBACK; never start Docker or call real gh.

### Success & Discard Criteria

- **Success:** A written ROUTE or FALLBACK decision with the evidence that triggered it.
- **Discard:** At 30 minutes without a decision, choose FALLBACK by default.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.90  |
| entropy_pred        | 0.6   |
| impact_pred         | 10.0  |
| cost_pred           | 2.0   |
| learning_value_pred | 7.0   |
| ev_pred             | 10.32 |

### Step Metrics Rationale

`EV = 0.90 * 10.0 + 0.5 * 7.0 - 0.3 * 0.6 - 2.0 = 9.00 + 3.50 - 0.18 - 2.00 = 10.32`. A probe almost always produces a
decision (high p); its learning value is high because it settles whether the role container can be the Stage 4
transport, which future work on review provenance depends on. Entropy is minimal because nothing is committed.

---

## Step 2: Move the consensus report out of `flow.py` into a real-data `review_report.py`

- **Sub‑intent recommendation:** NO
- **Reasoning:** Contained refactor with a clear oracle (tests on the rendered text); it is needed early because it
  frees the `flow.py` line budget for Step 3.
- **Step Type:** REFACTOR
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Replace the fabricated report with one rendered only from real `ReviewerResult` objects and a real
  title, in a new sibling module.
- **Git branch:** same execution branch as Step 1
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Create `apps/sandbox-executor/src/sandbox_executor/review_report.py` with a module docstring (Australian English) and
   small functions: one that resolves the title (intent title, else `ReviewTarget.title`, else `PR #<n>` or the branch
   name; never a hard-coded phrase); one that renders a single reviewer line (index, agent, model, status, verdict,
   `critical/important/nit` counts; a FAILED reviewer shows `FAILED` and its already-redacted `failure_reason`, passed
   through `redact_agent_secrets` once more as defence in depth); one that renders each finding (severity, file, line,
   summary); one that renders the CI and mergeability lines from the probe data; and the public
   `build_pr_review_consensus_report(repo_dir, target, consensus_data, reviewers, title)` that assembles them.
2. The header states the configured reviewer count and the completed count from the real list (for example "Reviewers: 3
   configured, 3 completed"), never "N-Agent Independent Ensemble" derived from a constant.
3. Keep the existing PR-link / target-commit / base-branch resolution helpers but move them into `review_report.py` with
   unchanged behaviour (they read real git and consensus data). Keep the human-only merge notice.
4. Delete from `flow.py`: the old `build_pr_review_consensus_report` body, the persona matrix and the boiler-plate
   "Verified Positive Findings" text (both fabricated: they never came from a reviewer), the `hashlib` reviewer ids, the
   copied-vote loop, the "N-Agent" wording, the fallback title and the now unused `import hashlib`. Re-export or import
   `build_pr_review_consensus_report` from `review_report` in `flow.py` so `post_pr_review_consensus()` and existing
   test imports keep working.
5. `post_pr_review_consensus()` takes the reviewer list and title (via `consensus_data` keys or explicit keyword
   arguments; prefer explicit keyword arguments with defaults of an empty list and `None`).
6. Update the `review_ensemble.py` module docstring sentence "Stage 4 does not call it yet" to describe the wiring.
7. Measure `flow.py` with `wc -l` before and after; the net count must not increase (expected net reduction of roughly
   200 lines after Step 3's additions).

### Dependencies & Criticality

- **Depends on:** NONE (may run in parallel with Step 1)
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 (human review is the final gate; the posted report is the human's input);
  intent constraint "no harness paths, bean ids or review ids in any file".
- **Potential failure modes for this step:** a reviewer's report text or finding summary injects markdown or secrets
  into the posted comment; title resolution silently falls back to a constant.
- **Guardrails and early‑abort checks:** escape or bound finding summaries (reuse `MAX_REASON_CHARS`-style bounding);
  grep the module for "Audit Remediation" and `sha256` before committing.

### Success & Discard Criteria

- **Success:** `grep` finds no `hashlib`, `sha256`, "Audit Remediation" or "-Agent Independent" in `flow.py` or
  `review_report.py`; `flow.py` line count is lower; existing import paths still resolve.
- **Discard:** If `review_report.py` exceeds about 400 lines, stop and split rendering helpers further rather than
  adding them back to `flow.py`.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 1.8   |
| impact_pred         | 20.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 13.96 |

### Step Metrics Rationale

`EV = 0.85 * 20.0 + 0.5 * 2.0 - 0.3 * 1.8 - 3.5 = 17.00 + 1.00 - 0.54 - 3.50 = 13.96`. High impact because this removes
the fabricated text that reached PR #97; entropy reflects about 300 deleted lines and one new module in a hot file
(`flow.py`, conflict likelihood).

---

## Step 3: Wire `run_ensemble()` and `decide_consensus()` into `run_review_loop()`

- **Sub‑intent recommendation:** NO
- **Reasoning:** The core change and the plan's bottleneck, but it is call-site wiring against already-tested APIs;
  splitting it would only duplicate rebase work on `flow.py`, and the operator ruled out sub-intents.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every iteration is decided by real reviewer results plus mergeability and CI; the old rule never
  approves on its own.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Add a keyword-only seam `run_ensemble_fn` (default `review_ensemble.run_ensemble`) to `run_review_loop()`, or an
   equivalent never-checkpointed `FlowContext` field mirroring `dispatcher`; choose the one that adds fewer lines. Tests
   inject a fake; production uses the real function.
2. Before the loop (after the empty-diff guard), resolve the `ReviewTarget` once with the existing
   `resolve_review_target(repo_dir, review_target)` (for a branch target this resolves locally; a resolution error fails
   closed with status `failed` and a clear message, no iteration and no post).
3. Add a small helper in `review_ensemble.py` (or `review_target.py`) that builds a `ReviewRequest` from a
   `ReviewTarget`, the base ref, the iteration and the PR title/body: diff text is `git diff <base>...<head_sha>`
   through the existing `run_git`, excluding metadata paths the same way the empty-diff guard does
   (`git.EMPTY_DIFF_EXCLUDES`); a failing diff raises so the iteration becomes FAILED. PR title/body come from the
   `ReviewTarget`, else from `generate_semantic_pr_metadata()` output already computed for the PR.
4. In each iteration, after the mergeability and CI probes: build the request, call the seam with `agent=context.agent`,
   `model=context.model`, `size=ensemble_size`, then
   `decide_consensus(results, ensemble_size, mergeable=not is_dirty and not missing_ref, ci_green=all_passed and not tests_failed)`.
   Pass `None` rather than `False` where the probe could not measure (unknown is never clean).
5. Replace the old `finding_ids`/severity derivation with real data: severity is the highest severity across reviewers'
   findings (plus the existing DIRTY/REF/CI infrastructure finding ids, kept so mergeability and CI failures stay
   visible); `consensus_reached` is `verdict is APPROVED` only.
6. Ledger rows via `record_review_action()` (already under `LedgerLock`): one `ACT-REV` row per reviewer (agent, model,
   status, verdict, counts, `review_iteration`, redacted `error_context` for FAILED) and one `ACT-FIND` row per finding
   (severity, file, line, bounded summary, reviewer index). Keep existing row keys and `action_origin: "pr_review"`; add
   only the new keys documented in Step 7. Dry runs suppress and count as today and never call the ensemble.
7. Delete the per-iteration `ACT-RES` "resolver triggered" block.
8. Stop after the first iteration whose verdict is CHANGES_REQUESTED or FAILED (log the reason); keep the existing
   missing-ref, not-measured and budget-breach exits.
9. Map statuses: APPROVED gives `approved`; CHANGES_REQUESTED gives `changes_requested` (or keeps `conflicted_dirty`
   when dirty); FAILED gives `failed`; add `reviewers` (serialised per-reviewer summaries, no report text) and
   `ensemble_verdict` to the returned dict and `consensus_data`.
10. Keep functions small: extract `_ensemble_iteration(...)` and `_record_reviewer_rows(...)` helpers rather than
    growing the loop body; confirm `flow.py` net line count stays at or below the original.

### Dependencies & Criticality

- **Depends on:** Step 2 (line budget and report signature)
- **Is Bottleneck:** YES (p 0.78, above 0.6, so no sub-intent required)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (append-only ledgers; only `executions.jsonl` is written
  here); `docs/safety.md` Invariant 6 (immutability); intent "only the three append-only ledgers".
- **Potential failure modes for this step:** approving with zero reviewers (prevented by `decide_consensus`); treating
  unknown CI as green; writing report text or secrets into the ledger; breaking the historical-row shape tests.
- **Guardrails and early‑abort checks:** never write `report_text` to the ledger; `failure_reason` is already redacted;
  keep historical keys and add new ones only; run `tests/test_flow.py` and `tests/test_review_loop.py` right after.

### Success & Discard Criteria

- **Success:** With a fake ensemble, only three clean reviewers plus mergeable and green CI approve; all other cases
  stop after one iteration with a non-APPROVED status and the expected `ACT-REV`/`ACT-FIND` rows.
- **Discard:** If the wiring needs changes inside `review_ensemble.run_ensemble` or `decide_consensus` semantics, stop
  and record why (those were reviewed and merged in the second change).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 2.6   |
| impact_pred         | 35.0  |
| cost_pred           | 5.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 23.52 |

### Step Metrics Rationale

`EV = 0.78 * 35.0 + 0.5 * 4.0 - 0.3 * 2.6 - 5.0 = 27.30 + 2.00 - 0.78 - 5.00 = 23.52`. Highest impact (it is the actual
fix) and highest entropy (hot function, ledger row shape, several existing tests). The p estimate reflects the number of
existing assertions on row order and statuses that must be updated consistently.

---

## Step 4: Stage 4 routing (ROUTE) or fail-closed host guard (FALLBACK), plus the preflight gate

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small, security-critical change whose shape is fixed by Step 1; both branches are short.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The Step 1 decision holds when implemented: either the dispatcher route works with fakes,
  or a single guard reliably refuses host-side reviewer turns.
- **Learning target:** Whether the existing sandbox signals (`HOLON_IN_SANDBOX`, `HOLON_ROLE`) are sufficient as a
  routing guard without a new detection mechanism.
- **Maximum acceptable cost for this learning:** within this step's cost of 3.0.

### Intent & Git Integration

- **Step Intent:** Reviewer agent turns never run on the host.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Common guard (both branches): in `run_review_loop()`, before the first ensemble call and only when not a dry run,
   call a small helper `reviewers_sandboxed()` that reads the existing explicit sandbox signals (`HOLON_IN_SANDBOX`
   truthy or `HOLON_ROLE` set; reuse `agent_defaults.SANDBOX_GUARD_VARS`; do not trust the `USER=holon` heuristic). If
   false, return a `failed` result with the error "Refusing to run reviewer agent turns on the host: Stage 4 reviewers
   run only inside the sandbox (use 'holon review-loop' without --local, or run inside the review-loop container)",
   write no `ACT-REV` reviewer rows and post nothing. This also covers `holon review-loop --local`/`--json` on a host.
2. ROUTE branch (only if Step 1 chose it): `run_review_stage()` replaces its direct `run_review_loop()` call with
   `_dispatch_role(context, "review-loop", [execution_branch, agent, model, --max-iterations=N, --ensemble-size=M])`;
   exit code 0 maps to the HALTED_FOR_HUMAN approval path, anything else to FAILED with the exit code in the error; the
   host does not post (the container already did). Document the deviation.
3. FALLBACK branch (expected): `run_review_stage()` keeps calling `run_review_loop()` on the host; the guard makes it
   fail closed with the clear error above unless the flow itself runs inside a sandbox. Record the routing follow-up in
   the execution notes.
4. `cli.py`: change `_AGENT_FLOW_STAGES` to include `"review"` and rewrite its comment: review dispatches reviewer agent
   turns (sandboxed), so a flow resumed at review must preflight the agent runner; only calibration needs none. Check
   that `flow_dispatches_agent()` and the preflight call site at about line 2564 need no further change.

### Dependencies & Criticality

- **Depends on:** Step 1 (decision), Step 3 (ensemble call site)
- **Is Bottleneck:** NO (FALLBACK is always available)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §2 and Invariant 2; `holon-config/world/constraints.md` §2; operator answer
  "Stage 4 must refuse with a clear error (fail closed)".
- **Potential failure modes for this step:** the guard passes on a developer host because a stray `HOLON_ROLE` is
  exported; existing tests that run `run_review_loop()` on the host now fail.
- **Guardrails and early‑abort checks:** tests set the sandbox variable explicitly through `monkeypatch` only where the
  test asserts the in-sandbox behaviour; a dedicated test asserts refusal with both variables cleared.

### Success & Discard Criteria

- **Success:** With the sandbox variables cleared, Stage 4 returns FAILED with the clear error and the fake ensemble is
  never called; with ROUTE, a fake dispatcher records exactly one `review-loop` dispatch and no host ensemble call.
- **Discard:** If ROUTE needs any change to `run_docker_container` mounts, revert to FALLBACK immediately.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.8   |
| impact_pred         | 20.0  |
| cost_pred           | 3.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 14.46 |

### Step Metrics Rationale

`EV = 0.80 * 20.0 + 0.5 * 4.0 - 0.3 * 1.8 - 3.0 = 16.00 + 2.00 - 0.54 - 3.00 = 14.46`. Impact is high because it closes
the host-execution path; entropy includes the sandbox-escape-risk term (SER) and the knock-on effect on host-run tests.

---

## Step 5: Posting conditions, exit codes, summary and CLI help

- **Sub‑intent recommendation:** NO
- **Reasoning:** Small edits in three files that follow directly from Step 3's result dictionary.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Post only real reviews, exit 0 only for APPROVED, never merge.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. In `run_review_loop()`, call `post_pr_review_consensus()` only when at least one reviewer is COMPLETED and the
   iteration did not fail for infrastructure reasons (target resolution, diff, isolation, sandbox refusal). Otherwise
   log why nothing is posted and write no `ACT-POST` row. A failed post still turns the result into `post_failed`.
2. `run_review_loop_cli()`: return 0 only when `ensemble_verdict` is APPROVED and `approved` is true; print one summary
   line per reviewer (index, agent, model, status, verdict, counts, or FAILED with its redacted reason).
3. `entrypoint/review_loop.py`: make its exit code follow the same rule (0 only for APPROVED) and replace its
   `--ensemble-size` help with the intent's exact text.
4. `cli.py` `review-loop` parser: `--ensemble-size` help becomes "Number of independent reviewer agent turns per
   iteration; approval needs every reviewer to approve with zero findings (default: 3)"; keep the `>= 1` validation.
5. `holon flow`: verify again that no equivalent option exists (search for `ensemble`, `reviewers`, `review-size` in the
   flow parser). Only if none exists, add `--ensemble-size` with the same help and `>= 1` validation, thread it through
   `FlowContext` (new field with default 3, included in checkpoints only if the existing schema allows an additive field
   without a version bump; otherwise not checkpointed) into `run_review_stage()`.
6. Confirm by grep that no `gh pr merge`, `--auto` or auto-merge call is introduced.

### Dependencies & Criticality

- **Depends on:** Step 3
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` §5 and Invariant 4 (human-only merge).
- **Potential failure modes for this step:** a FAILED-but-posted comment that looks like a review; checkpoint schema
  breakage from a new `FlowContext` field.
- **Guardrails and early‑abort checks:** test the "nothing posted when zero reviewers completed" path; run the
  checkpoint round-trip tests in `tests/test_flow.py` after adding any field.

### Success & Discard Criteria

- **Success:** CLI and entrypoint return 1 for every non-APPROVED fake outcome and 0 only for the clean fake ensemble;
  help text matches exactly.
- **Discard:** If adding the flow option forces a checkpoint schema version bump, do not add it; record it as a
  follow-up.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 1.0   |
| impact_pred         | 10.0  |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 7.50  |

### Step Metrics Rationale

`EV = 0.88 * 10.0 + 0.5 * 1.0 - 0.3 * 1.0 - 1.5 = 8.80 + 0.50 - 0.30 - 1.50 = 7.50`. Well-specified edits; moderate
impact because exit codes are what `holon flow` and operators act on.

---

## Step 6: Tests with fakes only (pytest)

- **Sub‑intent recommendation:** NO
- **Reasoning:** Tests must land with the behaviour change on the same branch; the world ruleset requires test changes
  to be declared in the plan, which this step does.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove the new behaviour and update every assertion that encoded the fabricated report.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Declared new tests (in `apps/sandbox-executor/tests/test_review_loop.py`, plus `tests/test_review_report.py` for the
   new module, following the ruleset's test-location rule):
   - three clean fake reviewers plus mergeable and green CI give `approved`, CLI exit 0, three `ACT-REV` rows;
   - zero reviewers (`ensemble_size` honoured, fake returns an empty list), one FAILED reviewer, one unparseable
     reviewer (FAILED with a trailer reason), one CHANGES_REQUESTED reviewer and one APPROVED-with-a-nit reviewer each
     block approval, stop after iteration 1, give CLI exit 1 and record `ACT-FIND` rows (where findings exist) and
     `ACT-REV` rows in a temporary `holon-knowledge/ledger/executions.jsonl`;
   - no `ACT-RES` row is ever written;
   - rendered report contains each fake reviewer's agent and model, has no 8-hex token matching any
     `sha256(f"{commit}-reviewer-{i}")[:8]`, never contains "Audit Remediation and Flow Review Hardening", and uses
     `ReviewTarget.title` when the intent has none (and the PR number or branch when neither exists);
   - with the sandbox variables cleared, Stage 4 fails closed and the fake ensemble and any agent runner are never
     called (or, under ROUTE, a fake `context.dispatcher` asserts the `review-loop` route);
   - nothing is posted when no reviewer completed.
2. Fakes only: inject the ensemble seam, monkeypatch `check_pr_mergeability`, `probe_ci_checks`,
   `post_pr_review_consensus`, `resolve_review_target`/diff helpers and `shutil.which("gh")`; never real gh, Docker,
   remotes or LLM runners. Reuse `hermetic_fixtures.py` and `gh_write_guard.py`.
3. Declared updates to existing tests (each to be listed in the execution notes with old and new assertion):
   `tests/test_flow.py` around lines 1833-1900 (resolver-row assertions removed; expectation becomes a stop after
   iteration 1), 3167-3241 (expected row prefixes no longer include `ACT-RES`; historical-key fixtures for old rows are
   kept because historical ledger lines still exist), 3564-3568 (new report signature), and any test that relied on the
   old "not dirty and CI green" approval, which now injects a clean fake ensemble and sets the sandbox variable.
4. Run affected modules first:
   `uv run pytest apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_flow.py apps/sandbox-executor/tests/test_review_report.py apps/sandbox-executor/tests/test_review_ensemble.py apps/sandbox-executor/tests/test_cli.py`
   from the repository root.

### Dependencies & Criticality

- **Depends on:** Steps 2, 3, 4, 5
- **Is Bottleneck:** NO (failures feed back into Steps 3 to 5)

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/ruleset.md` §3 (pytest; test changes declared in the plan); intent item 7.
- **Potential failure modes for this step:** a test accidentally reaching real gh or a runner; weakening an existing
  safety assertion while "updating" it.
- **Guardrails and early‑abort checks:** `gh_write_guard` stays active; any changed assertion is listed with a reason;
  no assertion about fail-closed behaviour is removed.

### Success & Discard Criteria

- **Success:** All declared tests pass in the affected modules; every changed assertion is listed.
- **Discard:** If more than about 25 existing assertions need changing, stop and check whether Step 3 changed a
  documented contract it should not have.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 1.6   |
| impact_pred         | 15.0  |
| cost_pred           | 3.5   |
| learning_value_pred | 3.0   |
| ev_pred             | 9.52  |

### Step Metrics Rationale

`EV = 0.80 * 15.0 + 0.5 * 3.0 - 0.3 * 1.6 - 3.5 = 12.00 + 1.50 - 0.48 - 3.50 = 9.52`. The test files are large
(`test_flow.py` has 5654 lines), so finding all dependent assertions is the main risk.

---

## Step 7: Documentation of the real Stage 4

- **Sub‑intent recommendation:** NO
- **Reasoning:** Text-only edits across eight files, reversible and low risk.
- **Step Type:** DOCUMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Every current description of Stage 4 matches the code.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. Update `docs/architecture.md`, `docs/git_flow.md`, `docs/agents.md`, `docs/budgets.md`,
   `docs/sandbox/execute_plan.md`, `README.md` and `apps/sandbox-executor/README.md`: real reviewer ensemble, the
   `--ensemble-size` option (and the flow option if added), the unanimity rule (every reviewer APPROVED with zero
   findings, plus mergeable and green CI), fail-closed behaviour (zero or failed reviewers, unknown CI, host refusal),
   and that findings stop the stage with exit 1 and no automatic resolver.
2. `docs/ledger_schema.md`: per reviewer one `ACT-REV` row (new keys listed), per finding one `ACT-FIND` row, the
   per-iteration `ACT-RES` row removed for new runs (historical rows remain valid), and no `ACT-POST` when no reviewer
   completed. Keep the "known gap" paragraph accurate given the Step 1 decision.
3. Do not touch `docs/audit_report.md`, `docs/audit-report-2026-10.md`, existing `plans/` or `executions/` records.
4. Australian English; no harness paths, bean ids or review ids added; format with the repository prettier
   configuration.

### Dependencies & Criticality

- **Depends on:** Steps 3, 4, 5
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** intent item 6 ("do not rewrite historical audit records"); intent item 8 (no bean ids).
- **Potential failure modes for this step:** prettier reformat churn; describing ROUTE when FALLBACK shipped.
- **Guardrails and early‑abort checks:** write docs after Step 4 is final; run `uv run task lint-docs`.

### Success & Discard Criteria

- **Success:** `grep -rn "resolver" docs/` returns no current-behaviour claim of an automatic resolver;
  `uv run task lint-docs` passes.
- **Discard:** None expected; if prettier fights a table, simplify the table.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.88  |
| entropy_pred        | 0.9   |
| impact_pred         | 6.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 1.0   |
| ev_pred             | 4.01  |

### Step Metrics Rationale

`EV = 0.88 * 6.0 + 0.5 * 1.0 - 0.3 * 0.9 - 1.5 = 5.28 + 0.50 - 0.27 - 1.50 = 4.01`. Broad but shallow surface area.

---

## Step 8: Verification and execution notes

- **Sub‑intent recommendation:** NO
- **Reasoning:** Single verification pass required by the intent.
- **Step Type:** TEST
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Prove lint, docs lint and the full suite pass once, and leave a complete execution record.
- **Git branch:** same execution branch
- **Sub‑intent:** NONE

### Implementation Details (No code blocks, only logic/steps)

1. From the repository root: `uv run task lint`, `uv run task lint-docs`, then `uv run task test` exactly once (no
   repeated runs; this is not a performance change). Never use `PYTHONPATH` and never run from `apps/sandbox-executor`.
2. Check `wc -l` of `flow.py` against 5107 (must be at or below), `uv.lock` unchanged (no new dependencies), and a grep
   for `hashlib`, "Audit Remediation", "-Agent Independent" and auto-merge calls.
3. Execution notes: the Step 1 decision and evidence, the routing follow-up if FALLBACK, each changed test assertion,
   the `flow.py` line delta and any deviation from the intent.

### Dependencies & Criticality

- **Depends on:** Steps 1 to 7
- **Is Bottleneck:** NO

### Safety & Constraint Considerations

- **Relevant rules:** `holon-config/world/constraints.md` §3 (only append to the ledgers); intent item 8.
- **Potential failure modes for this step:** an unrelated flaky test; the full suite surfacing an untouched module that
  imported the removed helper.
- **Guardrails and early‑abort checks:** a failure in the full run is fixed and only the affected module re-run; the
  full suite is not repeated unless a code change touched shared modules, in which case record why.

### Success & Discard Criteria

- **Success:** All three commands exit 0; notes complete.
- **Discard:** If the full suite fails in modules unrelated to this change, record the failure verbatim and do not mask
  it.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.85  |
| entropy_pred        | 0.3   |
| impact_pred         | 5.0   |
| cost_pred           | 1.5   |
| learning_value_pred | 2.0   |
| ev_pred             | 3.66  |

### Step Metrics Rationale

`EV = 0.85 * 5.0 + 0.5 * 2.0 - 0.3 * 0.3 - 1.5 = 4.25 + 1.00 - 0.09 - 1.50 = 3.66`. Mostly a gate; its impact is
confidence rather than new behaviour.

---

### Strategy Rationale

The intent is accepted as is: it is the last of three sequential changes, its prerequisites are merged, and the operator
ruled out splitting. The predicted entropy (10.6) is below the 14.0 budget, so no sub-intent list is used.

Ordering puts the cheap probe first (it fixes Step 4's shape), then the report extraction (it frees the `flow.py` line
budget that the intent caps), then the wiring. The single strongest assumption, stated against the world ruleset, is
that the host cannot read the container's ledger rows without a new transport, because the role container clones the
repository rather than mounting it; therefore FALLBACK (host refuses, with a follow-up) is the expected Step 4 outcome.
This keeps `docs/safety.md` Invariant 2 intact in either branch.

Aggregation:

- `entropy_pred` is the sum of step entropies: `0.6 + 1.8 + 2.6 + 1.8 + 1.0 + 1.6 + 0.9 + 0.3 = 10.6`.
- `cost_pred` is the sum of step costs: `2.0 + 3.5 + 5.0 + 3.0 + 1.5 + 3.5 + 1.5 + 1.5 = 21.5`.
- `p_success_pred` is a qualitative judgement anchored on the bottleneck Step 3 (0.78) and discounted for the correlated
  risks in Steps 4 and 6 (the same test files): `0.70`. A pure product of step probabilities (about 0.27) would
  understate it, because failures in Steps 5 to 8 are cheap to fix within the same branch.
- `impact_pred` is a qualitative 90.0: the change removes a live provenance defect (fabricated approvals posted to real
  PRs) and closes host-side agent execution. Step impacts sum to 121 but overlap.
- `learning_value_pred` is 6.0: driven by the routing probe (7.0) and the first real end-to-end use of the ensemble.
- `EV = 0.70 * 90.0 + 0.5 * 6.0 - 0.3 * 10.6 - 21.5 = 63.00 + 3.00 - 3.18 - 21.50 = 41.32`.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `docs/safety.md` §2 and Invariant 2 (sandboxing mandatory): reviewer turns only inside the sandbox.
  - `docs/safety.md` §5 and Invariant 4 (human review is the final gate): no merge, no auto-merge, posted report only
    from real reviewer data.
  - `docs/safety.md` Invariant 6 and `holon-config/world/constraints.md` §3 (append-only ledgers): only appends to
    `executions.jsonl` through `record_review_action()` under `LedgerLock`; historical rows untouched.
  - `holon-config/world/constraints.md` §1 (git flow): one execution branch under the plan branch; no commits to `main`.
  - `holon-config/world/ruleset.md` §2 and §3 (typing, docstrings, pytest, declared test changes; Python 3.13; `uv`).
- **Potential violations or edge cases:**
  - Host-side `holon review-loop --local` used to run the loop on the host; with real reviewers this would be agent
    turns on the host.
  - A developer shell exporting `HOLON_ROLE` would satisfy the guard on the host.
  - Finding summaries from a reviewer are untrusted text that end up in a public PR comment and in the ledger.
  - Container ledger rows not reaching the host (known gap) could tempt a mount, widening the sandbox boundary.
- **Mitigations built into the plan:**
  - A single explicit sandbox guard before any ensemble call, using only explicit signals; dedicated refusal test.
  - Bounded and redacted reasons and summaries; `report_text` never written to the ledger.
  - The routing probe forbids any new mount or transport; FALLBACK is the safe default.
  - `decide_consensus()` unchanged: zero, failed or unknown never approves.
- **Residual risk accepted (and why):** Under FALLBACK, `holon flow` Stage 4 on a host will fail closed until the
  routing follow-up lands, so flows cannot reach an approved review automatically. That is the operator's stated
  preference over host-run reviewers and over fabricated approvals.
- **Allocated Entropy Budget:** 14.0
- **Predicted Plan Entropy:** 10.6
- **Budget Compliance:** The strategy fits within budget (10.6 of 14.0, leaving 3.4 headroom for a ROUTE outcome or more
  test updates than expected).
