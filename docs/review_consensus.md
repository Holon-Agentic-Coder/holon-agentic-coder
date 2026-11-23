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
`--approve` only under the same rule. Consensus approval completes Stage 4 with `StageStatus.APPROVED`
(`halted_for_human=False`), writes `status: "approved"` on the `REV-...` summary row, logs `[REVIEW APPROVED]` in the
CLI summary and proceeds to Stage 5 calibration without merging to `main`. `StageStatus.HALTED_FOR_HUMAN` and
`[SAFETY HALT]` apply only to safety halts and dry-run boundaries (legacy `halted_for_human` checkpoints and summary
rows remain recognised as converged on read). Non-approved runs write `status: "failed"`; end-of-flow messages (the
`REV-...` summary row, the action event, the Stage 4 error, the Stage 4 outcome log and the `holon review-loop` summary)
state each reviewer's final verdict as `Reviewer N VERDICT (critical/important/nit)` and say
`not approved: reviewer N final verdict X` for any reviewer that did not approve cleanly; such a run never approves and
is never described as ready to merge.

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
`conflicting with a higher-severity finding` or `no-convergence` (set by the loop's convergence guard itself, never
through the evidence rule). A resolver entry with no recognised reason is read as `unspecified`, which is only a marker:
the rejection is refused and the finding stays `open`, so no `rejected` row carries an empty or `unspecified` reason
(historical rows may; see the void correction rows in [ledger_schema.md](ledger_schema.md)).

