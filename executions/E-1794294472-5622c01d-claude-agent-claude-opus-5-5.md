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
