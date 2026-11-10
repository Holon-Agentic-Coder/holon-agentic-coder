# Execution Record: E-1794294472-5622c01d-claude-agent-claude-opus-5-5

## Summary

In progress. Implements plan `P-1794294214-0a2285cf-claude-agent-claude-opus-5-5` for intent
`I-1794294198-review-consensus-never-approves-past-an-open-critical`: the Stage 4 review loop must decide consensus only
from what the reviewers returned, and the posted review must report it exactly.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0     |
| entropy_actual        | 0     |
| impact_actual         | 0     |
| cost_actual           | 0     |
| learning_value_actual | 0     |

## Steps Executed

### Step 1: Reproduce the false consensus and pin the flip point

- Added `apps/sandbox-executor/tests/flow/review/test_consensus_integrity.py`: three reviewers (APPROVED, APPROVED,
  CHANGES_REQUESTED) through the injected `run_ensemble_fn` and `run_resolver_fn` seams; the fake resolver rejects every
  finding with bare `out of scope` and pushes nothing; iteration 2's reviewer 3 returns one critical finding with a
  detail block whose Security role is `CHANGES`.
- Red run on unmodified code: `uv run pytest apps/sandbox-executor/tests/flow/review/test_consensus_integrity.py` gave
  3 failed in `0.14s` (scenario A id path; scenario B signature path at the same severity and at a higher severity).
  Every failure had `consensus.approved` True and `halted_for_human` True, and the resolver was called for iteration 1
  only.
- Direct probe of `_filter_adjudicated_findings` (`flow/review/resolver.py`, the
  `verdict = ReviewerVerdict.APPROVED if not kept_findings else result.verdict` line, then `counts` recomputed from the
  kept findings) on the iteration-2 reviewer 3:
  - id path (`rejected_ids = {"R3-F1"}`, empty signature history): verdict `APPROVED`, counts `0/0/0`, 0 findings, while
    the detail roles still read `Security CHANGES`. This is exactly the posted-review symptom.
  - signature path (same severity, path, line and text): verdict `APPROVED`, counts `0/0/0`.
- Contributing defects confirmed: `deserialize_finding_dispositions` turns a disposition with no reason into
  `rejected: out of scope`, and a finding with no disposition into `rejected: out of scope`
  (`_disposition_from_mapping` default and `_normalise_rejection_reason` fallback). `loop.py` discards the container's
  `ensemble_verdict` as `_container_verdict`. `deserialize_reviewer_result` and `_attach_transported_detail` keep the
  transported verdict unchanged, so the filter is the only flip point.
- Confirmed cause: `_filter_adjudicated_findings` rewrites a reviewer's verdict to APPROVED and its counts to zero once
  every finding is dropped, and the loop decides consensus on that rewritten list; the id path matches positional ids
  (`R{index}-F{n}`) that collide across iterations at any severity.

### Step 2: Nits-only adjudication suppression in `flow/review/adjudication.py`

- New module `flow/review/adjudication.py`; `_filter_adjudicated_findings` and `_augment_pr_text_with_rejected` moved
  out of `flow/review/resolver.py` (362 to 289 lines) and the flow package re-export now names the new module.
- `_filter_adjudicated_findings(reviewers, rejected_history)` returns an `Adjudication` (actionable and suppressed merged
  finding rows). It never returns rewritten `ReviewerResult`s, so verdicts, counts and role tables stay as returned. The
  cause is described in its docstring at the old flip point.
- `is_suppressible`: only a `nit` whose full signature (severity, path, line, summary) was rejected at `nit`, and only
  when no raising reviewer's Security role is at `CHANGES`. The id-only match (`rejected_ids`) is gone. Findings carry no
  per-finding role attribution in the detail block, so the per-reviewer Security rule is used (the plan's stricter
  fallback was not needed).
- `record_nit_rejections` keeps only rejected nits in `rejected_history`, so the adjudicated list shown to reviewers in
  the PR text and handed to the resolver (`ResolverInput.rejected_findings`) holds nits only;
  `_augment_pr_text_with_rejected` filters to nits as well.

