# Execution Record: E-1794727887-d80950e5-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794727651-2a1e918e-claude-agent-claude-opus-5-5` for intent `I-1794727635-guardbase`: when the
execution branch's own ledger records an integrated decomposition of its plan branch, the Stage 4 empty-diff guard
compares the combined execution branch with its merge base on the intent's target branch using a net (merge-inclusive)
diff. Every other execution keeps the plan-branch comparison unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 5.2   |
| impact_actual         | 75.0  |
| cost_actual           | 10.0  |
| learning_value_actual | 3.5   |

## Steps Executed

### Step 1: Confirm wiring facts (read-only)

- Package exports: `apps/sandbox-executor/tests/flow/test_package_exports.py` (`public_definitions`, line ~38) treats
  only non-underscore top-level functions and classes as definitions that must be re-exported. The new module therefore
  uses private (`_`-prefixed) helper names, so `flow/__init__.py` and its budget stay untouched.
- Host fetch: the flow's Stage 4 review loop runs on the host (`flow/stages/review.py` ~96), where
  `_execution_effective_diff` fetches its own refs. In a mounted role container (`entrypoint/execution_workspace.py`
  `workspace_mounted`, line 19) nothing is fetched (`entrypoint/review_loop.py` docstring, line ~15). The target branch
  is fetched on the host by `fetch_base` (`cli/commands/_host_publish.py` line 31) for standalone role containers, but
  no explicit fetch of the target branch before a mounted review dispatch was found. Decision (fail closed): when
  mounted, the target tracking ref is only checked, and a missing ref returns unverifiable with "'<target>' is not
  fetched from origin; the host must fetch it before dispatch". No fetch was added inside the container. Possible
  host-side gap, flagged rather than widened.
- Merge shape: `flow/stages/integration.py` (`integrate_children`, line ~65) merges the plan branch into the execution
  branch with a merge commit, and children land on the plan branch as a merge commit or a squash
  (`flow/github/merge_method.py`). Both reach the execution branch only through merges, so `changed_paths`' own
  first-parent filter would drop them: a net diff is required.

### Step 2: Net, merge-inclusive changed-path helper

- Added `net_changed_paths(repo_dir, base, head, exclude, git_runner)` to
  `apps/sandbox-executor/src/sandbox_executor/git.py` right before `has_effective_diff`. It runs one two-dot
  `git diff --name-only --no-renames -z <base> <head>` (reusing `_NAME_ONLY_ARGS`) and filters with `_is_excluded`.
  `changed_paths`, `has_effective_diff`, `EMPTY_DIFF_EXCLUDES` and `empty_diff_summary` are unchanged. Module docstring
  bullet added. `ruff check` and `ruff format --check` are clean.

### Step 3: Route the guard for integrated executions

- New `apps/sandbox-executor/src/sandbox_executor/flow/combined_base.py` (90 lines; imports only `sandbox_executor.git`
  and `sandbox_executor.redaction`): `_integrated_plan_recorded` (summary row with `integrated: true`, the exact derived
  `plan_branch` and a non-empty `children` list), `_target_ref` (host: fetch the target; mounted: only check the
  host-fetched tracking ref; no origin: local branch) and `_combined_diff_verdict`
  (`git merge-base <target_ref> <exec_ref>`, then `net_changed_paths`, with the base label
  `<target>@<sha12> (merge base of '<plan branch>' with <target>)`). Every unresolvable case returns `None`.
- `flow/git_refs.py`: `_execution_effective_diff` gained `target_branch: str = "main"` and three lines that route an
  integrated execution to `_combined_diff_verdict`. The last two returns were folded into one (identical strings) to
  stay within ruff `PLR0911`. Net growth 6 lines (394 to 400).
- `review_target.py` `_classify_target_diff` passes `target_branch=base_branch`. `flow/review/loop.py` already passes
  the intent's `target_branch` to `classify_review_diff` (line ~249) and reaches the guard by no other route, so it is
  unchanged.
- `flow_module_budgets.json`: `combined_base.py: 90` added, `git_refs.py` raised from 394 to 400, one description
  sentence appended. No `flow/__init__.py` change (private names).
- Guardrail run:
  `uv run pytest apps/sandbox-executor/tests/test_review_mounted_no_fetch.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_target.py apps/sandbox-executor/tests/flow -n auto --dist worksteal`:
  962 passed in 8.73s.

