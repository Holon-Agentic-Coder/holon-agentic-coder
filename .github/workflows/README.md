# GitHub Actions Workflows

This directory contains the CI/CD workflows for the `holon-agentic-coder` project.

## Workflows

- `make.yml`: Runs on pushes to `main` and `develop` and on pull requests across both `ubuntu-latest` and
  `macos-latest`. It verifies `uname -m`, `make help`, and default `make`.
- `test-hygiene.yml`: Runs on pushes to `main` and `develop` and on pull requests on `ubuntu-latest`. It executes fast
  repository hygiene in an isolated job:
  - Verifying lockfile currency with `uv lock --check` and `git diff --exit-code uv.lock`.
  - Running static linting with `uv run ruff check .`.
  - Checking formatting with `uv run ruff format --check .`.
  - Checking markdown formatting with Prettier (`npx prettier@3.8.4 --check "**/*.md"`).
  - Validating repository cleanup via `make clean`.
- `test-unit.yml`: Runs on pushes to `main` and `develop` and on pull requests across both `ubuntu-latest` and
  `macos-latest`. It executes unit tests (`uv run pytest -m "not integration_test"`) using cached `uv` dependencies.
- `test-integration.yml`: Runs on pushes to `main` and `develop` and on pull requests on `ubuntu-latest`. It builds all
  sandbox Docker images via `./apps/sandbox-executor/build_all_images.sh --output-log` and executes integration tests
  that require Docker services. For why containerized tests run exclusively on Linux, see
  [macos-docker.md](../macos-docker.md).
- `build-images.yml`: Manual dispatch workflow to build and verify Docker container images with multi-architecture
  Buildx caching.

## Standards Alignment

All workflows in this repository conform to the standard Holon modular CI/CD architecture derived from
[`agentic-knowledge-base/.github`](https://github.com/thomashan/agentic-knowledge-base/tree/main/.github).