### Step 3: Verdict from the returned reviewers in `flow/review/verdict.py`

- `consensus_blockers(reviewers, container_verdict, adjudication)`: every role at `CHANGES` (an unreadable role table
  counts as an objection), every reviewer whose findings were all suppressed (pre-filter verdict vs the APPROVED it
  would read after adjudication, with the suppressed finding ids), and a container ensemble verdict other than APPROVED.
  A missing container verdict adds no blocker and never approves.
- `decide_returned_consensus(reviewers, ensemble_size, mergeable, ci_green, blockers)` replaces all three
  `decide_consensus` call sites in `loop.py`; any blocker downgrades APPROVED to CHANGES_REQUESTED.
- `loop.py` keeps the container verdict (was `_container_verdict`), logs each blocker
  (`Consensus blocked at iteration N: ...`), treats a blocker as an objection for the CI fast path, feeds only
  `adjudication.actionable` to the resolver, and names the blockers in the "no resolver pass applicable" halt.
- The three local-tests CI-status blocks became `apply_local_test_evidence` in a new `flow/review/ci_evidence.py`, so
  `loop.py` shrank from 960 to 929 lines with no budget raise and no new `noqa`.
- Tests added to `test_consensus_integrity.py` (16 in total, all passing): suppression rules, verdict and counts
  unchanged by the filter, role CHANGES blocks a clean ensemble, a PASS role table still approves, a container
  CHANGES_REQUESTED blocks a clean host view, a suppressed-nit mismatch is logged with `R1-F1`, and the final-verdict
  renderer. No existing review test needed a change; only `test_move_proof.py` and `test_module_budgets.py` fail until
  Step 8 records the new definitions and modules.
- Residual risk recorded: the container's ensemble verdict also folds in its own CI and mergeability view, so a
  container that sees CI as unmeasured blocks a host approval. That fails closed (human decides) by design.

### Step 4: The posted review reports the returned verdicts exactly

- `review_report.py`: `_approvals` counts only reviewers that completed with `APPROVED` and zero counts and findings
  (`_clean_approval`). New public `report_approves(consensus_data, reviewers)` requires an APPROVED ensemble verdict,
  at least one reviewer, every reviewer clean and no merged role at `CHANGES`; `_heading` and the body ordering use it,
  so the renderer cannot be made to head an inconsistent review APPROVED. Heading text is otherwise byte-identical.
- `flow/github/review_post.py`: the `--approve` flag now follows `report_approves` instead of the ensemble verdict plus
  any completed reviewer (216 lines, unchanged).
- `render_reviewer_line` already prints each reviewer's verdict and counts from the result it is given; with Step 3 that
  is the returned result, asserted by the new failing-shape test.
- Tests: `test_review_report.py` gains the failing-shape render (heading `CHANGES REQUESTED ... — 2/3 approved`, reviewer
  3 `CHANGES_REQUESTED` with critical 1, `| Security | CHANGES |`), a role-only objection, and an APPROVED reviewer with
  a nit not counted as a clean approval. `test_an_approved_report_still_lists_any_open_finding` asserted the old
  `APPROVED (1/3)` heading with an open finding, which is the defect, so it was rewritten as
  `test_an_approved_verdict_with_an_open_finding_renders_as_changes_requested`. A post-path test in
  `test_consensus_integrity.py` checks `--comment` (never `--approve`) for the failing shape.

### Step 5: Resolver rejections of important or critical findings need evidence

- New package module `resolver_evidence.py`: `settle_rejection(severity, reason, evidence)` keeps a rejection of an
  `important` or `critical` finding only with a reason other than `out of scope` or `unspecified` and an `evidence`
  string naming a `path:line` or a test (`tests/...::test_name` or a `test_*.py` file); otherwise the finding is `open`
  with a null reason and the resolver's text kept as `evidence` (`unevidenced rejection (<reason>): <evidence>`).
  `default_disposition` makes an unadjudicated important or critical finding `open` and a nit `rejected`.
