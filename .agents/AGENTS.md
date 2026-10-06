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
