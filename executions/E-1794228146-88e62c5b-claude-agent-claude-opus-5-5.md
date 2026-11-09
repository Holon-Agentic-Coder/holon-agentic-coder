# Execution Record: E-1794228146-88e62c5b-claude-agent-claude-opus-5-5

## Summary

Implements plan `P-1794227872-152246bc-claude-agent-claude-opus-5-5`: nest decomposed children under the plan branch,
merge each child PR into it with a guarded merge commit, integrate the plan branch into the parent execution branch so
the normal Stage 4 and Stage 5 raise and review the combined PR, and resume decomposed runs without re-dispatching the
parent executor or a child.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0     |
| entropy_actual        | 0     |
| impact_actual         | 0     |
| cost_actual           | 0     |
| learning_value_actual | 0     |

## Steps Executed

### Step 1: Contracts, budgets and Stage 4 behaviour confirmed (read-only)

- Baseline: `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal` gave 504 passed in `5.15s`.
- Headroom: `pipeline.py` 394/394, `flow/__init__.py` 362/362, `stages/decomposition.py` 242/242. Deleting the
  decomposed Stage 4/5 skip block (8 lines) and `_parent_was_decomposed` (6 lines) frees 14 lines in `pipeline.py`, so
  a resume hook of about 5 lines fits without raising its budget.
- Stage 4 (`stages/review.py`) raises and reviews the PR from `context.execution_branch` into
  `intent_data["target_branch"]` with no `main` special case, so a combined decomposed PR needs no Stage 4 change.
- Stage 1 cuts the child intent branch from `origin/{target_branch}` (`entrypoint/intent_creator.py` clones and checks
  out `origin/{target_branch}`; the test fake `FakeRoleAgents._intent` does the same), so recording the plan branch as
  the child's `target_branch` cuts each child from the plan branch tip as it stands on origin.
- Checkpoints of a child (`parent_intent_id` set) are keyed on the child's bare branch in `save_checkpoint`, so each
  child already has its own checkpoint file.
- `git_flow.repository_default_branch` falls back to `main`, and `probe_ci_checks` falls back to a passing
  `local_tests` result when `gh` is missing; neither is fail-closed, so the merge guard uses its own `gh` lookups
  through an injectable runner.
- Unplanned constraint: `tests/flow/test_move_proof.py` pins the AST digest of every flow-package definition against
  `tests/flow/fixtures/flow_py_definitions.json` and fails on any new definition ("extra"), any changed body and any
  removed definition. Resolution: the fixture gains an `added_definitions` map (each new definition with its module,
  this execution and the reason) and a `removed_definitions` map; changed bodies are listed in the existing
  `amended_definitions` map with refreshed digests; the move-proof test excludes declared additions from "extra" and
  checks that each declared addition exists. The late-binding plumbing key moves from
  `_decomposition.PipelineEngine` to `_children.PipelineEngine` in `tests/flow/move_proof.py`.
- Tests that pin the old behaviour and must be updated in Step 8: `tests/flow/stages/test_decomposition.py` (child
  `target_branch` equals the intent branch, the "parent intent branch"/"Pending (not yet automated)" summary, the
  decomposed Stage 4/5 skip, `_parent_was_decomposed`, end-to-end runs with `skip_review=True`),
  `tests/flow/test_package_exports.py` (late binding on `decomposition`), `tests/test_cli.py` (decomposed summary
  wording) and `tests/flow/fixtures/flow_py_definitions.json` with `tests/flow/move_proof.py`.
- Abort criteria not met: Stage 4 targets any branch and the hook fits the freed lines, so the plan continues.

### Step 2: `flow/ledger_interleave.py` for ledger-only merge conflicts

- `interleave_ledger_rows(base, ours, theirs)`: refuses (with `ConflictResolutionError` from `ledger_reconcile`) a side
  that does not start with the merge-base rows; keeps the base rows as a byte-identical prefix; merges the two appended
  tails like a merge step, so each side keeps its own order (a deviation from a full stable sort, which could reorder
  rows within one side's tail), the earlier `created_at` goes first, ours wins a tie, and a row without a parsable
  `created_at` waits for the other side's timed rows; a row already kept is never repeated.
- `resolve_ledger_only_conflicts(repo_dir)`: returns False when nothing conflicts, when any unmerged path is outside
  `holon-knowledge/ledger/*.jsonl` or when a ledger conflict is not a pure append; otherwise writes the interleaved
  rows from index stages 1, 2 and 3 and stages them.
- Tests: `tests/flow/test_ledger_interleave.py` (10 tests, including a real two-branch repository with a conflicting
  `executions.jsonl`, and a non-ledger conflict left unresolved for the caller).
- Move proof: `tests/flow/test_move_proof.py` now reads an `added_definitions` map from the fixture (excluded from
  "extra"; a new test checks each declared addition is defined once in its declared module and carries a reason);
  this module's seven definitions are declared there. Budget entry `ledger_interleave.py: 112`.
- `uv run pytest apps/sandbox-executor/tests/flow -n auto --dist worksteal`: 515 passed in `5.05s`; `ruff check`,
  `ruff format --check` and the budget script pass.
