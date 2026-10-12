# Behavioral Rules for Holon Agentic Coder

- **Pytest execution**: Always execute `pytest` commands using `uv run pytest` (e.g., `uv run pytest <args>`) to ensure
  environment consistency and dependencies are resolved correctly.
- **Single Root Virtual Environment**: Always execute all `uv` and Python commands strictly from the repository root
  directory. Never run `uv` commands with a working directory inside subfolders (e.g. `apps/sandbox-executor/`). Virtual
  environments must exist exclusively at the root `.venv`.
- **Cleaning virtual environments**: If the virtual environment/stale code requires cleanup, always run
  `uv run task clean` instead of manually removing `.venv` or cache directories yourself.
- **Task Runner (`taskipy`)**: Use standardized `uv run task <name>` tasks configured in `pyproject.toml`:
  - `uv run task check` (runs lint and lint-docs)
  - `uv run task lint` (runs uv lock --check, git diff uv.lock, ruff check, and ruff format --check)
  - `uv run task lint-docs` (runs prettier check on markdown)
  - `uv run task format` (formats python and markdown files)
  - `uv run task test` (runs unit tests excluding integration and stress)

## OS Toolbelt & Script Hygiene Rules

- **Mandatory Tool Probing**: Always probe for tools before writing scripts using `command -v <tool>` (e.g.
  `command -v jq`, `command -v yq`, `command -v shellcheck`).
- **No Throwaway Scripts**: Strictly forbid writing one-off Python or Node throwaway scripts to inspect, filter,
  pretty-print, or validate JSON, YAML, or text files.
- **Mandatory Baked-in Tools**:
  - Require `jq` for JSON and JSONL inspection, transformation, and validation (`jq -r`, `jq -s`, `jq -e .`).
  - Require `yq` for YAML inspection, transformation, and validation (`yq -e 'true' <file>`).
  - Require `grep`, `awk`, and `sed` for text and line extraction.
  - Require `diff` and `cmp` for file comparisons.
  - Require `gh api --jq` for GitHub CLI queries and filtering.
- **Scratch Script Boundary**: Explicitly restrict scratch scripts to `/tmp` or `todo/`, and mandate that they are
  deleted immediately after use.
- **No Per-Command Environment Injection**: Forbid per-command `export VAR=... &&` prefixes. Instruct agents to rely on
  standard `ENV PATH` discoverability.

## Action Ledger & Provenance Guidelines

- **Action-Level Execution Logging**: When executing tasks, record discrete operations (code edits, test runs, config
  updates, git mutations) as `"entry_type": "action"` in `holon-knowledge/ledger/executions.jsonl`.
- **Commit Trailers for Review & CI Provenance**: When resolving PR review comments or CI failures, append resolver
  trailers to the commit message:
  - `Action-Origin: pr_review` (or `ci_fix`, `operator`, `calibration`)
  - `Review-Severity: critical` (or `important`, `nit`)
  - `Review-Iteration: <iteration_number>`
  - `Review-Finding-Ids: FIND-001, FIND-002`
- **Pre-execution Failure Querying**: Before embarking on risky refactors or complex implementations, query past
  failures and lessons using `holon ledger query --outcome failure --category <cat>` to avoid repeating past mistakes.