### Step 4: Hermetic tests

- New `apps/sandbox-executor/tests/test_combined_base.py` (11 tests, local bare origin and `tmp_path` only): merged
  children pass (merge and squash variants, detail names `main@<sha12>`); bookkeeping-only children refused with the
  merge-base summary ending `(compared on origin)`; plain execution unchanged (old `False` summary and old
  `"1 changed path(s) on origin"`); an integrated row naming another plan branch, or with no children, keeps the plan
  base; an unfetched host fetches plan, execution and target itself; a mounted container without the target ref and an
  orphan target are unverifiable; the no-origin local path; and `classify_review_diff(repo, EXEC, "main")` is
  `DiffVerdict.VERIFIED`.
- `apps/sandbox-executor/tests/test_git.py`: two `net_changed_paths` tests (merged-in content counts where
  `changed_paths` does not; exclusions and a rename into `executions/` match `changed_paths`).
- Revert check (not committed): with the routing disabled in `git_refs.py`, 8 of the 11 new tests fail; the 3 that still
  pass are the plain-path and non-matching-row tests, which pin the unchanged behaviour.

### Step 5: Targeted tests, full suite and lints

- Targeted, full suite, `uv run task lint` and the budget check all pass (see Tests Run). `uv.lock` and `pyproject.toml`
  are unchanged, and no new `*.jsonl` file exists.
- `uv run task lint-docs` exits 1 because of 39 Markdown files that this branch does not touch (for example
  `.agents/holon_flow.md`, `holon-config/metrics/README.md` and several older `plans/*_calibration.md` files). They are
  already present on `main` and were left alone as out of scope. Every Markdown file this execution changed, including
  this record and the plan, passes `npx prettier --check`. This record was formatted once with prettier.

### Step 6: Execution notes and hand-off

- Root cause: the guard compared the combined execution branch with the plan branch, which had already absorbed every
  child. A second trap: `changed_paths` counts only the head's own first-parent, non-merge commits, so switching only
  the base would still have reported the branch as empty. The decomposed path therefore uses the net diff from the merge
  base on the target branch.
- Residual risk (accepted in the plan): a forged `integrated` row on a plain execution switches the base to the target
  merge base. That brings only the plan branch's own content into the count, which is normally bookkeeping and excluded.
  The human maintainer still reviews and merges every PR.
- Host-fetch note: no explicit host fetch of the target branch before a mounted review dispatch was found. A mounted
  combined review without `refs/remotes/origin/<target>` fails closed and names the target.
- No ledger was written or rewritten by this execution. The entrypoint records the execution, and merging stays with the
  human maintainer.

## Tests Run

| command                                                                                                                                                                                                                                                                                          | result                                                              | pytest time |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- | ----------- |
| `uv run pytest apps/sandbox-executor/tests/test_review_mounted_no_fetch.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_target.py apps/sandbox-executor/tests/flow -n auto --dist worksteal` (Step 3 guardrail)                                       | 962 passed, 0 failed                                                | 8.73s       |
| `uv run pytest apps/sandbox-executor/tests/test_combined_base.py -n auto --dist worksteal` (Step 4)                                                                                                                                                                                              | 11 passed, 0 failed                                                 | 1.98s       |
| `uv run pytest apps/sandbox-executor/tests/test_combined_base.py -p no:randomly` with routing disabled (revert check, not committed)                                                                                                                                                             | 3 passed, 8 failed (expected)                                       | 0.79s       |
| `uv run pytest apps/sandbox-executor/tests/test_combined_base.py apps/sandbox-executor/tests/test_git.py apps/sandbox-executor/tests/test_review_loop.py apps/sandbox-executor/tests/test_review_mounted_no_fetch.py apps/sandbox-executor/tests/test_review_target.py -n auto --dist worksteal` | 209 passed, 0 failed                                                | 2.49s       |
| `uv run task test` (full suite, once)                                                                                                                                                                                                                                                            | 4085 passed, 1 skipped, 0 failed                                    | 23.03s      |
| `uv run task lint` (ruff, format check, flow module budgets)                                                                                                                                                                                                                                     | pass; 58 modules within budget                                      | n/a         |
| `uv run task lint-docs`                                                                                                                                                                                                                                                                          | exit 1: 39 files not changed by this branch; all changed files pass | n/a         |

Each command ran once, so no median applies. The intent does not measure timing.
