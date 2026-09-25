.DEFAULT_GOAL := help

# OS and Architecture Detection
DETECTED_OS := $(shell uname -s)
DETECTED_ARCH := $(shell uname -m)

# CI Detection
CI ?= false

.PHONY: help setup test test-integration lint format clean build-images check-prerequisites

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@printf "  %-22s %s\n" "setup" "Synchronize dependencies using uv sync."
	@printf "  %-22s %s\n" "test" "Run unit tests (excluding container integration tests)."
	@printf "  %-22s %s\n" "test-integration" "Build images and run integration test suite."
	@printf "  %-22s %s\n" "lint" "Run Ruff linter and formatting check."
	@printf "  %-22s %s\n" "format" "Auto-format codebase with Ruff."
	@printf "  %-22s %s\n" "clean" "Remove transient build artifacts, caches, and virtualenvs."
	@printf "  %-22s %s\n" "build-images" "Build all sandbox Docker images."
	@printf "  %-22s %s\n" "check-prerequisites" "Verify presence of required tools (uv, git, docker)."
	@printf "  %-22s %s\n" "help" "Show this help message."
	@echo ""

setup:
	uv sync

test:
	uv run pytest -m "not integration_test"

test-integration:
	./apps/sandbox-executor/build_all_images.sh --output-log
	uv run pytest -m "integration_test"

lint:
	uv lock --check
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

clean:
	find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".ruff_cache" -o -name "*.egg-info" -o -name "build" -o -name "dist" \) -not -path "*/.venv/*" -exec rm -rf {} +
	rm -rf .venv

build-images:
	./apps/sandbox-executor/build_all_images.sh

check-prerequisites:
	@echo "Checking prerequisites on $(DETECTED_OS) ($(DETECTED_ARCH))..."
	@command -v git >/dev/null 2>&1 || (echo "❌ git not found" && exit 1)
	@command -v uv >/dev/null 2>&1 || (echo "❌ uv not found" && exit 1)
	@echo "✅ git and uv are installed."
	@if command -v docker >/dev/null 2>&1; then \
		echo "✅ docker is installed."; \
	else \
		echo "⚠️  docker not found (required only for container integration tests)."; \
	fi
