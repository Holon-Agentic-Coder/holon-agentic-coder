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
  - Checking markdown formatting with Prettier (`npx --yes prettier@3.8.4 --check "**/*.md"`).
  - Validating repository cleanup via `make clean`.
- `test-unit.yml`: Runs on pushes to `main` and `develop` and on pull requests across both `ubuntu-latest` and
  `macos-latest`. It executes unit tests (`uv run pytest -m "not integration_test"`) using cached `uv` dependencies.
- `test-integration.yml`: Runs on pushes to `main` and `develop` and on pull requests on `ubuntu-latest`. It builds all
  sandbox Docker images via `./apps/sandbox-executor/build_all_images.sh --output-log` and executes integration tests
  that require Docker services. For why containerized tests run exclusively on Linux, see
  [macos-docker.md](../macos-docker.md).
- `build-images.yml`: Manual dispatch workflow to build and verify Docker container images with multi-architecture
  Buildx caching.

## Branch Protection Status Checks

Repository maintainers configuring branch protection rulesets on `main` or `develop` should require the following status
checks:

- `Test - Hygiene / hygiene (ubuntu-latest)`
- `Test - Unit / unit (ubuntu-latest)`
- `Test - Unit / unit (macos-latest)`
- `Test - Integration / integration (ubuntu-latest)`
- `Run Make / build (ubuntu-latest)`
- `Run Make / build (macos-latest)`

## Composite Actions

- `.github/actions/docker-pull/`: Reusable GHCR pull-through caching composite action. It attempts to pull base or
  dependency container images from GitHub Container Registry (GHCR) mirror cache first. If an image is not present in
  GHCR, it falls back to pulling from the public registry source, pushes the image to the GHCR mirror cache (when
  `packages: write` permissions are available), and retags it back to its original name. This minimizes upstream rate
  limits and accelerates container build pipelines.

## Standards Alignment

All workflows in this repository conform to the standard Holon modular CI/CD architecture derived from
[`agentic-knowledge-base/.github`](https://github.com/thomashan/agentic-knowledge-base/tree/main/.github).
