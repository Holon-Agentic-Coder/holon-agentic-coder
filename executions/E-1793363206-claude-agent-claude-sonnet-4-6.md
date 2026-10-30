# Execution Record: E-1793363206-claude-agent-claude-sonnet-4-6

## Summary

Implementing plan P-1793339950-claude-agent-claude-opus-5-5 for intent
I-1793339935-flow-tests-never-write-the-checkout-knowledge-index.

The goal is to add a guard that detects and fails any test that creates, modifies or deletes a file under the checkout's
`holon-knowledge/.index/` directory. The guard is a per-test autouse fixture in `conftest.py` backed by snapshot helpers
in `hermetic_fixtures.py`.

**Step 1 findings (baseline measurement):**

The checkout had no `holon-knowledge/.index/` directory before any test ran (clean baseline). The two named tests
(`test_stage1_intent_validation_failure` and `test_intent_stage_fails_fast_on_unresolved_open_questions`) both use
`repo_dir=str(tmp_path)` and write the index to `tmp_path`, not the checkout. Running the full `test_flow.py` (311
tests) left the checkout index absent. Running the full suite (2855 tests) also left it absent. No writers were found at
baseline.

The `repo_dir="."` patterns at lines 1228, 3075, and 3632 of `test_flow.py` were investigated. The review-stage consult
at `flow.py:4199` only opens the database `if os.path.exists(db_path)`, so it is a no-op when the checkout has no index.
The `_dispatch_role` test mocks the dispatcher and never reaches the consult. These three tests do not write the
checkout index.

**Step 2 (guard added):**

Added `knowledge_index_snapshot(root)` and `describe_index_changes(before, after)` to `hermetic_fixtures.py`. Added the
`checkout_knowledge_index_guard` autouse fixture to `conftest.py`. Extended `test_sandbox_hermetic_guard.py` with six
unit tests for the helpers and an index-change assertion in the stress canary.

**Step 3 (sweep):**

No additional writers found. The grep patterns listed in the plan were checked; all `repo_dir="."` uses either do not
reach the consult (review stage guard) or are mocked (dispatch test). No other files under
`apps/sandbox-executor/tests/` write to `holon-knowledge/.index/`.

**Step 4 (fixes):**

No fixes required for the named tests (already hermetic). No other writers found.

**Step 5 (scratch proof):**

A scratch test with `repo_dir="."` was run. The guard caught the write and reported:

```
Test wrote the checkout's holon-knowledge/.index/: created holon-knowledge/.index/action_index.db
-- pin repo_dir and the working directory to tmp_path to fix this.
```

The scratch file and the created index were deleted. The checkout returned to the clean baseline.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 2.0   |
| impact_actual         | 65.0  |
| cost_actual           | 8.0   |
| learning_value_actual | 5.0   |

## Steps Executed

- **Step 1**: Baseline measurement. Confirmed the two named tests use `tmp_path` and the full suite leaves the checkout
  index absent. No writers found.
- **Step 2**: Added `knowledge_index_snapshot`, `describe_index_changes` to `hermetic_fixtures.py`; added
  `checkout_knowledge_index_guard` autouse fixture to `conftest.py`; added six unit tests and the canary index assertion
  to `test_sandbox_hermetic_guard.py`.
- **Step 3**: Sweep confirmed no other writers. All `repo_dir="."` uses are either guarded by `os.path.exists` (review
  stage) or mocked (dispatch test).
- **Step 4**: No production or test changes needed beyond the guard itself.
- **Step 5**: Scratch proof confirmed the guard fires with a clear message naming the path. Cleanup restored the clean
  baseline.
- **Step 6**: Full suite, lint, and lint-docs all pass. Ledger row appended.

**Touch set note:** `conftest.py` and `hermetic_fixtures.py` are inside `apps/sandbox-executor/tests/`, the touch set's
outer boundary. A per-test autouse guard cannot live only in a test module; `conftest.py` is the correct place for
suite-wide fixtures.

## Tests Run

| command                                                                | result               | time   |
| ---------------------------------------------------------------------- | -------------------- | ------ |
| `uv run pytest test_flow.py test_sandbox_hermetic_guard.py -n auto -q` | 318 passed           | 4.98s  |
| `uv run pytest apps/sandbox-executor/tests/ -n auto -q` (post-guard)   | 2861 passed, 8 warns | 30.27s |
| `uv run task lint`                                                     | passed               | &lt;1s |
| `uv run task lint-docs`                                                | passed (after fix)   | &lt;5s |
