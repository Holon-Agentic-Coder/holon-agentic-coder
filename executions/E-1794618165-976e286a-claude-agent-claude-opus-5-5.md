# Execution Record: E-1794618165-976e286a-claude-agent-claude-opus-5-5

## Summary

Implemented plan `P-1794617990-33b5cb73-claude-agent-claude-opus-5-5`. Plans now declare a `## Touch Set` section, which
the planner prompt asks for and `flow/touch_set.py` parses. The planner records it on new `plans.jsonl` rows as
`touch_set` (a list, `[]` for a placeholder, `null` without the section). Stage 5 calibration scores it against the
execution diff: precision, recall, unpredicted paths and unused entries go into the report dict, `CalibrationReport`, a
"Touch Set Accuracy" markdown section and the `ACT-CAL` row's new `touch_set_accuracy` key. `docs/ledger_schema.md`
documents both fields. The code is advisory only: a missing or malformed touch set gives `null` or `n/a` and never fails
a stage. No historic row is rewritten, and there is no new dependency, `uv.lock` change or `# noqa`.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1.0   |
| entropy_actual        | 7.5   |
| impact_actual         | 70.0  |
| cost_actual           | 12.0  |
| learning_value_actual | 4.0   |

## Steps Executed

1. Step 1 (module): added `flow/touch_set.py` (205 lines) with `TouchSetEntry`, `TouchSet` (`to_row`, tolerant
   `from_row`), `normalise_touch_path`, `parse_touch_set_section`, `entry_matches` and `TouchSetScore` /
   `score_touch_set`. Globs use a small own translator rather than `fnmatch.translate` (whose output changes across
   Python versions): `*` and `?` stay within one segment, `**/` spans zero or more directories, `[...]` and `[!...]` are
   classes. Wrapped bullet continuation lines (as prettier writes them) join the bullet, prettier-escaped `\_` in a
   `symbols:` suffix is unescaped, and nested bullets are ignored. The seven public names are re-exported from
   `flow/__init__.py` (`425 -> 441` lines) and `flow_module_budgets.json` records `touch_set.py` at 205 lines and the
   `__init__.py` raise of 16 lines, with one sentence appended to the description. The budget change landed in Step 1
   rather than Step 6 so the lint gate stays green after each commit.
2. Step 2 (planner): `holon-config/prompts/planner.template.md` extends the existing touch-set bullet with where and how
   to write `## Touch Set` and adds the section to the plan skeleton after `## Plan Description & Strategy`.
   `entrypoint/planner.py` gained `_touch_set_field(plan_md_path)` (lazy import of `parse_touch_set_section`, a warning
   and `None` on a missing section or an `OSError` / `ValueError`), and `main()` gained a single
   `plan_entry.update(_touch_set_field(plan_md_path))` line. The pinned template tests still pass unchanged.
3. Step 4 (calibration), done before Step 3 so the documentation names the real fields: `calibration.py` is pinned by a
   line ratchet in `tests/test_calibration_dual_ev.py` (net growth must stay within `1684` lines), so the scoring and
   the report section live in a new module `calibration_touch_set.py` (`touch_set_metrics`, `touch_set_report_lines`,
   lazy import of `flow/touch_set.py`), and `calibration.py` gained only call sites (`1678 -> 1683` lines). The numstat
   loop now passes the raw path to `is_metadata_path`, which already strips `./` and resolves a rename arrow, so the
   duplicated three-line normalisation was replaced by one `exec_meta["changed_paths"]` append. The report dict, the
   `CalibrationReport` (`touch_set` field, default `None`) and its `to_dict()` carry
   `{precision, recall, unpredicted_paths, unused_entries}`; the markdown report gains a "Touch Set Accuracy" section
   (percentages or `n/a`, lists capped at 20 with an "and N more" line) between sections 3 and 4. The `ACT-CAL` row
   appends one optional key, `touch_set_accuracy`, after `created_at`, so the historical key order stays a prefix; the
   key-order assertion in `tests/test_calibration.py` gained that one entry. `normalise_touch_path` also resolves the
   brace rename form `a/{b => c}/d` that `git diff --numstat` prints, so recall is scored on real target paths
   (`touch_set.py` budget `206`).
