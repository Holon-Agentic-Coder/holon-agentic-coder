.DEFAULT_GOAL := help

# OS and Architecture Detection
DETECTED_OS := $(shell uname -s)
DETECTED_ARCH := $(shell uname -m)

PYTEST_ARGS ?=

.PHONY: help setup test test-integration check lint lint-docs format format-code format-docs clean distclean build-images check-prerequisites

help:
	@echo "Usage: make [target] [PYTEST_ARGS=\"...\"]"
	@echo ""
	@echo "Available targets:"
	@printf "  %-22s %s\n" "setup" "Synchronize dependencies using uv sync."
	@printf "  %-22s %s\n" "test" "Run unit tests (excluding container integration tests)."
	@printf "  %-22s %s\n" "test-integration" "Build images and run integration test suite."
	@printf "  %-22s %s\n" "check" "Run all static checks (lint and lint-docs)."
	@printf "  %-22s %s\n" "lint" "Run Ruff linter and formatting check."
	@printf "  %-22s %s\n" "lint-docs" "Check Markdown documentation formatting with Prettier."
	@printf "  %-22s %s\n" "format" "Auto-format codebase and documentation (format-code and format-docs)."
	@printf "  %-22s %s\n" "format-code" "Auto-format codebase with Ruff."
	@printf "  %-22s %s\n" "format-docs" "Auto-format Markdown documentation with Prettier."
	@printf "  %-22s %s\n" "clean" "Remove transient build artifacts, caches, and logs."
	@printf "  %-22s %s\n" "distclean" "Clean transient artifacts and remove .venv virtual environment."
	@printf "  %-22s %s\n" "build-images" "Build all sandbox Docker images."
	@printf "  %-22s %s\n" "check-prerequisites" "Verify presence of required tools (uv, git, docker, npx)."
	@printf "  %-22s %s\n" "help" "Show this help message."
	@echo ""
	@echo "Options:"
	@printf "  %-22s %s\n" "PYTEST_ARGS" "Pass additional arguments to pytest (e.g. PYTEST_ARGS=\"-k test_name -v\")."
	@echo ""

setup:
	uv sync

test:
	uv run pytest -m "not integration_test" $(PYTEST_ARGS)

test-integration:
	./apps/sandbox-executor/build_all_images.sh --output-log
	uv run pytest -m "integration_test" $(PYTEST_ARGS)

check: lint lint-docs

lint:
	uv lock --check
	uv run ruff check .
	uv run ruff format --check .

lint-docs:
	npx --yes prettier@3.8.4 --check "**/*.md"

format: format-code format-docs

format-code:
	uv run ruff check --fix .
	uv run ruff format .

format-docs:
	npx --yes prettier@3.8.4 --write "**/*.md"

clean:
	find . -path "./.git" -prune -o -path "./.venv" -prune -o -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".ruff_cache" \) -exec rm -rf {} +
	rm -rf .coverage coverage.xml htmlcov apps/sandbox-executor/build_all_images.log build_all_images.log

distclean: clean
	rm -rf .venv

build-images:
	./apps/sandbox-executor/build_all_images.sh

check-prerequisites:
	@echo "Checking prerequisites on $(DETECTED_OS) ($(DETECTED_ARCH))..."
	@command -v git >/dev/null 2>&1 || (echo "❌ git not found" && exit 1)
	@command -v uv >/dev/null 2>&1 || (echo "❌ uv not found" && exit 1)
	@echo "✅ git and uv are installed."
	@if command -v npx >/dev/null 2>&1; then \
		echo "✅ npx is installed."; \
	else \
		echo "⚠️  npx not found (required for lint-docs and format-docs)."; \
	fi
	@if command -v docker >/dev/null 2>&1; then \
		echo "✅ docker is installed."; \
	else \
		echo "⚠️  docker not found (required only for container integration tests)."; \
	fi
