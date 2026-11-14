# Planner touch set

The planner declares the files a plan expects to change in a `## Touch Set` section of the plan file. Stage 2 records
the parsed set on the plan row, Stage 3 admits the plan only when that set overlaps no flow in flight, Stage 3 then
enforces it on the execution diff, and Stage 5 calibration scores it. The parsing, matching, overlap and scoring rules
live in `sandbox_executor/touch_set.py`; discovery in `flow/in_flight.py`, the admission decision in `flow/admission.py`
and enforcement in `flow/stages/touch_set_guard.py`.

## Plan row `touch_set`

`parse_touch_set_section` parses the section of the committed plan file. The field is a list of objects, each with
`path` (a repository-relative POSIX path, a directory ending in `/`, or a glob where `*` stays within one path segment
and `**` spans directories) and, only when the plan named some, `symbols` (stored and reported, never scored). Metadata
paths (`plans/`, `executions/`, `holon-knowledge/`, `.holon/`) are never listed. Placeholder items such as `- none` are
skipped (a section holding only placeholders records `[]`), and a plan without the section records `null`; neither fails
the plan. Example:

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
  `gh pr list --state open` call, at most 50 pull requests), and a checkpoint (`.holon/flow/checkpoint-*.json`) in
  another git worktree of the repository whose current stage is execute or review and whose plan branch still exists.
  Flows of the same root intent (this run's own lineage) are excluded, and a local flow that also has a pull request is
  listed once. Discovery runs host-side and only reads refs and checkpoint files; it never fetches, checks out or writes
  another flow's branch.
- **Compared:** this plan's declared entries against the other flow's declared entries (two literals overlap when equal,
  a literal and a directory or glob when the entry matches the literal, two directories when one contains the other, and
  a glob against a directory or glob when one literal prefix starts the other: conservative on purpose), and against the
  real files of each pull request (`gh pr diff <n> --name-only`). Metadata never conflicts.
- **Refusal:** Stage 3 fails before any dispatch with a message naming each pull request and branch (or local branch and
  worktree) and the overlapping files, for example
  `Stage 3 refused: the touch set overlaps pull request #7 (branch I-.../E-.../_) on src/app.py; finish or merge that flow first, or rerun with --allow-overlap.`
  The payload carries `admission_refused: true`, `dispatched: false` and `conflicts`. A plan row without a touch set,
  and a `gh` or `git` failure during discovery, are refused too (fail closed), with the reason in `admission_reason`. A
  dry run only logs the decision. Refusal never closes, merges or edits the other flow.

A checkpoint left at execute or review by an abandoned run still counts as in flight while its plan branch exists;
delete the checkpoint (or the branch) or use the override. Local flows are compared by their declared sets only.

## `--allow-overlap`

`holon flow --allow-overlap` admits a refused plan anyway (child intents inherit the flag) and appends one
`admission_override` row to `executions.jsonl` on the plan branch before dispatch, recording the conflicts or the reason
(see `docs/ledger_schema.md`). If that row cannot be written the run is refused, so every overlapping execution is on
record.

## Enforcement after Stage 3

When the executor succeeds with a non-empty diff, every changed non-metadata file must match some declared entry. Files
outside the set fail Stage 3 with `touch_set_violation`: a superseding failed summary row is appended to the execution
branch (kept for the human), and the engine returns to Stage 2 with transient feedback asking the planner to widen the
touch set to the listed files or decompose the plan. This shares the `MAX_REPLAN_ATTEMPTS` counter with the over-budget
re-plan; once spent, Stage 3 fails with a message naming the files. The next Stage 3 runs admission again before
dispatching. A plan admitted by `--allow-overlap` without a touch set has nothing to enforce.
