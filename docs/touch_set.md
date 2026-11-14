# Planner touch set

The planner declares the files a plan expects to change in a `## Touch Set` section of the plan file. Stage 2 records
the parsed set on the plan row, and Stage 5 calibration scores it against the execution diff. Admission and enforcement
will read the same rows later. The parsing, matching and scoring live in `sandbox_executor/touch_set.py`.

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
