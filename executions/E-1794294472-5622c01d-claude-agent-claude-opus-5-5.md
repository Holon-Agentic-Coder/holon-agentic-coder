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
