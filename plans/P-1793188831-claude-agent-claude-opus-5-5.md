# Plan for I-1793188814-native-review-loop-real-ensemble-no-fabricated-approval

- **Plan ID:** P-1793188831-claude-agent-claude-opus-5-5
- **Parent Intent ID:** NONE
- **Agent:** claude-agent/claude-opus-5-5 (version: 2.1.302)
- **Created At:** 2026-10-28T12:00:32.275Z

## Planner Autonomy Summary

- **Intent handling:** SPLIT
- **Reframed intent (if applicable):** Not reframed. The goal is kept in full but delivered as three ordered child
  intents that merge into this root intent branch: (1) fail-closed target resolution, guards, posting and the host busy
  loop; (2) a new, isolated `review_ensemble` module (reviewer dispatch, trailer parsing, unanimity consensus) tested
  against fakes; (3) wiring the ensemble into `run_review_loop()` and Stage 4, rebuilding the consensus report from real
  reviewer results, CLI help and docs. The root branch only reaches `main` after all three land, so `main` never sees a
  half-real review loop.
- **Exploration stance:** balanced. The work is production hardening and mostly exploit, but two unknowns are tested
  cheaply up front: the root cause of the 99% CPU busy loop (a concrete suspect is already identified) and the sandbox
  route by which `holon flow` Stage 4 can run reviewer agent turns without running an agent on the host.