- `review_report.py`: `FindingDisposition.evidence` (serialised when set), `DISPOSITIONS` gains `open`,
  `_normalise_rejection_reason` returns `unspecified` instead of manufacturing `out of scope`, and
  `_disposition_from_mapping` applies the rule. Hardening found while writing the tests: the reviewed finding row's
  severity is now authoritative, so a resolver cannot report a critical as a nit to escape the rule.
- Deviation (plan's discard path): `ledger.py` validates `review_disposition` against `{"applied", "rejected"}` and is
  outside the intent's scope list, so no `open` ledger value is written. An `open` disposition stays in memory, is never
  added to `rejected_history`, and the finding goes back to the resolver when reviewers raise it again; only the
  end-of-loop fallback records a still-open finding, as an `ACT-RES` row with `review_disposition` null, outcome
  `failure`, action text `left open (no evidenced adjudication)` and the audit text in `error_context`. Recording open
  rows mid-loop was avoided because the recorder dedupes by signature and would then drop the later real disposition.
- The container resolver prompt (`entrypoint/review_loop.py` `_build_resolver_prompt`) now asks for an `evidence` key for
  important and critical rejections, says `out of scope` alone is not accepted for them, and offers `open`.
- Consumers checked: `ledger.py` (validation, trailer parsing: unaffected because `open` never reaches a row),
  `render_dispositions_section` (shows evidence for an open row), `entrypoint/review_loop.py` commit path (only acts on
  `applied`), `flow/review/loop.py` (`rejected_now` and `applied_now` filters ignore `open`).
- Tests: new `tests/test_resolver_evidence.py` (19 passing), including a loop test where a bare `out of scope` rejection
  of an important finding goes back to the resolver in iteration 2 and ends as a null-disposition `left open` row.

### Step 6: End-of-flow messages state the final verdicts

- `flow/review/verdict.py`: `render_final_verdicts` (`Reviewer 1 APPROVED (0/0/0); ...` in critical/important/nit order,
  plus each role at `CHANGES`) and `not_approved_verdicts` (`reviewer N final verdict X`). `review_loop_approved` and
  `review_summary_lines` moved here from `flow/review/cli.py` (106 to 87 lines) so the shared CLI and entrypoint helpers
  could gain the guard without raising the `cli.py` budget; `review_loop_approved` now also requires every final verdict
  to be a clean APPROVED, which gates both `[SAFETY HALT]` prints and both exit codes, and the summary gains a
  `Not approved   : ...` line (before the Roles line, which existing tests read as the last line).
- `loop.py`: the summary ledger row and the action event `error_context` end with `final verdicts: ...`, and a
  `not approved: ...` halt reason is added whenever a final verdict is not a clean APPROVED.
- `flow/stages/review.py`: the dirty, missing-ref and non-consensus failures share one `StageResult` block (295 lines,
  down from 322; message text of the dirty and missing-ref paths unchanged); a consensus whose final verdicts are not
  all clean APPROVED fails instead of halting as approved; non-consensus messages end with
  `Final verdicts: ...; not approved: ...`; the `[SAFETY HALT]` reason names the final verdicts.
- `entrypoint/review_loop.py` console summary needed no further change: it already uses `review_summary_lines` and
  `review_loop_approved` from the flow package (an entrypoint-to-flow import, so the host flow engine gains no entrypoint
  import).
- Other summary paths checked: `flow/stages/children.py` merge readiness already requires a Stage 4
  `HALTED_FOR_HUMAN` with an approved consensus, which the Stage 4 guard now withholds; `flow/pipeline.py` only logs the
  halt; `cli/commands/flow.py` (outside scope) prints `[SAFETY HALT]` only for `halted_for_human`, which is now False for
  any non-approved final verdict, and otherwise prints the Stage 4 error that carries the final verdicts.
- Tests (21 in `test_consensus_integrity.py` now): Stage 4 with a non-approved final ensemble, both honestly reported
  and with an approved consensus claimed by a simulated regression, fails with every verdict and `not approved` in the
  message and never `ready for merge`, `consensus approval` or `[SAFETY HALT]`; summary lines and exit status; the
  summary ledger row.
