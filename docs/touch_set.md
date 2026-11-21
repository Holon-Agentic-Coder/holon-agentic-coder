# Planner touch set

The planner declares the files a plan expects to change in a `## Touch Set` section of the plan file. Stage 2 records
the parsed set on the plan row, Stage 3 admits the plan only when that set overlaps no flow in flight, Stage 3 then
enforces it on the execution diff, and Stage 5 calibration scores it. The parsing, matching and scoring rules live in
`sandbox_executor/touch_set.py` and the admission overlap rule in `sandbox_executor/touch_set_overlap.py`; discovery in
`flow/in_flight.py` (other worktrees' checkpoints read by `flow/worktree_checkpoints.py`), the base-branch file listing
in `flow/base_files.py`, the admission decision in `flow/admission_decision.py` (the oldest-waiter rule in
`flow/admission_order.py`), the lock and the recorded state in `flow/admission_lock.py` and `flow/admission_state.py`,
the Stage 3 hook in `flow/admission.py` and enforcement in `flow/stages/touch_set_guard.py`.

## Plan row `touch_set`

`parse_touch_set_section` parses the section of the committed plan file. The field is a list of objects, each with
`path` (a repository-relative POSIX path, a directory ending in `/`, or a glob where `*` stays within one path segment
and `**` spans directories) and, only when the plan named some, `symbols` (stored and reported, never scored). Metadata
paths (`plans/`, `executions/`, `holon-knowledge/`, `.holon/`) are never listed. Placeholder items such as `- none` are
skipped (a section holding only placeholders records `[]`), and a plan without the section records `null`; neither fails
the plan. A folder or wildcard entry prints one advisory warning naming it (at Stage 3 it matches every existing file
below it, so module paths are preferred); the entry is still recorded and the plan never fails. Example:

```json
{
  "touch_set": [
    { "path": "apps/sandbox-executor/src/sandbox_executor/touch_set.py" },
    { "path": "apps/sandbox-executor/src/sandbox_executor/calibration.py", "symbols": ["generate_calibration"] },
    { "path": "docs/" }
  ]
}
```

## `ACT-CAL` `touch_set_accuracy`

New `ACT-CAL` rows append `touch_set_accuracy` after `created_at`, the same object as the calibration report's
`touch_set`:

- `precision`: declared entries that matched at least one changed file, over declared entries.
- `recall`: changed non-metadata files covered by some entry, over changed non-metadata files.
- `unpredicted_paths` and `unused_entries`: the changed files outside the touch set and the declared entries the diff
  did not touch.

`precision` or `recall` is `null` when its denominator is zero or the plan row has no touch set (the report then shows
`n/a`).

## Stage 3 admission

Before Stage 3 snapshots branches or dispatches the executor (after the plan budget and sub-intent depth checks), the
flow reads the active plan row's `touch_set` and compares it with every flow in flight:

- **In flight:** an open pull request whose head is a flow execution branch (`I-.../P-.../E-.../_`, one
  `gh pr list --state open` call; more than 50 open flow pull requests outside this lineage, or more than 1000 open pull
  requests in all, make discovery unavailable), and a checkpoint (`.holon/flow/checkpoint-*.json`) in another git
  worktree of the repository whose current stage is execute or review, whose plan branch still exists and whose
  admission state is not `blocked` (see [Admission state](#admission-state)). Flows of the same root intent (this run's
  own lineage) are excluded, and a local flow that also has a pull request is listed once, so a flow with an open pull
  request stays in flight whatever its checkpoint says. Discovery runs host-side and only reads refs and checkpoint
  files; it never fetches, checks out or writes another flow's branch.
- **Compared:** on concrete files, against the files of the base branch (the intent's `target_branch`, `main` by
  default, origin tracking ref first, listed once per admission with `git ls-tree -r --name-only`, metadata dropped). An
  open pull request is compared by its real changed files only (the paths of its `gh pr diff <n>` patch, a rename's
  source and a deleted file included), never by its planned touch set; a local flow, and an older waiter, by its
  declared touch set. A literal entry names itself, whether it exists or not; a folder or glob entry names only the base
  files it matches. So a changed file that exists on the base overlaps when one of this plan's entries matches it, a new
  file (absent from the base) overlaps only an identical literal path in the other set, and folder or glob entries on
  both sides meet only on existing files. A glob written for new files (`tests/test_new_*.py`) therefore overlaps
  nothing. A path with only a bracket class, such as `app/[id]/page.tsx`, reads as a literal and also as a class against
  the base. Metadata never conflicts. A base listing that fails (an unresolved branch or a `git` failure) refuses
  admission (fail closed); with no flow in flight and no waiter the base is not listed at all.
- **Refusal:** Stage 3 fails before any dispatch with a message naming each pull request and branch (or local branch and
  worktree) and the concrete overlapping files (never the folder or glob pattern), for example
  `Stage 3 refused: the touch set overlaps pull request #7 (branch I-.../E-.../_) on src/app.py; finish or merge that flow first, or rerun with --allow-overlap.`
  At most 20 files are named per flow, followed by `and N more`. The payload carries `admission_refused: true`,
  `dispatched: false` and `conflicts` (each with its full file list). A plan row without a touch set, and a `gh` or
  `git` failure during discovery or the base listing, are refused too (fail closed), with the reason in
  `admission_reason`. A dry run only logs the decision. Refusal never closes, merges or edits the other flow.

A checkpoint left at execute or review by an abandoned admitted run (or one older than the admission state) still counts
as in flight while its plan branch exists; delete the checkpoint (or the branch) or use the override. A blocked
checkpoint never counts as in flight. Local flows are compared by their declared sets only, so a local flow without a
pull request whose plan row records no touch set never conflicts, and the new files of a local flow are compared only
when one set declares them literally (a known gap: its files are not known until it opens a pull request). A resumed
Stage 3 (`--from-stage execute` or a `--checkpoint` resume) is admitted again like a fresh one, so a flow that started
since can refuse it, and each admitted resume under `--allow-overlap` appends another override row.

## Admission state

Outside a dry run, admission holds an exclusive lock on `<git-common-dir>/holon-admission.lock` (the git directory every
worktree of the repository shares, from `git rev-parse --git-common-dir`) from discovery until it has written its
outcome into this flow's checkpoint, so two overlapping flows on the same host are never admitted together. The wait is
bounded (120 seconds); a timeout refuses fail-closed with the reason, and the kernel releases the lock of a process that
dies. Flows on different hosts are not serialised; once one opens a pull request, the other's admission sees it.

The outcome is the top-level `admission` object of the checkpoint JSON (`.holon/flow/checkpoint-<slug>.json`, also kept
under `context.admission`). It is the machine-readable interface for operators: a waiting runner reads
`admission.blocked_by` to know exactly which pull requests or flows to watch before it re-runs the flow.

- **`blocked`:** a refusal for a touch-set overlap records
  `{"state": "blocked", "blocked_since": "<UTC timestamp>", "blocked_by": [...]}`. `blocked_since` is kept from the
  first refusal across retries; `blocked_by` is replaced on each refusal. Each entry names its kind, its reference and
  the overlapping files: `{"kind": "pull_request", "number", "branch", "files"}` for an open pull request (`branch` is
  its head) or `{"kind": "local_flow", "plan_branch", "worktree", "files"}` for a flow in another worktree. A refusal
  without an overlap (no touch set, discovery or lock unavailable) records nothing.
- **`admitted`:** an admission records `{"state": "admitted"}`; an `--allow-overlap` admission records
  `{"state": "admitted", "override": true, "override_conflicts": [...]}` (the `{flow, files}` conflicts it overrode, as
  in the `admission_override` row). If that row cannot be written the run is refused and the previous state is put back.
  An admission that follows a refusal also carries `waited_since` (the refusal's `blocked_since`, kept across resumes)
  and, once Stage 3 has merged the target branch into the plan branch, `refreshed_to` (the target commit merged in) and
  `refreshed_branch` (the plan branch it was merged into):
  `{"state": "admitted", "waited_since": "<UTC timestamp>", "refreshed_to": "<sha>", "refreshed_branch": "<plan>"}`. A
  refresh is due while the current plan branch is not `refreshed_branch` ([plan_refresh.md](plan_refresh.md)). An
  admission on the first check records neither field.
- **No field:** a checkpoint written before the admission state existed loads with no `admission` object and keeps the
  older rule (at execute or review it counts as in flight). So does a flow never admitted whose refusal recorded nothing
  (no overlap) or whose failed override write restored no field: until it re-runs admission or leaves execute, other
  flows' discovery counts it as in flight, which can only refuse more (fail closed), never admit an overlap.

A blocked flow is not in flight: it blocks no other flow, so two refused flows that overlap no longer list each other
forever. Every `blocked_by` entry therefore names an open pull request, an admitted (or older) local flow, or an older
waiting flow, never a newer waiter, and no cycle can form.

**Oldest waiter first.** When the lock holder finds no overlap, it still defers to another blocked flow whose declared
touch set overlaps its own on concrete files (the same base-file rule), whose `(blocked_since, plan_branch)` is older
than its own (a flow never refused counts as blocked now) and whose own `blocked_by` entries have all cleared (no longer
an open pull request; a local flow neither in flight nor waiting). It then records itself blocked by that flow as a
`local_flow` entry, and the refusal names "an older waiting flow on branch ... (worktree ..., blocked since ...)". So
when a pull request merges and every flow waiting on it re-checks, the one blocked longest is admitted first and the
others wait for it. A waiter's entry naming the checking flow itself is clear (an admitted flow, resuming, defers to no
waiter); one naming another pull request or flow of the checking flow's own lineage is never clear. An abandoned waiter
whose blockers have cleared holds newer overlapping flows back until its plan branch is deleted (which drops its
checkpoint from discovery) or a flow is run with `--allow-overlap`.

## `--allow-overlap`

`holon flow --allow-overlap` admits a refused plan anyway (child intents inherit the flag) and appends one
`admission_override` row to `executions.jsonl` on the plan branch before dispatch, recording the conflicts or the reason
(the other `executions.jsonl` readers skip it, since they filter on `entry_type`). If that row cannot be written the run
is refused, so every overlapping execution is on record (the execution branch inherits the row). Fields:

- `entry_type` and `kind`: both `"admission_override"`.
- `plan_id`, `plan_branch` and `intent_id` (the intent branch): the admitted plan. No `execution_id`.
- `conflicts`: a list of `{flow, files}`, where `flow` names the pull request and head branch (or the local branch and
  worktree) and `files` the concrete overlapping files; empty when the refusal had no conflict.
- `reason`: the refusal reason without a conflict (no touch set, discovery unavailable), otherwise `null`.
- `recorded_by`: `"flow"`; `created_at`: an ISO 8601 UTC timestamp.

## Enforcement after Stage 3

When the executor succeeds with a non-empty diff, every changed non-metadata file must match some declared entry. Files
outside the set fail Stage 3 with `touch_set_violation`: a superseding summary row in the shape of the empty-diff guard
row (see `docs/ledger_schema.md`: same `execution_id`, higher `ledger_revision`, `status: "failed"`,
`failure_reason: "touch_set_violation"`, a `summary` naming the files) is appended to the execution branch (kept for the
human), the Stage 3 payload carries `touch_set_violation` (the files), `replan_requested: true`, `container_status` and
`superseding_row_recorded`, and the engine returns to Stage 2 with transient feedback asking the planner to widen the
touch set to the listed files or decompose the plan. This shares the `MAX_REPLAN_ATTEMPTS` counter with the over-budget
re-plan; once spent, Stage 3 fails with a message naming the files. The next Stage 3 runs admission again before
dispatching. A plan admitted by `--allow-overlap` without a touch set has nothing to enforce.