4. Step 3 (docs): `docs/ledger_schema.md` lists `touch_set` in the Stage 2 row of the stage matrix and in the
   `plans.jsonl` field list, adds a plan row `touch_set` paragraph with a JSON example (shape, `[]` for a placeholder,
   `null` without the section, absent on historic rows, consumers), and documents the `ACT-CAL` row's
   `touch_set_accuracy` (precision and recall definitions, `null` and `n/a` semantics). The calibration report fields
   are one nested `touch_set` object rather than the four flat `touch_set_*` names the plan sketched, matching the
   single `report_dict` entry of plan Step 4.3.
5. Step 5 (tests): `tests/flow/test_touch_set.py` (section parsing, nested bullets, numbered items, wrapped `symbols:`
   continuation lines and prettier escapes, placeholders, metadata and duplicate removal, `normalise_touch_path`
   including brace renames, a table of `entry_matches` cases, hand-computed scores, zero denominators, row round trip
   and tolerant `from_row`). `tests/test_planner_touch_set.py` (`_touch_set_field` with, without and with a placeholder
   section and on a missing file, plus a fresh-interpreter import test via `sys.executable -c` for the planner,
   calibration and the touch-set module). `tests/test_calibration_touch_set.py` (`touch_set_metrics` on a row with a
   touch set, a historic row, malformed rows, the markdown fallback and an unreadable plan file; report lines with
   values, the 20-item cap and `n/a`; `changed_paths` excluding metadata; and an end-to-end `generate_calibration` on a
   temporary git repository with `precision = 1 / 2` and `recall = 1 / 2`). The rendered section was checked with
   prettier for both the values and `n/a` layouts.
6. Step 6 (budgets, lint, touch-set check): the flow budgets were set in Steps 1 and 4 (`__init__.py` `425 -> 441`,
   `touch_set.py` at `206`, one description sentence). `uv run task lint` passes and adds no `# noqa`. `uv.lock` is
   unchanged. `uv run task lint-docs` reports 38 files, all of them pre-existing and outside this diff (36 historic
   `plans/*_calibration.md` reports, `.agents/holon_flow.md`, `docs/optimisation/token_reduction_measurement_plan.md`
   and `holon-config/metrics/README.md`). Every markdown file this execution changed passes `prettier --check`. Checking
   the diff by hand against the plan's `## Touch Set`: all 10 declared entries were touched (precision `10 / 10 = 1.0`).
   12 non-metadata files changed and 10 of them were declared (recall `10 / 12 = 0.83`). The two extra files:
   `calibration_touch_set.py`, a new module forced by the `calibration.py` line ratchet in
   `tests/test_calibration_dual_ev.py`, and `tests/test_calibration.py`, which needed one more entry in the pinned
   `ACT-CAL` key order (plan Step 4.5 allows this adjustment).

## Tests Run

No repeated runs: the intent sets no timing or performance target.

| command                                                                                                                                 | result                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `uv run pytest apps/sandbox-executor/tests/flow/test_package_exports.py` (Step 1)                                                       | 98 passed in 0.17s                                      |
| `uv run pytest apps/sandbox-executor/tests/test_prompt_templates.py apps/sandbox-executor/tests/test_planner.py` (Step 2)               | 76 passed, 15 subtests passed in 0.33s                  |
| `uv run pytest -n auto tests/test_calibration*.py tests/flow/` (Step 4, first try)                                                      | 2 failed, 920 passed in 9.08s (ratchet and budget)      |
| same, after moving the helpers to `calibration_touch_set.py` and fixing the budget                                                      | 1 failed, 825 passed in 7.36s (budget `205 -> 206`)     |
| `uv run pytest tests/flow/test_touch_set.py tests/test_planner_touch_set.py` (Step 5)                                                   | 51 passed in 0.69s                                      |
| `uv run pytest tests/test_calibration_touch_set.py` (Step 5)                                                                            | 9 passed in 0.09s                                       |
| affected set: the three new test files, `test_package_exports.py`, `test_calibration.py`, `test_planner.py`, `test_prompt_templates.py` | 316 passed in 1.91s                                     |
| `uv run task lint`                                                                                                                      | passed (ruff, format, flow module budgets)              |
| `uv run task lint-docs`                                                                                                                 | 38 pre-existing files outside this diff; own files pass |
| `uv run task test` (full suite, once)                                                                                                   | 4043 passed, 1 skipped in 22.59s                        |
