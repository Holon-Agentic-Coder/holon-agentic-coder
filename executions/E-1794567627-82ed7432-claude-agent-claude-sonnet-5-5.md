# Execution Record: E-1794567627-82ed7432-claude-agent-claude-sonnet-5-5

## Summary

The capture code already existed. A linked worktree with staged, unstaged and new-file edits produced a correct diff
locally, so that was not the cause. The likely real-run cause is git refusing the bind-mounted workspace for a different
container user (`dubious ownership`): every `git` call returned `None`, so no patch was written while the JSON record
still was. The fix trusts the workspace per call (`-c safe.directory=<workspace>`), writes files atomically, and carries
the redacted, capped patch in the result payload (`rejected_patch_text`) so the host writes it if the container file is
lost.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.85  |
| entropy_actual        | 3.5   |
| impact_actual         | 35.0  |
| cost_actual           | 7.0   |
| learning_value_actual | 4.0   |

## Steps Executed

1. Diagnosis: the linked-worktree scratch repository gave a correct diff, so the cause is container-only. The
   `safe.directory` refusal is the named cause; the payload route removes the dependence on the mount.
2. Durability: `entrypoint/rejected_attempt.py` (safe directory, atomic write, `read_saved_patch`), `review_loop.py` and
   `resolver_result.py` (new optional `rejected_patch_text`), `ResolverOutcome`, and the new
   `flow/review/rejected_patch_host.py` called from `_record_rejected_attempt`. Budgets updated.
3. Test: `tests/test_rejected_patch_durability.py`; the `safe.directory` case fails without the fix.
4. Docs: `docs/ledger_schema.md` paragraph on `rejected_patch`.
5. Gates: see below.

## Tests Run

- `uv run pytest apps/sandbox-executor/tests/test_rejected_patch_durability.py apps/sandbox-executor/tests/test_rejected_attempt.py`:
  13 passed.
- `uv run task lint`: passed (ruff, format, flow module budgets).
- `uv run task test`: 3917 passed, 1 skipped in 21.58s (one earlier run failed on the package-export convention, fixed
  by making the host helper private; one run hit an xdist collection mismatch and passed on rerun).
- `uv run task lint-docs`: 38 Prettier warnings in files untouched by this change, the same count as before it; the
  edited files are formatted.
