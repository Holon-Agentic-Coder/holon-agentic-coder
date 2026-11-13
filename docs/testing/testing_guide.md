# Executing Unit and Integration Tests

This guide explains how to execute the unit and integration tests, run linting and format checks, and troubleshoot
environment issues in the Holon Agentic Coder repository.

---

## 1. Running Tests

Testing is managed using `pytest`. The test suite includes both unit tests and docker-based integration tests.

### Running the Entire Test Suite

To run all tests (unit and integration tests), run the following command from the workspace root:

````bash
uv run pytest

> [!NOTE]
> **Why `PYTHONPATH` is not necessary:**
> Because this repository is configured as a `uv` workspace, `uv` automatically resolves local package dependencies.
> Specifically, the root `pyproject.toml` lists `"apps/sandbox-executor"` as a workspace member and sets its source path:
> ```toml
> [workspace]
> members = ["apps/sandbox-executor"]
>
> [tool.uv.sources]
> sandbox-executor = { path = "apps/sandbox-executor" }
> ```
> This configuration allows `uv run` to resolve module imports directly from the active source files (`apps/sandbox-executor/src`) without needing any manual `PYTHONPATH` overrides.



### Running Specific Tests

You can filter tests by their pytest markers (such as `integration_test`) or by target files:

- **Run only integration tests:**
  ```bash
  uv run pytest -m integration_test
````

- **Run only unit tests (excluding integration tests):**
  ```bash
  uv run pytest -m "not integration_test"
  ```
- **Run the unit selection the way CI does (in parallel, with pytest-xdist):**
  ```bash
  uv run task test
  ```
  This runs `pytest -n auto --dist worksteal -m "not integration_test and not stress"`. To debug serially, use
  `uv run task test -n 0`, or disable xdist with `uv run pytest -p no:xdist -m "not integration_test and not stress"`.
- **Repeated runs:** by default, run the affected tests first with `uv run pytest <paths>`, then the full suite once
  with `uv run task test`. Repeat runs only when the intent requires measuring performance or timing behaviour
  (test-suite speed, a timing-sensitive or flaky test, a stated performance target). Take N from the intent, or state N
  and the reason; never open-ended or "many" runs. Every repetition runs with `-n auto --dist worksteal`, including runs
  under CPU load; run serially only when serial behaviour is itself the measurement, and then once. Report each run's
  pass/fail count and pytest-reported time, plus the median, from real runs only, and never re-measure a commit that
  already has a recorded figure. For scale: two test files took about 30 s per serial run under CPU load, against 16.97
  s under `-n auto`.
- **Run a specific test file:**
  ```bash
  uv run pytest apps/sandbox-executor/tests/test_planner.py
  ```

---

## 2. Running Linting and Format Checks

Formatting and code quality are managed using `ruff`.

### Linting Checks

To run the static analysis checks:

```bash
uv run task lint
# Or directly:
uv run ruff check .
```

To automatically fix any autofixable linting issues:

```bash
uv run ruff check --fix .
```

### Formatting Checks

To check if the files comply with the codebase format guidelines:

```bash
uv run ruff format --check .
```

To automatically reformat the codebase:

```bash
uv run task format
# Or directly:
uv run ruff format .
```

---

## 3. Managing the Environment & Troubleshooting

### Dealing with Stale Code

If the virtual environment caches a previous build and `pytest` fails to see your local code updates (causing unexpected
`AttributeError` or missing code errors), clean the environment caches:

```bash
uv run task clean
```

_Note: This command clears all python cache directories (`__pycache__`), test caches (`.pytest_cache`), and removes the
root `.venv`. You should run `uv sync` afterwards to re-initialise a clean environment._

### Re-syncing Workspace Dependencies

To recreate and align your local virtual environment:

```bash
uv sync
```

> [!IMPORTANT] **Single Virtual Environment Rule:** Always use the `.venv` located at the root of the workspace. Do NOT
> run commands that initialise a separate virtual environment inside subdirectories (e.g.,
> `apps/sandbox-executor/.venv`), as this will cause package resolution mismatches.

## Stage 4 pre-push test failures

When the resolver's pre-push `uv run task test` fails, the halt message reads
`Resolver pre-push test failed (exit code N); failed: <ids> (+K more); full output: <path>: <excerpt>`. It names at most
10 failed test ids (about 600 characters) and stays within 2000 characters. The excerpt is summary-first: the
`short test summary info` section plus the final counts line (for example `2 failed, 198 passed in 12.34s`), or the tail
of the output when there is no such section, never the progress dots at its head. The gate sets `PYTEST_ADDOPTS` to
include `-rfE` unless it already reports failures and errors. The complete output is redacted once, before anything is
kept, and written to `.holon/review/verification/test.log` in the workspace. That directory is git-ignored, so it is
never committed. Lint failures and missing tools keep their earlier messages.

The rejected attempt is kept too. Before the container exits, the resolver's uncommitted changes (new files included,
the ledger and `.holon/` excluded) are saved as `.holon/review/rejected/iter-N.patch`, beside an `iter-N.json` record of
the failing tests, the summary and the head they were made on. The workspace is bind-mounted, so the host can read both.
The real git index is never altered: the diff uses a temporary copy of it. The patch is redacted before it is capped at
200,000 characters and written. The resolver result carries the optional `rejected_patch`, `failed_tests` and
`test_summary` fields, and the host appends one failed `ACT-RES` action row (label `rejected-attempt`) with
`rejected_patch`, `failed_tests` and the summary as `error_context`. The next resolver pass on the same head gets a
"Previous attempt failed verification" prompt section with the failing tests, the summary and the redacted, capped diff,
and is told not to repeat that change. A passing test run clears the saved attempts.
