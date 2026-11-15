# Execution Record: E-1794727887-d80950e5-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794727651-2a1e918e-claude-agent-claude-opus-5-5` for intent `I-1794727635-guardbase`: when the
execution branch's own ledger records an integrated decomposition of its plan branch, the Stage 4 empty-diff guard
compares the combined execution branch with its merge base on the intent's target branch using a net
(merge-inclusive) diff. Every other execution keeps the plan-branch comparison unchanged.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.0   |
| entropy_actual        | 0.0   |
| impact_actual         | 0.0   |
| cost_actual           | 0.0   |
| learning_value_actual | 0.0   |

## Steps Executed

### Step 1: Confirm wiring facts (read-only)

- Package exports: `apps/sandbox-executor/tests/flow/test_package_exports.py` (`public_definitions`, line ~38) treats
  only non-underscore top-level functions and classes as definitions that must be re-exported. The new module therefore
  uses private (`_`-prefixed) helper names, so `flow/__init__.py` and its budget stay untouched.
- Host fetch: the flow's Stage 4 review loop runs on the host (`flow/stages/review.py` ~96), where
  `_execution_effective_diff` fetches its own refs. In a mounted role container
  (`entrypoint/execution_workspace.py` `workspace_mounted`, line 19) nothing is fetched
  (`entrypoint/review_loop.py` docstring, line ~15). The target branch is fetched on the host by `fetch_base`
  (`cli/commands/_host_publish.py` line 31) for standalone role containers, but no explicit fetch of the target branch
  before a mounted review dispatch was found. Decision (fail closed): when mounted, the target tracking ref is only
  checked, and a missing ref returns unverifiable with "'<target>' is not fetched from origin; the host must fetch it
  before dispatch". No fetch was added inside the container. Possible host-side gap, flagged rather than widened.
- Merge shape: `flow/stages/integration.py` (`integrate_children`, line ~65) merges the plan branch into the execution
  branch with a merge commit, and children land on the plan branch as a merge commit or a squash
  (`flow/github/merge_method.py`). Both reach the execution branch only through merges, so `changed_paths`' own
  first-parent filter would drop them: a net diff is required.

### Step 2: Net, merge-inclusive changed-path helper

- Added `net_changed_paths(repo_dir, base, head, exclude, git_runner)` to `apps/sandbox-executor/src/sandbox_executor/git.py`
  right before `has_effective_diff`. It runs one two-dot `git diff --name-only --no-renames -z <base> <head>` (reusing
  `_NAME_ONLY_ARGS`) and filters with `_is_excluded`. `changed_paths`, `has_effective_diff`, `EMPTY_DIFF_EXCLUDES` and
  `empty_diff_summary` are unchanged. Module docstring bullet added. `ruff check` and `ruff format --check` are clean.
