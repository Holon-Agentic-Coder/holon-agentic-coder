# review_consensus.md

This document defines how the Stage 4 review loop decides consensus and when a resolver rejection counts. The review
loop itself is described in [git_flow.md](git_flow.md); the ledger fields it writes are in
[ledger_schema.md](ledger_schema.md).

---

## Consensus integrity

The loop decides consensus only from what the reviewers returned (`flow/review/verdict.py`): each reviewer's own verdict
and counts go to `decide_consensus`, and three cross-checks can only withhold an approval, never grant one. An activated
role at `CHANGES` in any reviewer's role table (an unreadable table counts as an objection), a container ensemble
verdict other than `APPROVED` on the host dispatch path (a missing one adds no check), and a reviewer whose findings
adjudication suppressed entirely (its pre-filter verdict is logged beside the `APPROVED` it would read after filtering,
with the suppressed finding ids) each block consensus and are logged as `Consensus blocked at iteration N: ...`.
Adjudication suppression (`flow/review/adjudication.py`) applies to nit findings only: a nit is withheld from the
resolver when a nit with the same severity, path, line and summary was rejected earlier, unless the raising reviewer's
Security role is at `CHANGES`. Critical and important findings are never suppressed, whatever an earlier resolver row
says; a finding raised again at a higher severity is a new finding; and finding ids, which are positional
(`R{reviewer}-F{n}`) and repeat across iterations, are never matched. Suppression never rewrites a reviewer's verdict,
counts or role table; the earlier filter did, which let a critical re-raised as `R3-F1` be dropped and its reviewer
posted as approved. Only rejected nits are listed to later reviewers as already adjudicated. A resolver rejection of an
important or critical finding counts only with a reason other than `out of scope` and an `evidence` value naming a
`path:line` or a test; otherwise the finding stays `open` and goes back to the resolver when it is raised again. The
posted review is headed `APPROVED` only for an `APPROVED` ensemble verdict with every reviewer approved with zero open
findings and no role at `CHANGES`; its fraction counts only those clean approvals, and the PR review is submitted with
`--approve` only under the same rule. End-of-flow messages (the `REV-...` summary row, the action event, the Stage 4
error, the `[SAFETY HALT]` line and the `holon review-loop` summary) state each reviewer's final verdict as
`Reviewer N VERDICT (critical/important/nit)` and say `not approved: reviewer N final verdict X` for any reviewer that
did not approve cleanly; such a run never halts as approved and is never described as ready to merge.

When every reviewer approved cleanly but one of these checks withheld consensus, the Stage 4 error names the blocker
(`Autonomous review blocked: consensus withheld by ...`) rather than a test failure.

## Resolver evidence rule

Ledger fields (see [ledger_schema.md](ledger_schema.md)): an `ACT-RES` row with a null `review_disposition` (action text
`left open (no evidenced adjudication)`, outcome `failure`) records a finding still open when the review loop ended; no
new disposition value is written, so `executions.jsonl` keeps its vocabulary and Holon still keeps exactly three
append-only ledgers. The `rejection_reason` vocabulary is `not diff-grounded`, `inaccurate`, `out of scope`,
`conflicting with a higher-severity finding`, `no-convergence` (set by the loop's convergence guard) or `unspecified`
(the resolver gave no recognised reason; it is never silently recorded as `out of scope`).

Evidence rule for resolver rejections (`sandbox_executor/resolver_evidence.py`): a rejection of an `important` or
`critical` finding stands only when its reason is not `out of scope` or `unspecified` and the resolver supplies an
`evidence` value naming the code it checked, a `path:line` token or a test (`tests/...::test_name` or a `test_*.py`
file). Otherwise the finding's in-memory disposition is `open`, its rejection reason is null and the resolver's text is
kept as evidence for audit (`unevidenced rejection (<reason>): <evidence>`, carried in the row's `error_context`). A
finding the resolver did not adjudicate is `open` when important or critical and `rejected` (`unspecified`) when a nit.
The reviewed finding's severity is authoritative, so a resolver cannot downgrade a finding to escape the rule. Only
rejected nits are remembered as already adjudicated; see [Consensus integrity](#consensus-integrity).
