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

## Reviewer failure causes

A reviewer that exits non-zero without a usable report is classified by `review_failure.py` and the stop reason names
the cause: `authentication` (missing, invalid or expired credential), `quota` (rate limit or usage limit, with the reset
time when the message gives one), `timeout` (a turn, stall or stage budget breach), `other`, or `unknown` when the
runner left no output at all. The log and the reviewer's failure reason carry a redacted excerpt (at most 600 characters
and 8 lines of stderr and the end of stdout); `sk-ant-` strings of any length and other credentials are masked before
the excerpt is bounded. Because the reason reaches the flow summary, the summary names the cause too.

A `quota` failure that names a reset time within the wait ceiling makes the ensemble wait until the reset and run that
reviewer once more. Only one such wait happens per ensemble, and the wait is credited to the stage timer. A reset that
is absent or beyond the ceiling fails closed with `quota (resets <time>)` so the operator can see when to retry.

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

## When the review is posted

The loop posts the consensus review to the pull request only on a terminal exit: a consensus approval, or the iteration
cap (the label budget) reached with findings still open, which posts the changes-requested form once. Every other exit
keeps the report local and posts nothing: a resolver error, a failed pre-push gate, a stage budget stop, the
no-convergence halt (including one exactly at the iteration cap), and a halt before the cap. The loop logs that nothing
was posted and why. Dry runs, `--skip-post`, a refused or failed review and a run with no completed reviewer post
nothing, as before. `ACT-POST` is written only when a post was actually attempted, and a failed attempt still writes its
failure row and ends the loop as `post_failed`.

## Test evidence and its source

The CI gate reads one test result and names where it came from, in the review evidence and on the posted `Checks` line:
`stage_3` (the executor's own measurement in its summary row), `ci` (GitHub checks) or `review_local` (the review loop's
own run). When no CI checks register and Stage 3 recorded no rate, the loop's `local_tests` fallback runs
`uv run task test` in the review container after the CI-registration wait, once per head commit (the result is cached on
the head SHA and re-measured only after the resolver pushes a fix). A dry run never runs it. `null` still means "not
measured" and withholds consensus: a run that could not execute records its `run_error`, and a measured result, passing
or failing, is never reported as unmeasured. A rate below `1.0`, or a non-zero exit code of a completed run, is a failed
test result and raises `FIND-CI`.

## Approval covers one head

An approved review names the head it covers and the measured CI result at that commit, on one line, for example "Covers
head `abc1234`; CI at that commit: 11 of 11 checks passed." It never says "all CI checks were green" without counts.
When Stage 5 later finds CI red on a different head, one follow-up comment says the approval no longer stands (see
[Final-head CI check](git_flow.md#final-head-ci-check-stage-5)). A later green head needs its own approval, which names
its own head.

## Resolver pre-push verification

Before the resolver commits a fix it runs, in order: `uv run task lint`, a Prettier check of the Markdown files it
changed, and `uv run task test`. The Markdown check is the one CI's `lint-md` runs (`npx prettier --check`), limited to
changed files and using the target repository's own Prettier configuration. When it fails, the resolver runs
`npx prettier --write` on just those files, re-checks, and the formatting lands in the same fix commit. A file that
still fails is named in the halt message (`Resolver pre-push markdown formatting failed: ...`). A change with no
Markdown starts no Prettier process, and unrelated Markdown files are never touched. The review-loop image bakes in
Prettier; a missing `npx` is a failure that names it, never a silent skip. `lint-json`, `lint-yaml` and `lint-shell`
need tools the image lacks and stay CI-only, which `tests/test_resolver_lint_parity.py` records.