Evidence rule for resolver rejections (`sandbox_executor/resolver_evidence.py`): a rejection of an `important` or
`critical` finding stands only when its reason is not `out of scope` or `unspecified` and the resolver supplies an
`evidence` value naming the code it checked, a `path:line` token or a test (`tests/...::test_name` or a `test_*.py`
file). Otherwise the finding's in-memory disposition is `open`, its rejection reason is null and the resolver's text is
kept as evidence for audit (`unevidenced rejection (<reason>): <evidence>`, carried in the row's `error_context`). At
every severity, a rejection without a stated reason is refused (`refused rejection (no reason)`) and a finding the
resolver did not adjudicate stays `open`; `DispositionRecorder` refuses to write a reasonless rejection as a second
guard. The reviewed finding's severity is authoritative, so a resolver cannot downgrade a finding to escape the rule.
Only rejected nits are remembered as already adjudicated; see [Consensus integrity](#consensus-integrity).

## Failed resolver passes and carried findings

A finding leaves the review loop only through a disposition supplied by a resolver that ran: `applied`, or `rejected`
with a stated reason. A resolver pass that fails (a non-zero exit, a usage or session limit, a timeout, no usable
output, or an exception) carries no dispositions at all: the container entrypoint checks the turn's exit code before
reading any dispositions file it left, and treats a clean exit without one as "no usable output". The loop then halts
and writes no `ACT-RES` adjudication row for the pass (the tests-failed rejected-attempt row is unchanged). The loop
result returns the findings still open as `open_findings`, on every exit except a dry run, which decides nothing and
leaves the carried findings as they were.

The engine stores `open_findings` in `FlowContext.carried_review_findings`, which the checkpoint saved after Stage 4
persists. A `--from-stage review` resume sends those carried findings to the resolver before any new reviewer iteration
(labelled with the halted pass's iteration); if that pass fails again, no iteration starts, and a rejected attempt is
recorded as on any failed pass. A carried fix applied without a push is recorded with the loop's pending dispositions
and does not block approval. Later resolver passes receive the carried findings still open merged with the fresh ones,
de-duplicated by signature. While any carried finding has no disposition, an `APPROVED` ensemble verdict is turned into
`CHANGES_REQUESTED`, the consensus blockers and the summary's halt reasons name the findings, and nothing is posted. The
logic lives in `flow/review/carried_findings.py`.

## When the review is posted

The loop posts the consensus review to the pull request only on a terminal exit: a consensus approval, or the iteration
cap (the label budget) reached with findings still open, which posts the changes-requested form once. Every other exit
keeps the report local and posts nothing: a resolver error, a failed pre-push gate, a stage budget stop, the
no-convergence halt (including one exactly at the iteration cap), and a halt before the cap. An iteration that halts on
a FAILED verdict (a crashed, timed-out or stalled reviewer turn), a stage budget stop, an unresolved ref or unmeasured
tests also keeps the report local when it lands on the last label, so a run resumed from its checkpoint posts only its
terminal review. The loop logs that nothing was posted and why, and the `REV-...` summary row records the reason as
`review_post_withheld`. Dry runs, `--skip-post`, a refused or failed review and a run with no completed reviewer post
nothing, as before. `ACT-POST` is written only when a post was actually attempted, and a failed attempt still writes its
failure row and ends the loop as `post_failed`.

## Test evidence and its source

The CI gate (`flow/review/head_test_evidence.py` and `flow/review/local_tests.py`) evaluates test evidence for the
current git `HEAD` commit (after waiting for pending GitHub CI checks to settle) and records `test_source`,
`test_head_sha` and `test_counts` in the CI evidence and on the posted `Checks` line, selecting the first applicable
source in strict precedence order:

1. `resolver_evidence`: a `FIND-CI` finding rejected by the resolver with passing code or test evidence on the current
   head, confirmed by a local `uv run task test` measurement on that head (preserved across loop-authored ledger-only
   commits).
2. `ci_head`: green GitHub CI checks (`source == "github_cli"`, `all_passed == True`, `pending == 0`) verified against
   the PR's `headRefOid` matching the current `HEAD`.
3. `stage_3`: Stage 3's recorded test result (passing or failing) when `stage_3_head` matches the current `HEAD` (a
   Stage 3 failure on that head raises `FIND-CI` unless `resolver_evidence` or `ci_head` supersedes it).
4. `review_local`: a local `uv run task test` re-run in the review container, cached per head commit and re-run only
   when `HEAD` advances (used when Stage 3 recorded no measurement, `stage_3_head` is missing, or `HEAD` moved past
   `stage_3_head`).

A dry run never executes local tests and attributes the Stage 3 or GitHub CI probe (`ci` or `stage_3`). `null` still
means "not measured" and withholds consensus: a run that could not execute records its `run_error`, and a measured
result, passing or failing, is never reported as unmeasured. A rate below `1.0`, or a non-zero exit code of a completed
run, is a failed test result and raises `FIND-CI`.

## Approval covers one head

An approved review names the head it covers and the measured CI result at that commit, on one line, for example "Covers
head `abc1234`; CI at that commit: 11 of 11 checks passed." It never says "all CI checks were green" without counts.
When Stage 5 later finds CI red on a different head, one follow-up comment says the approval no longer stands (see
[Final-head CI check](git_flow.md#final-head-ci-check-stage-5)). A later green head needs its own approval, which names
its own head.

## CI gate on the merged head

Stage 5 pushes no calibration commit until CI has passed on the head it would calibrate. After the conflict gate, it
compares the execution head with the approved head. If the head is unchanged, or moved only by non-merge commits that
touch `plans/`, `executions/` or `holon-knowledge/` (the ledger rows Stage 4 commits on exit), Stage 5 relies on the
Stage 4 CI result and runs immediately. Any other head, for example one with `origin/main` merged in, waits for every
check run on exactly that SHA, read from the GitHub check-runs API for the commit.

- Only `success`, `neutral` and `skipped` pass. Runs registered against another SHA (superseded) are ignored, and the
  newest run per check suite and check name counts.
- A `cancelled` or `stale` run is never treated as passed. It is re-triggered once per run id; a second cancellation, or
  a cancelled check with no Actions run, fails Stage 5 closed.
- Checks still pending when the CI wait budget runs out, or no readable checks, also fail Stage 5 closed. Nothing is
  pushed and the approval is not withdrawn, because no check failed.
- A failed check withdraws the consensus. A new ledger row (labels `consensus-withdrawn` and `merged-head-ci`) records
  it, and no earlier row is edited. One PR comment per merged head says the approval was withdrawn. The pipeline then
  restarts Stage 4 at iteration 1, with a fresh reviewer ensemble, for the new head. It does this at most 3 times; after
  that, Stage 5 fails and nothing is calibrated.

The Stage 5 log names the head SHA, the run ids and the conclusions it relied on. Dry runs and `--skip-push` skip the
gate.

## Approval carry-over across target merges

An approval is bound to the PR's own change as well as to the SHA the reviewers saw. Stage 4 records a change
fingerprint with every approval, so a merge of the target branch that only resolved ledger rows does not send the PR
back through a full review. The fingerprint never stands in for CI.

The fingerprint (`flow/review/approval_fingerprint.py`) covers the diff from `merge-base(head, origin/<target>)` to the
head. It excludes `holon-knowledge/**` (the ledgers and the knowledge base) and the Stage 5 calibration reports
(`plans/**/*_calibration.md`). It hashes `git patch-id --stable` of that diff together with the sorted status, mode,
blob and path of each changed file, so mode-only and binary changes count. It is written as `pf1:` and 40 hex
characters; the `pf1` prefix keeps a later reduction from ever matching an old fingerprint. An empty PR-own change or
unreadable git gives no fingerprint, and a missing fingerprint never matches.

The post-time conflict gate keeps the approval when:

- the head conflicted with the target branch and the gate merged it cleanly, or with ledger-only conflicts resolved
  mechanically; and
- the merged head's fingerprint equals the fingerprint recorded with the approval.

It keeps the gate's block, and the approval must be re-earned, when:

- a code conflict aborts the merge (a knowledge-base file conflict also aborts: only ledger conflicts are
  auto-resolved);
- a push after the approval changed a PR-own file, so the fingerprint differs;
- the target branch edited a file the PR also changes, even in lines the PR did not touch: the hunk context or the file
  blob differs, which costs a re-review but never misses one;
- the approval has no fingerprint (a checkpoint from before this change).

Each carry-over is logged with both SHAs and the fingerprint, and appends exactly one ledger row (`ACT-CARRY`, labels
`review`, `conflict-gate` and `approval-carry-over`, fields `approved_head`, `merged_head` and `approval_fingerprint`).
No earlier row is edited, and a re-run on the merged head probes clean and writes no second row. Stage 5's own conflict
gate records the same row when its merge keeps the change.

The approval stamp names the head, the fingerprint and the CI result at that head, for example "Covers head `def5678`;
change fingerprint `pf1:0123456789ab`; carried over from `abc1234` (same change); CI at that commit: not measured." A
carried approval never shows the earlier head's green CI: its CI result is "not measured" until Stage 5 measures it.

The consensus keeps `target_commit` as the SHA the reviewers saw and adds `carried_from` and `carried_to`, so the CI
gate on the merged head still sees a moved head. It waits for every check on the new SHA, and withdraws the consensus
and restarts Stage 4 when one fails, exactly as for any merged head. In Stage 5 a head whose fingerprint differs from
the approved one is not covered: the consensus is withdrawn, with the reason "PR-own change changed since approval" in
the ledger row and the PR comment, and Stage 4 reviews the change again, within the same limit of 3 restarts. The
automatic timing-gate rerun of the final-head CI check stays limited to bookkeeping-only moves; a merged target branch
is not one.

## Resolver pre-push verification

Before the resolver commits a fix it runs, in order: `uv run task lint`, a Prettier check of the Markdown files it
changed, and `uv run task test`. The Markdown check is the one CI's `lint-md` runs (`npx prettier --check`), limited to
changed files and using the target repository's own Prettier configuration. When it fails, the resolver runs
`npx prettier --write` on just those files, re-checks, and the formatting lands in the same fix commit. A file that
still fails is named in the halt message (`Resolver pre-push markdown formatting failed: ...`). A change with no
Markdown starts no Prettier process, and unrelated Markdown files are never touched. The review-loop image bakes in
Prettier; a missing `npx` is a failure that names it, never a silent skip. `lint-json`, `lint-yaml` and `lint-shell`
need tools the image lacks and stay CI-only, which `tests/test_resolver_lint_parity.py` records.
