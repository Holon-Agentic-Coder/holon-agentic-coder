# Execution Record: E-1794458626-bd9b5cc1-claude-agent-claude-sonnet-5-5

## Summary

The failing children test was caused by the unstubbed `reconcile_with_origin` (a real fetch from origin), not only
`_fetch_refspecs_error`. The children tests now stub both, an autouse `network_guard_fixture` (`tests/network_guard.py`)
fails any git network command against a non-local remote and any non-loopback inet connection, the guard has 45 tests,
and `hermetic_testing.md` documents it. No production code changed.

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 0.9   |
| entropy_actual        | 4.0   |
| impact_actual         | 55.0  |
| cost_actual           | 9.0   |
| learning_value_actual | 6.0   |

## Steps Executed

- Step 1 (hermetic children tests): the real cause was not the `_fetch_refspecs_error` call but `reconcile_with_origin`,
  which fetches from origin; with an unreachable origin it marks the child `origin_lookup_failed`, so Stage 3 logs
  `resuming` instead of `running`. `_run_children` now stubs it (overridable through a `reconcile` argument). Reproduced
  with `GIT_SSH_COMMAND=false` and the ssh-URL origin before the fix; passes after.
- Step 2 (guard): added `tests/network_guard.py` and the autouse `network_guard_fixture` (chained on `gh_write_guard`).
  The first full run exposed 4 tests that needed `reconcile_with_origin` stubbed (2 in `test_children.py`, 2 in
  `test_decomposition.py`) and 2 that use a loopback ssh/https URL (`test_host_remote.py`, `test_review_loop.py`);
  loopback hosts are therefore allowed by the guard rather than the tests changed.
- Step 3 (guard tests): `tests/test_network_guard.py`, 45 tests covering the classifier, refusal and recording, a local
  bare remote, `insteadOf`, sockets (refused, loopback, `AF_UNIX`).
- Step 5 (docs): added a "Network guard" section to `apps/sandbox-executor/docs/hermetic_testing.md`; `testing_guide.md`
  does not list guards, so it is unchanged.
- Step 4 (container proof): `docker` is not available in the executor sandbox, so the real review-loop image run was
  **not** possible. The suite was run on the host with the container conditions simulated: no `ssh` binary on `PATH`,
  `GIT_SSH_COMMAND=false` and an ssh-URL `origin` (`git@github.com:...`). `unshare -rn` is not permitted, so no network
  namespace was used.

## Tests Run

- Reproduction before the fix: `GIT_SSH_COMMAND=false uv run pytest apps/sandbox-executor/tests/flow/stages -p no:xdist`
  gave `1 failed, 103 passed` on the two children and decomposition modules; after the fix `142 passed`.
- `uv run pytest apps/sandbox-executor/tests/test_network_guard.py`: `45 passed`.
- `GIT_SSH_COMMAND=false uv run task test` (simulated container, once): `3663 passed` in `33.00s`. The baseline of 3617
  plus the 45 new tests and the earlier `3618` run is consistent. No repetition was needed (no timing measurement).
- `uv run ruff check` and `uv run ruff format --check` on `apps/sandbox-executor/tests`: pass.