- **Safety priority level:** critical
- **Priority Justification:** The defect fabricates provenance (reviewer ids, votes, titles) and posts it to GitHub as
  if it were an independent review, which undermines the only human gate in `docs/safety.md` ("Human review is the final
  gate", Invariant 4) and the Human-Only merge boundary in `holon-config/world/constraints.md` section 1. The fix also
  introduces agent turns in Stage 4, which must obey "Sandboxing is mandatory for execution" (`docs/safety.md` principle
  2, `constraints.md` section 2) and append-only ledgers (`constraints.md` section 3).

## Exploration

- **Proportion of steps that are exploratory:** 0.33
- **Justification:** One of three steps (Step 1) carries an explicit diagnostic hypothesis (the busy loop) with a hard
  cost cap; Step 3 carries a bounded design probe (sandbox route for reviewer turns). Everything else is exploit work
  with fully specified acceptance tests from the intent goal.

## Overall Plan Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.58  |
| entropy_pred        | 18.5  |
| impact_pred         | 100.0 |
| cost_pred           | 30.0  |
| learning_value_pred | 6.0   |
| ev_pred             | 25.45 |

`EV = P(success) * Impact + mu * LearningValue - lambda * Delta_S_intent - Cost` with `mu = 0.5`, `lambda = 0.3` from
`holon-config/metrics/ev_config.json`:
`EV = 0.58 * 100.0 + 0.5 * 6.0 - 0.3 * 18.5 - 30.0 = 58.0 + 3.0 - 5.55 - 30.0 = 25.45`. Cost is the step sum `28.0` plus
`2.0` for the coordination overhead of three sequential child pipelines (re-plans and rebases).

---

## Step 1: Make Stage 4 fail closed on target resolution, clone failure, empty diff, posting and host waits

- **Sub‑intent recommendation:** STRONGLY_YES
- **Reasoning:** Independently valuable security fix (it alone stops the 2026-10-24 "clone failed but APPROVED" path and
  the 21-minute busy loop), touches the container entrypoint, `flow.py` call sites and `cli.py`, and has its own clean
  test surface; it should be evaluated and calibrated on its own branch.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** The host-side 99% CPU hang with no child process is `_find_git_root()` in
  `apps/sandbox-executor/src/sandbox_executor/cli.py`: its inner `while True` computes `parent` but never assigns
  `cur = parent`, so any start directory without a `.git` entry spins forever. It is reached through
  `setup_token_reduction_proxy()`, which the review-loop path calls when `holon-coherence` turns token reduction on.
- **Learning target:** Confirm or refute the suspect with a pytest that calls `_find_git_root()` on a `tmp_path`
  subdirectory without `.git` under a short `pytest` timeout guard (a thread join with a deadline, no new dependency).
  If refuted, enumerate every other unbounded wait on the review path (`_wait_for_ci_registration`, the single
  pending-CI `time.sleep(15)` re-probe, `check_pr_mergeability` retries, `_wait_for_proxy`) and bound each one.
- **Maximum acceptable cost for this learning:** 1.5 cost units (about 15 minutes of executor time); past that, bound
  every loop defensively and record the root cause as unconfirmed in the execution report.

### Intent & Git Integration

- **Step Intent:** A review target that cannot be resolved, cloned, checked out, diff-verified or posted can never yield
  an approval or a zero exit, and no host-side wait can spin unbounded.
- **Git branch:** child intent branch created by the flow under this root intent (slug suggestion
  `review-loop-fail-closed-target-and-guards`)
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. Add a small new module `apps/sandbox-executor/src/sandbox_executor/review_target.py` (target resolution only) with:
   - a frozen dataclass `ReviewTarget` holding `head_branch`, `head_sha`, `pr_number` (optional), `title` and `body`
     (both optional) and the original `raw` target string;
   - `is_pr_reference(target)` returning True for a bare number or a GitHub PR URL;
   - `resolve_review_target(repo_dir, target, run_gh)` that, for a PR reference, calls
     `gh pr view <target> --json number,headRefName,headRefOid,title,body` through an injectable `run_gh` seam and
     raises a dedicated `ReviewTargetError` when gh is missing, exits non-zero, times out (60 s) or returns an empty
     branch or SHA; for a branch target it fetches nothing itself and returns the branch with the SHA from the existing
     `_resolve_review_commit()` logic (moved or reused, not duplicated).
   - The default `run_gh` uses the same `subprocess.run([...], timeout=60, check=False)` shape already used for
     `gh pr view` in `flow.py`, so no second gh layer is invented; `flow.py`'s existing inline `gh pr view headRefOid`
     call in `_resolve_review_commit()` is replaced by a call into this module (call-site change only).
2. Container entrypoint `entrypoint/review_loop.py`: resolve the target before any git operation; clone with
   `--branch <head_branch>` (never the raw number), then verify `git rev-parse HEAD` equals `head_sha` (fetch the SHA
   explicitly if the branch moved). Switch the clone and checkout calls from `check=False` to explicit return-code
   handling. On any resolution, clone, fetch or checkout failure: append one failure action record (`ACT-REV`,
   `outcome: failure`, `action_origin: pr_review`, redacted `error_context`) to
   `holon-knowledge/ledger/executions.jsonl` via `try_append_action_record(..., preserve_shape=True)` (which takes
   `LedgerLock`), print a FAILED summary and exit 1. No consensus is computed or posted. When the clone itself failed
   there is no checkout to write into, so the record is written to the workspace ledger only if a ledger directory
   exists; otherwise the failure goes to stderr and the exit code, and this limitation is documented in Step 3's docs.
3. `run_review_loop()` in `flow.py` (call-site changes only): replace the `if auto_pr and ...` gate around the
   empty-diff guard with an unconditional call, extracted into a helper in `review_target.py` named
   `verify_review_diff(repo_dir, target)` that returns verified, empty or unverified. Empty or unverified returns a
   non-approved result with status `rejected` or `failed` for every target, including PR-number targets (diff is taken
   between the resolved head SHA and the PR base) and including `--no-auto-pr`. The guard no longer depends on
   `shutil.which("gh")`.
4. Posting: when `post_pr_review_consensus()` reports failure or raises, the loop result gets `error` set and consensus
   `approved` forced False with status `post_failed`; the ACT-POST failure row is still written. `run_review_loop_cli()`
   and the container entrypoint already exit 1 when not approved. Never call `gh pr merge` or enable auto-merge; add an
   assertion-style test that the gh write guard sees no merge command.
5. Busy loop: fix `_find_git_root()` by advancing `cur = parent`; audit every `while` loop reachable from `review-loop`
   and `holon flow` Stage 4 and make sure each has a monotonic deadline and a sleep (the pending-CI re-probe becomes a
   bounded poll using the existing `CI_REGISTRATION_POLL_SECONDS` and `review_iteration` stage budget).
6. Tests (pytest, in `apps/sandbox-executor/tests/test_review_target.py` plus additions to `test_review_loop.py` and
   `test_cli.py`), all with fakes at the `run_gh`, `run_cmd` and dispatcher seams, never real gh, git remotes, Docker or
   LLM runners: PR number and PR URL resolve to `headRefName` and `headRefOid`; gh failure raises `ReviewTargetError`;
   the entrypoint never passes a bare number to `git clone --branch`; a failing clone exits non-zero, posts nothing and
   writes exactly one failure action record; the empty-diff guard runs with `auto_pr=False` and for a PR-number target;
   a failed post makes the loop non-approved and the CLI exit 1; `_find_git_root()` returns within the deadline for a
   directory with no `.git` ancestor inside `tmp_path`; the pending-CI wait honours its deadline with an injected clock
   and sleep.
7. Run `uv run task test`, `uv run task lint` and `uv run task lint-docs` from the repository root.

### Dependencies & Criticality

- **Depends on:** NONE
- **Is Bottleneck:** NO (Steps 2 and 3 can still land; but the root intent is only complete when all three succeed)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` principles 2 and 5 and Invariant 4; `holon-config/world/constraints.md` sections
  1 (Human-Only merge) and 3 (append-only ledgers); `holon-config/world/ruleset.md` section 3 (pytest, tests next to
  source).
- **Potential failure modes for this step:** the write guard in `tests/gh_write_guard.py` blocks a read-only
  `gh pr view` used by a test that forgot to inject the fake; moving `_resolve_review_commit()` logic breaks existing
  `test_review_loop.py` / `test_flow.py` expectations; an unconditional guard makes `holon flow` dry runs fail.
- **Guardrails and early‑abort checks:** keep the dry-run path explicit (dry runs report `not_measured`, never
  approved); run the existing review-loop and flow test modules after each sub-step; ledger writes only through
  `try_append_action_record` under `LedgerLock`, append-only.

### Success & Discard Criteria

- **Success:** all listed tests pass; no code path can print `Consensus: APPROVED` or exit 0 after a resolution, clone,
  checkout, diff-verification or posting failure; full suite, lint and lint-docs green.
- **Discard:** cost exceeds `1.5 * 8.0 = 12.0` units, or more than 10 pre-existing tests need assertion changes that are
  not explained by the fail-closed behaviour.

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.80  |
| entropy_pred        | 5.5   |
| impact_pred         | 35.0  |
| cost_pred           | 8.0   |
| learning_value_pred | 4.5   |
| ev_pred             | 20.6  |

### Step Metrics Rationale

`EV = 0.80 * 35.0 + 0.5 * 4.5 - 0.3 * 5.5 - 8.0 = 28.0 + 2.25 - 1.65 - 8.0 = 20.6`. High probability because the busy
loop suspect is concrete and the other fixes are small call-site changes with clear seams. Entropy reflects about six
files touched (new module, entrypoint, `flow.py`, `cli.py`, two to three test modules). Learning value comes from
confirming the busy-loop root cause and from mapping every unbounded wait on the review path.

---

## Step 2: Add the `review_ensemble` module (reviewer dispatch, trailer parsing, unanimity consensus)

- **Sub‑intent recommendation:** STRONGLY_YES
- **Reasoning:** This is the new core logic: a reusable, self-contained module that can be built and fully tested
  against fakes before anything calls it, which keeps `flow.py` untouched in this child and isolates the riskiest design
  decisions (trailer format, failure classification, isolation between reviewers).
- **Step Type:** IMPLEMENTATION
- **Exploration level:** EXPLOIT

### Intent & Git Integration

- **Step Intent:** Provide small, intention-revealing functions that run N independent reviewer turns, parse each
  reviewer's report trailer from the file it wrote, and compute a consensus that can only be APPROVED by unanimity of
  completed, finding-free reviewers plus mergeable and green CI.
- **Git branch:** child intent branch created by the flow under this root intent (slug suggestion
  `review-ensemble-module`)
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. New module `apps/sandbox-executor/src/sandbox_executor/review_ensemble.py` with typed, docstringed (Australian
   English) public API:
   - `ReviewerVerdict` (StrEnum: `APPROVED`, `CHANGES_REQUESTED`), `ReviewerStatus` (StrEnum: `COMPLETED`, `FAILED`).
   - `ReviewFinding` dataclass (`severity` critical, important or nit; `path`; `line`; `summary`).
   - `ReviewerResult` dataclass (`index`, `agent`, `model`, `status`, `verdict` (optional), counts, `findings`,
     `report_path`, `failure_reason`). A FAILED result carries no verdict and is never counted as a vote.
   - `ReviewRequest` dataclass (`repo_dir`, `head_sha`, `base_ref`, `pr_title`, `pr_body`, `diff_text`, `iteration`).
   - `parse_review_trailer(text)` returning a `ReviewerResult` fragment or raising `TrailerError`. Trailer format: a
     final fenced-free block delimited by `<!-- holon-review-trailer -->` and `<!-- /holon-review-trailer -->` lines
     containing one JSON object with keys `verdict`, `critical`, `important`, `nit` and `findings` (list of objects with
     `severity`, `file`, `line`, `summary`). JSON is used because it parses with the standard library (no new
     dependency). Validation rejects unknown verdicts, negative counts, counts that disagree with the findings list, and
     an `APPROVED` verdict with any non-zero count (treated as unparseable, so it fails closed).
   - `build_reviewer_prompt(template_text, request, report_rel)` filling `holon-config/prompts/review_loop.template.md`
     placeholders plus the diff, PR description and the exact report path and trailer contract.
   - `run_reviewer(request, index, agent, model, *, turn_runner, runner_factory, budgets, stage_timer)` that creates a
     fresh per-reviewer scratch directory under `.holon/review/iter-<n>/reviewer-<i>/` (target-native path, excluded
     from commits), builds the agent command with `get_runner(agent).build_cmd(...)` and runs it through
     `supervise_authoring_turn(...)` from `turns.py` with an `ArtefactSpec` for the report, the same supervision used by
     the planner. A runner exception, a budget breach, a missing report or a `TrailerError` returns a FAILED result with
     a redacted reason. Ephemeral credentials are cleaned in a `finally`, as the planner does.
   - `run_ensemble(request, size, agent, model, **seams)` running reviewers sequentially; before each reviewer starts,
     earlier reviewers' report files have already been read into memory and removed from the workspace, and each
     reviewer's prompt contains only the diff and PR description, so no reviewer can see another reviewer's output.
   - `decide_consensus(results, configured_size, mergeable, ci_green)` returning `APPROVED`, `CHANGES_REQUESTED` or
     `FAILED`: FAILED when zero reviewers were dispatched, any reviewer FAILED, or `len(results) != configured_size`;
     CHANGES_REQUESTED when any completed reviewer requested changes, reported any finding, or mergeability or CI is not
     clean; APPROVED only otherwise. Zero completed reviewers can never approve. Each function stays under about 25
     lines with at most two levels of branching.
2. Extend `holon-config/prompts/review_loop.template.md` with a "Reviewer turn contract" section: the reviewer reviews
   only the supplied diff and description, writes its full report to the given path, ends it with the trailer block,
   must not modify code, commit, push, call `gh pr merge` or enable auto-merge, and must not read other files under
   `.holon/review/`. Remove nothing that other callers rely on; add no new bean ids.
3. Tests in `apps/sandbox-executor/tests/test_review_ensemble.py` with a fake `turn_runner` that writes chosen report
   text (or nothing, or raises, or reports a budget breach): trailer round-trip; malformed, missing and inconsistent
   trailers fail; one failed reviewer blocks approval; one unparseable reviewer blocks approval; one `CHANGES_REQUESTED`
   blocks; a single nit blocks; zero dispatched and zero completed cannot approve; non-mergeable or red CI blocks; three
   clean reviewers plus mergeable and green CI approve; each reviewer's prompt contains no other reviewer's report text
   and earlier report files are gone when the next reviewer starts. No real runner, gh, git remote or Docker is touched.
4. Run `uv run task test`, `uv run task lint`, `uv run task lint-docs` from the repository root.

### Dependencies & Criticality

- **Depends on:** NONE (logically independent of Step 1; runs after it because children run sequentially)
- **Is Bottleneck:** YES (without it there is no real reviewer; p_success 0.78 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` principle 2 (agent turns only in a sandbox) and the Human-Only merge boundary;
  `holon-config/world/ruleset.md` section 2 (typing and docstrings) and section 3 (pytest, matching test file).
- **Potential failure modes for this step:** `supervise_authoring_turn` assumes a planner-style artefact spec and
  follow-up re-prompt that does not fit a report trailer; `.holon/review/` scratch files leak into commits; trailer
  parsing too lenient (would let a malformed report vote).
- **Guardrails and early‑abort checks:** if `supervise_authoring_turn` cannot be reused cleanly, call `run_turn`
  directly with the same `TurnBudgets` and `StageTimer` and document why; add `.holon/review/` to the excluded paths
  used by the empty-diff guard and to `.gitignore` handling only if the repository already ignores `.holon/` (verify
  before editing); strict parser by default, with every rejection a FAILED reviewer.

### Success & Discard Criteria

- **Success:** module importable with no `flow.py` change; all listed tests pass; lint and lint-docs green; no function
  longer than about 30 lines.
- **Discard:** cost exceeds `1.5 * 9.0 = 13.5` units, or turn supervision would need changes to `turns.py` beyond a new
  optional parameter (then re-plan the child with a direct `run_turn` path).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.78  |
| entropy_pred        | 6.0   |
| impact_pred         | 30.0  |
| cost_pred           | 9.0   |
| learning_value_pred | 4.0   |
| ev_pred             | 14.6  |

### Step Metrics Rationale

`EV = 0.78 * 30.0 + 0.5 * 4.0 - 0.3 * 6.0 - 9.0 = 23.4 + 2.0 - 1.8 - 9.0 = 14.6`. New-file work with injectable seams is
predictable, but the reuse of planner turn supervision for a different artefact is the main uncertainty. Entropy is
moderate: one new module, one prompt template, one new test module.

---

## Step 3: Wire the ensemble into `run_review_loop()` and Stage 4, rebuild the report from real results, update CLI and docs

- **Sub‑intent recommendation:** STRONGLY_YES
- **Reasoning:** This is the integration child that removes the fabricated report and changes user-visible Stage 4
  behaviour, the CLI help and several docs; it depends on Steps 1 and 2 and is the step most likely to collide with
  existing `test_review_loop.py` and `test_flow.py` expectations, so it needs its own evaluation and calibration.
- **Step Type:** IMPLEMENTATION
- **Exploration level:** BALANCED

- **Hypothesis being tested:** `holon flow` Stage 4 can run reviewer turns inside the sandbox by dispatching the
  existing `review-loop` role container through `_dispatch_role()` (the dispatcher planner and executor use), with the
  container running the ensemble and the host reading the stage outcome from the container exit code and the ledger rows
  the container records, rather than by running any agent on the host.
- **Learning target:** Whether the existing dispatcher and container entrypoint can carry the ensemble size and return
  the review outcome without a new transport. If not, the fail-closed fallback applies: host-side Stage 4 refuses to
  dispatch reviewers outside a sandbox and fails with a clear error, which is still strictly safer than the current
  fabricated approval.
- **Maximum acceptable cost for this learning:** 2.0 cost units for the routing probe; past that, ship the fail-closed
  fallback and record the routing work as a follow-up intent in the execution report.

### Intent & Git Integration

- **Step Intent:** Every Stage 4 iteration is decided by real reviewer results plus mergeability and CI, the posted
  report contains only real reviewer data, and the CLI and docs describe the behaviour honestly.
- **Git branch:** child intent branch created by the flow under this root intent (slug suggestion
  `review-loop-wire-real-ensemble`)
- **Sub‑intent:** NEW

### Implementation Details (No code blocks, only logic/steps)

1. `run_review_loop()` call-site changes only: per iteration, after mergeability and CI probes, build a `ReviewRequest`
   from the resolved `ReviewTarget` (Step 1), the real diff between base and head SHA, and the PR title and body; call
   `run_ensemble()` and `decide_consensus()` from `review_ensemble`. The old rule "not dirty and CI green" no longer
   approves on its own. Record one `ACT-FIND` row per finding and one `ACT-REV` row per reviewer (agent, model, status,
   verdict, counts) via `record_review_action()`. Stop after the first iteration whose result is `CHANGES_REQUESTED` or
   `FAILED` (no resolver exists, so later iterations would review the same diff); remove the misleading per-iteration
   "resolver triggered" `ACT-RES` row. Thread `ensemble_size` into `run_ensemble()` (it is currently unused). The
   `agent` and `model` for reviewers come from `context.agent` and `context.model`, now meaningful.
2. Stage 4 routing (`run_review_stage()`): apply the hypothesis above; keep host-side Stage 4 for mergeability, CI and
   posting only, and dispatch the reviewer work through `_dispatch_role()`; if the probe fails, fail closed as
   described. In either case `cli.py`'s `_AGENT_FLOW_STAGES` comment and the preflight gate are updated so a flow
   resumed at review also preflights the agent runner.
3. Report: rewrite `build_pr_review_consensus_report()` to take the list of `ReviewerResult` objects and the real title
   (intent title, else the PR title from `ReviewTarget`; if neither exists the heading carries the PR number or branch
   only). Delete the `hashlib.sha256` reviewer ids, the copied vote loop, the "3-Agent" wording derived from a constant,
   and the hard-coded fallback title. Each reviewer line shows its real agent, model, status, verdict and counts, and
   failed reviewers are shown as failed with their reason. Move the rendering helpers into `review_ensemble.py` (or a
   sibling `review_report.py` if `review_ensemble.py` would exceed about 400 lines) so `flow.py` shrinks.
4. Posting: `post_pr_review_consensus()` is called only when the ensemble produced at least one COMPLETED reviewer
   result and the iteration was not FAILED for infrastructure reasons; Step 1 already makes a failed post fail the
   stage. Exit codes: `run_review_loop_cli()` and the container entrypoint return 0 only for APPROVED; the summary
   prints the real per-reviewer verdicts. Never merge, never enable auto-merge.
5. CLI help: `--ensemble-size` help text becomes "Number of independent reviewer agent turns per iteration; approval
   needs every reviewer to approve with zero findings (default: 3)"; add the same option to `holon flow` only if no
   equivalent exists (verify first); keep validation `>= 1`.
6. Docs (Australian English, formatted with the repository prettier configuration): update Stage 4 descriptions in
   `docs/architecture.md`, `docs/git_flow.md`, `docs/agents.md`, `docs/budgets.md`, `docs/ledger_schema.md` (ACT-REV,
   ACT-FIND rows, removal of per-iteration ACT-RES), `docs/sandbox/execute_plan.md`, `README.md` and
   `apps/sandbox-executor/README.md`, wherever Stage 4 is described: real ensemble, reviewer count option, unanimity
   rule, fail-closed behaviour (resolution, clone, diff, post), and that findings currently stop the stage with no
   automatic resolver. Do not edit historical audit reports beyond a pointer if they describe current behaviour.
7. Tests (pytest; fakes only): extend `test_review_loop.py` and `test_flow.py` so that a fake ensemble with three clean
   reviewers plus mergeable and green CI approves; zero reviewers, a failed reviewer, an unparseable reviewer, a
   `CHANGES_REQUESTED` reviewer or any nit blocks approval and gives a non-zero CLI exit with findings recorded in
   `executions.jsonl`; the rendered report contains each fake reviewer's agent and model, contains no 8-hex reviewer ids
   derived from the commit, never contains "Audit Remediation and Flow Review Hardening", and uses the PR title when the
   intent has none; Stage 4 never runs an agent runner on the host (fake dispatcher asserts the route). Update existing
   tests that asserted the old fabricated report, documenting each changed assertion in the execution report.
8. Run `uv run task test`, `uv run task lint`, `uv run task lint-docs` from the repository root, then a final check of
   the integrated root branch.

### Dependencies & Criticality

- **Depends on:** Step 1, Step 2
- **Is Bottleneck:** YES (the fabricated report is only removed here; p_success 0.68 is above 0.6)

### Safety & Constraint Considerations

- **Relevant rules:** `docs/safety.md` principle 2 and Invariant 4; `holon-config/world/constraints.md` sections 1, 2
  and 3; `holon-config/world/ruleset.md` section 3 (test changes declared here, in the plan).
- **Potential failure modes for this step:** many existing assertions depend on the old report wording; routing via
  `_dispatch_role()` cannot return the review outcome; `flow.py` grows instead of shrinking; docs drift between files.
- **Guardrails and early‑abort checks:** measure `flow.py` line count before and after (it must not grow net); grep the
  repository for "Ensemble" and "Reviewer" wording after the change; if the routing probe exceeds its cost cap, ship the
  fail-closed fallback; every ledger write via `record_review_action()` under `LedgerLock`, append-only.

### Success & Discard Criteria

- **Success:** no code path can approve without real completed reviewer results; report contains only real data; all
  tests, lint and lint-docs green; `flow.py` net line count does not increase.
- **Discard:** cost exceeds `1.5 * 11.0 = 16.5` units, or more than 25 pre-existing assertions need changes (signal that
  the report contract is wider than assumed; re-plan the report rewrite as its own child).

### Metrics

| metric              | value |
| ------------------- | ----- |
| p_success_pred      | 0.68  |
| entropy_pred        | 7.0   |
| impact_pred         | 35.0  |
| cost_pred           | 11.0  |
| learning_value_pred | 5.0   |
| ev_pred             | 13.2  |

### Step Metrics Rationale

`EV = 0.68 * 35.0 + 0.5 * 5.0 - 0.3 * 7.0 - 11.0 = 23.8 + 2.5 - 2.1 - 11.0 = 13.2`. Lowest probability because it is the
integration point, rewrites a large report function, changes many docs and has a real routing unknown. Learning value is
the highest: it establishes how Stage 4 can run agents inside the sandbox, which the future resolver change will reuse.

---

### Strategy Rationale

The intent bundles ten goals across a new module, the container entrypoint, `flow.py`, `cli.py`, the prompt template,
about eight docs and three to five test modules. Done as one plan, the honest entropy is about `18.5`, above the `16.0`
budget, and the scope would not fit one 45-minute execute stage (`StageBudgets.execute = 2700 s`). The plan therefore
SPLITs into three children, ordered so that each one is useful and safe on its own when merged into the root branch:
Step 1 removes every fail-open path immediately, Step 2 builds and proves the ensemble logic in isolation, and Step 3
integrates it and deletes the fabrication. Rejecting or deferring was considered and dismissed: the defect posts
fabricated reviews to GitHub, so its impact is high, and every step has concrete acceptance tests.

Aggregation: entropy is the sum of step entropy, `5.5 + 6.0 + 7.0 = 18.5`. Impact is the sum, `35 + 30 + 35 = 100`. Cost
is the sum, `8.0 + 9.0 + 11.0 = 28.0`, plus `2.0` coordination overhead, giving `30.0`. Probability: the naive product
is `0.80 * 0.78 * 0.68 = 0.42`, but each child has its own bounded re-plan attempts and the steps are only loosely
coupled (Step 2 does not depend on Step 1), so the plan uses `0.58`, between the product and the bottleneck step (Step
3, `0.68`). Learning value is a qualitative judgement (`6.0`) above the maximum single step because the busy-loop root
cause and the Stage 4 sandbox route are both reusable findings. Strong assumptions: `supervise_authoring_turn` or
`run_turn` can drive a reviewer turn unchanged, and the gh write guard permits read-only `gh pr view` through injected
fakes.

## Safety & Constraint Alignment

- **Key world ruleset constraints that affect this plan:**
  - `holon-config/world/constraints.md` section 1: only humans merge to `main`; children merge into this root branch.
  - `holon-config/world/constraints.md` section 2 and `docs/safety.md` principle 2: reviewer agent turns run only in a
    sandbox, never on the host.
  - `holon-config/world/constraints.md` section 3: ledgers are append-only; only `intents.jsonl`, `plans.jsonl` and
    `executions.jsonl` are used.
  - `holon-config/world/ruleset.md` sections 2 and 3: typed and docstringed public API, pytest only, tests next to the
    module they test; test changes are declared in this plan (Steps 1 to 3).
  - `docs/safety.md` "Human review is the final gate": the review must never fabricate evidence presented to that gate.
- **Potential violations or edge cases:**
  - Running reviewer agents on the host during `holon flow` Stage 4.
  - A reviewer reading another reviewer's report through the shared workspace.
  - Scratch report files under `.holon/review/` being committed or polluting the empty-diff guard.
  - Tests that accidentally call real `gh`, git remotes, Docker or an LLM runner.
  - A failure record that cannot be written because the clone failed and no ledger exists yet.
- **Mitigations built into the plan:**
  - Stage 4 routes reviewer turns through the existing role dispatcher, with a fail-closed fallback (Step 3).
  - Reports are read and removed before the next reviewer starts, prompts carry only diff and description (Step 2).
  - Every ledger write goes through `try_append_action_record` / `record_review_action` under `LedgerLock`.
  - Injectable `run_gh`, `turn_runner`, `runner_factory` and dispatcher seams; the existing gh write guard stays on.
  - Consensus is unanimity of completed reviewers with zero findings, plus mergeable and green CI; any failure or
    missing data is FAILED or CHANGES_REQUESTED, and the CLI exits non-zero.
  - No merge and no auto-merge anywhere; a test asserts no merge command is issued.
  - No new dependencies, Australian English, no harness paths, bean ids or review ids added to any file.
- **Residual risk accepted (and why):** after this change Stage 4 stops on any finding with no automatic resolver, so
  `holon flow` will more often end without approval; this is the intended fail-closed behaviour and the resolver is a
  separate change. Reviewer quality itself is not measured here; only its honesty is enforced.
- **Allocated Entropy Budget:** 16.0
- **Predicted Plan Entropy:** 18.5
- **Budget Compliance:** The strategy exceeds budget as a single plan (`18.5 > 16.0`), so it is decomposed through the
  sub-intent list below; each child is predicted well inside the budget (`5.5`, `6.0`, `7.0`).

## Plan Description & Strategy

Make Stage 4 review real and fail closed in three sequential child intents that merge into this root intent branch.
Child 1 closes every fail-open path (target resolution, clone and checkout failure, empty or unverified diff, failed
posting) and fixes the host-side busy loop. Child 2 adds a focused `review_ensemble` module that dispatches independent
reviewer turns through the existing agent runner and turn supervision, parses a machine-readable trailer from each
reviewer's report file and decides consensus by unanimity. Child 3 wires the ensemble into `run_review_loop()` and Stage
4, replaces the fabricated consensus report with one built only from real reviewer results, and updates the CLI help and
docs. All tests use pytest through `uv run task test` from the repository root with fakes at every external seam.

## Sub-intent list

1. Harden the native Stage 4 review loop to fail closed: add a `review_target` module that resolves a PR number or PR
   URL to its head branch and head SHA through `gh pr view --json number,headRefName,headRefOid,title,body`, clone and
   verify only that branch in `entrypoint/review_loop.py`, treat any resolution, clone or checkout failure as a hard
   failure (no consensus, non-zero exit, one failure action record via `try_append_action_record` under `LedgerLock`),
   run the empty-diff and diff-unverified guard on every target regardless of `--auto-pr` and for PR-number targets,
   make a failed consensus post fail the stage, fix the `_find_git_root()` busy loop in `cli.py` and bound every wait on
   the review path with a deadline and a sleep, with pytest coverage using fakes only.
2. Add a new `sandbox_executor/review_ensemble.py` module that runs N independent reviewer agent turns (default 3)
   through `get_runner()` and the planner's turn supervision with per-reviewer scratch reports under `.holon/review/`,
   parses a JSON trailer (verdict, critical, important and nit counts, findings with file and line) from each report
   file, records failed, timed-out or unparseable reviewers as FAILED rather than votes, and decides consensus by
   unanimity of completed, finding-free reviewers plus mergeable and green CI; extend
   `holon-config/prompts/review_loop.template.md` with the reviewer turn contract and cover everything with pytest
   fakes.
3. Wire the review ensemble into `run_review_loop()` and `holon flow` Stage 4 with call-site changes only, run reviewer
   turns inside the sandbox via the existing role dispatcher (failing closed if that is impossible), rebuild the PR
   consensus report solely from real reviewer results and the real intent or PR title (removing hashed reviewer ids,
   copied votes and the hard-coded fallback title), post only real results, exit non-zero on CHANGES_REQUESTED or
   FAILED, update the `--ensemble-size` help text and the Stage 4 docs (real ensemble, reviewer count, unanimity rule,
   fail-closed behaviour, no automatic resolver), and update the pytest suite accordingly.
