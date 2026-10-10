.DEFAULT_GOAL := help

# OS and Architecture Detection
DETECTED_OS := $(shell uname -s)
DETECTED_ARCH := $(shell uname -m)

# CI Detection
CI ?= false

# Automated prerequisite installation (set to false in check-prerequisites for read-only probe)
ifeq ($(CI),true)
AUTO_INSTALL ?= false
else
AUTO_INSTALL ?= true
endif

# Terminal Colors (disabled in CI or when NO_COLOR is set)
ifeq ($(CI),true)
COLOR_BOLD :=
COLOR_GREEN :=
COLOR_RED :=
COLOR_YELLOW :=
COLOR_RESET :=
else ifdef NO_COLOR
COLOR_BOLD :=
COLOR_GREEN :=
COLOR_RED :=
COLOR_YELLOW :=
COLOR_RESET :=
else
COLOR_BOLD := \033[1m
COLOR_GREEN := \033[1;32m
COLOR_RED := \033[1;31m
COLOR_YELLOW := \033[1;33m
COLOR_RESET := \033[0m
endif

.PHONY: help build-images check-prerequisites prerequisites check-docker install-docker install-homebrew

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@printf "  %-22s %s\n" "build-images" "Build all sandbox Docker images."
	@printf "  %-22s %s\n" "check-prerequisites" "Verify development prerequisites (fatal: uv, gh, openssl; advisory: GNU Make, npx, gh auth, Docker CLI/Buildx/daemon)."
	@printf "  %-22s %s\n" "check-docker" "Check Docker installation, Buildx, and daemon running status."
	@printf "  %-22s %s\n" "install-docker" "Install Docker for the detected operating system."
	@printf "  %-22s %s\n" "install-homebrew" "Install Homebrew (macOS only)."
	@printf "  %-22s %s\n" "help" "Show this help message."
	@echo ""

build-images:
	./apps/sandbox-executor/build_all_images.sh

## ==============================================================================
## Docker & Prerequisite Installation & Checks
## ==============================================================================

# Install Homebrew if not installed (macOS)
install-homebrew:
	@if [ "$(DETECTED_OS)" = "Darwin" ]; then \
		if [ -x /opt/homebrew/bin/brew ]; then eval "$$(/opt/homebrew/bin/brew shellenv)"; fi; \
		if ! command -v brew >/dev/null 2>&1; then \
			echo "Homebrew not found. Installing Homebrew..."; \
			NONINTERACTIVE=1 /bin/bash -c "$$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || exit 1; \
			if [ -x /opt/homebrew/bin/brew ]; then eval "$$(/opt/homebrew/bin/brew shellenv)"; fi; \
		else \
			echo "$(COLOR_GREEN)✅ Homebrew is already installed.$(COLOR_RESET)"; \
		fi; \
	fi

# Install Docker based on operating system
install-docker:
	@echo "$(COLOR_BOLD)Checking Docker installation for $(DETECTED_OS)...$(COLOR_RESET)"
	@if [ -n "$(findstring n,$(foreach w,$(MAKEFLAGS),$(if $(findstring =,$(w)),,$(filter-out --%,$(w)))))" ]; then exit 0; \
	elif command -v docker >/dev/null 2>&1; then \
		echo "$(COLOR_GREEN)✅ Docker is already installed: $$(docker --version)$(COLOR_RESET)"; \
	else \
		echo "$(COLOR_YELLOW)⚠️  Docker not found. Installing Docker for $(DETECTED_OS)...$(COLOR_RESET)"; \
		if [ "$(DETECTED_OS)" = "Darwin" ]; then \
			if [ -x /opt/homebrew/bin/brew ]; then eval "$$(/opt/homebrew/bin/brew shellenv)"; fi; \
			if ! command -v brew >/dev/null 2>&1; then \
				$(MAKE) install-homebrew || exit 1; \
				if [ -x /opt/homebrew/bin/brew ]; then eval "$$(/opt/homebrew/bin/brew shellenv)"; fi; \
			fi; \
			echo "Detected macOS - installing Docker Desktop via Homebrew..."; \
			brew install --cask docker || exit 1; \
			echo "$(COLOR_GREEN)✅ Docker installed successfully.$(COLOR_RESET)"; \
			echo "Please open Docker Desktop to complete initialization."; \
		elif [ "$(DETECTED_OS)" = "Linux" ]; then \
			SUDO=$$([ "$$(id -u)" -ne 0 ] && echo "sudo" || true); \
			echo "Detected Linux - installing Docker via get.docker.com..."; \
			TMP_SCRIPT=$$(mktemp /tmp/get-docker-XXXXXX.sh); \
			trap 'rm -f "$$TMP_SCRIPT"' EXIT INT TERM; \
			curl -fsSL https://get.docker.com -o "$$TMP_SCRIPT" || { echo "$(COLOR_RED)❌ Failed to download Docker installer$(COLOR_RESET)"; exit 1; }; \
			$$SUDO sh "$$TMP_SCRIPT" || { echo "$(COLOR_RED)❌ Docker installation failed$(COLOR_RESET)"; exit 1; }; \
			if command -v systemctl >/dev/null 2>&1; then \
				$$SUDO systemctl enable --now docker 2>/dev/null || true; \
			elif command -v service >/dev/null 2>&1; then \
				$$SUDO service docker start 2>/dev/null || true; \
			fi; \
			$$SUDO usermod -aG docker "$${USER:-$$(id -un)}" 2>/dev/null || true; \
			echo "$(COLOR_GREEN)✅ Docker installed successfully.$(COLOR_RESET)"; \
			echo "⚠️  Please log out and log back in, or run newgrp docker to use Docker without sudo."; \
		else \
			echo "$(COLOR_RED)❌ Unsupported operating system: $(DETECTED_OS)$(COLOR_RESET)"; \
			exit 1; \
		fi; \
	fi

# Check Docker prerequisite (CLI, buildx, daemon)
check-docker:
	@if [ -n "$(findstring n,$(foreach w,$(MAKEFLAGS),$(if $(findstring =,$(w)),,$(filter-out --%,$(w)))))" ]; then exit 0; fi; \
	ERRORS=0; \
	printf "%-32s " "Checking Docker CLI..."; \
	if ! command -v docker >/dev/null 2>&1; then \
		if [ "$(AUTO_INSTALL)" = "true" ]; then \
			echo "$(COLOR_YELLOW)⚠️  Docker CLI not found. Installing Docker...$(COLOR_RESET)"; \
			$(MAKE) install-docker || { echo "$(COLOR_RED)❌ Docker installation failed$(COLOR_RESET)"; ERRORS=$$((ERRORS + 1)); }; \
		fi; \
	fi; \
	if command -v docker >/dev/null 2>&1; then \
		DOCKER_VER=$$(docker --version 2>/dev/null); \
		echo "$(COLOR_GREEN)✅ Found: $$DOCKER_VER$(COLOR_RESET)"; \
	else \
		echo "$(COLOR_RED)❌ Missing: Docker CLI not found$(COLOR_RESET)"; \
		echo "   Run $(COLOR_BOLD)make install-docker$(COLOR_RESET) to install."; \
		exit 1; \
	fi; \
	\
	printf "%-32s " "Checking Docker Buildx..."; \
	if docker buildx version >/dev/null 2>&1; then \
		BUILDX_VER=$$(docker buildx version 2>/dev/null); \
		echo "$(COLOR_GREEN)✅ Found: $$BUILDX_VER$(COLOR_RESET)"; \
	else \
		if [ "$(AUTO_INSTALL)" = "true" ]; then \
			echo "$(COLOR_YELLOW)⚠️  Docker Buildx plugin not found. Attempting installation...$(COLOR_RESET)"; \
			if [ "$(DETECTED_OS)" = "Darwin" ]; then \
				if [ -x /opt/homebrew/bin/brew ]; then eval "$$(/opt/homebrew/bin/brew shellenv)"; fi; \
				if command -v brew >/dev/null 2>&1; then \
					brew install docker-buildx 2>/dev/null || true; \
				fi; \
			elif [ "$(DETECTED_OS)" = "Linux" ]; then \
				SUDO=$$([ "$$(id -u)" -ne 0 ] && echo "sudo" || true); \
				if command -v apt-get >/dev/null 2>&1; then \
					$$SUDO apt-get update -qq && $$SUDO apt-get install -y docker-buildx-plugin 2>/dev/null || true; \
				elif command -v dnf >/dev/null 2>&1; then \
					$$SUDO dnf install -y docker-buildx-plugin 2>/dev/null || true; \
				elif command -v pacman >/dev/null 2>&1; then \
					$$SUDO pacman -S --noconfirm docker-buildx 2>/dev/null || true; \
				fi; \
			fi; \
			if docker buildx version >/dev/null 2>&1; then \
				BUILDX_VER=$$(docker buildx version 2>/dev/null); \
				echo "$(COLOR_GREEN)✅ Found: $$BUILDX_VER$(COLOR_RESET)"; \
			else \
				echo "$(COLOR_RED)❌ Missing: Docker Buildx plugin not found$(COLOR_RESET)"; \
				ERRORS=$$((ERRORS + 1)); \
			fi; \
		else \
			echo "$(COLOR_RED)❌ Missing: Docker Buildx plugin not found$(COLOR_RESET)"; \
			echo "   Run $(COLOR_BOLD)make check-docker$(COLOR_RESET) to install."; \
			ERRORS=$$((ERRORS + 1)); \
		fi; \
	fi; \
	\
	printf "%-32s " "Checking Docker daemon status..."; \
	if docker info >/dev/null 2>&1; then \
		echo "$(COLOR_GREEN)✅ Running$(COLOR_RESET)"; \
	else \
		if [ "$(AUTO_INSTALL)" = "true" ]; then \
			echo "$(COLOR_YELLOW)⚠️  Docker daemon stopped. Attempting to start...$(COLOR_RESET)"; \
			if [ "$(DETECTED_OS)" = "Darwin" ]; then \
				open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true; \
			else \
				if command -v systemctl >/dev/null 2>&1; then \
					sudo systemctl start docker 2>/dev/null || true; \
				elif command -v service >/dev/null 2>&1; then \
					sudo service docker start 2>/dev/null || true; \
				fi; \
			fi; \
			for i in $$(seq 1 35); do \
				if docker info >/dev/null 2>&1; then \
					break; \
				fi; \
				sleep 1; \
			done; \
			if docker info >/dev/null 2>&1; then \
				echo "$(COLOR_GREEN)✅ Docker daemon running$(COLOR_RESET)"; \
			else \
				echo "$(COLOR_RED)❌ Stopped: Docker daemon is not running$(COLOR_RESET)"; \
				if [ "$(DETECTED_OS)" = "Darwin" ]; then \
					echo "   Please start Docker Desktop manually (open -a Docker)"; \
				else \
					echo "   Please start Docker daemon: sudo systemctl start docker"; \
				fi; \
				ERRORS=$$((ERRORS + 1)); \
			fi; \
		else \
			echo "$(COLOR_RED)❌ Stopped: Docker daemon is not running$(COLOR_RESET)"; \
			if [ "$(DETECTED_OS)" = "Darwin" ]; then \
				echo "   Please start Docker Desktop manually (open -a Docker) or run $(COLOR_BOLD)make check-docker$(COLOR_RESET)."; \
			else \
				echo "   Please start Docker daemon: sudo systemctl start docker or run $(COLOR_BOLD)make check-docker$(COLOR_RESET)."; \
			fi; \
			ERRORS=$$((ERRORS + 1)); \
		fi; \
	fi; \
	if [ $$ERRORS -gt 0 ]; then \
		exit 1; \
	fi

# Check all system prerequisites
check-prerequisites:
	@echo "$(COLOR_BOLD)====================================================$(COLOR_RESET)"
	@echo "$(COLOR_BOLD) Checking Prerequisites for holon-agentic-coder$(COLOR_RESET)"
	@echo "$(COLOR_BOLD) OS: $(DETECTED_OS) | Architecture: $(DETECTED_ARCH)$(COLOR_RESET)"
	@echo "$(COLOR_BOLD)====================================================$(COLOR_RESET)"
	@if [ -n "$(findstring n,$(foreach w,$(MAKEFLAGS),$(if $(findstring =,$(w)),,$(filter-out --%,$(w)))))" ]; then exit 0; fi; \
	ERRORS=0; \
	WARNINGS=0; \
	printf "%-32s " "Checking GNU Make..."; \
	MAKE_OUT="$$($(MAKE) --version 2>/dev/null | head -n 1 || true)"; \
	if echo "$$MAKE_OUT" | grep -q "GNU Make"; then \
		echo "$(COLOR_GREEN)✅ Found: $$MAKE_OUT$(COLOR_RESET)"; \
	else \
		echo "$(COLOR_YELLOW)⚠️  Found non-GNU make: $$MAKE_OUT$(COLOR_RESET)"; \
		echo "   GNU Make >= 3.81 is recommended."; \
		WARNINGS=$$((WARNINGS + 1)); \
	fi; \
	\
	printf "%-32s " "Checking uv..."; \
	if command -v uv >/dev/null 2>&1; then \
		UV_VER=$$(uv --version 2>/dev/null || true); \
		echo "$(COLOR_GREEN)✅ Found: $$UV_VER$(COLOR_RESET)"; \
	else \
		echo "$(COLOR_RED)❌ Missing: uv not found$(COLOR_RESET)"; \
		echo "   Install uv via: curl -LsSf https://astral.sh/uv/install.sh | sh"; \
		ERRORS=$$((ERRORS + 1)); \
	fi; \
	\
	printf "%-32s " "Checking npx (Prettier)..."; \
	if command -v npx >/dev/null 2>&1; then \
		NPX_VER=$$(npx --version 2>/dev/null || true); \
		echo "$(COLOR_GREEN)✅ Found: npx v$$NPX_VER$(COLOR_RESET)"; \
	else \
		echo "$(COLOR_YELLOW)⚠️  Missing: npx not found$(COLOR_RESET)"; \
		echo "   npx is required for 'uv run task lint-docs' and 'uv run task format-docs' (Prettier)."; \
		WARNINGS=$$((WARNINGS + 1)); \
	fi; \
	\
	printf "%-32s " "Checking GitHub CLI (gh)..."; \
	if command -v gh >/dev/null 2>&1; then \
		GH_VER=$$(gh --version 2>/dev/null | head -n 1 || true); \
		if [ -z "$$GH_VER" ]; then \
			echo "$(COLOR_RED)❌ Unusable: gh is on PATH but 'gh --version' produced no output$(COLOR_RESET)"; \
			echo "   A binary that cannot report its version cannot be trusted to resolve PR refs; reinstall it, or repair the PATH entry shadowing it."; \
			ERRORS=$$((ERRORS + 1)); \
		else \
			echo "$(COLOR_GREEN)✅ Found: $$GH_VER$(COLOR_RESET)"; \
			if ! gh auth status >/dev/null 2>&1; then \
				echo "$(COLOR_YELLOW)⚠️  gh is not authenticated. Run 'gh auth login' to authenticate.$(COLOR_RESET)"; \
				WARNINGS=$$((WARNINGS + 1)); \
			fi; \
		fi; \
	else \
		echo "$(COLOR_RED)❌ Missing: GitHub CLI (gh) not found$(COLOR_RESET)"; \
		echo "   Needed to resolve Pull Request refs during the flow; 'gh auth token' is the fallback when GITHUB_TOKEN/GH_TOKEN are unset."; \
		if [ "$(DETECTED_OS)" = "Darwin" ]; then \
			echo "   Install gh via: brew install gh"; \
		else \
			echo "   Install gh via: sudo apt install gh (see https://github.com/cli/cli#installation for the apt repository)"; \
		fi; \
		ERRORS=$$((ERRORS + 1)); \
	fi; \
	\
	printf "%-32s " "Checking OpenSSL..."; \
	if command -v openssl >/dev/null 2>&1; then \
		OPENSSL_VER=$$(openssl version 2>/dev/null || true); \
		if [ -z "$$OPENSSL_VER" ]; then \
			echo "$(COLOR_RED)❌ Unusable: openssl is on PATH but 'openssl version' produced no output$(COLOR_RESET)"; \
			echo "   ca_generator.py cannot mint or read certificates through a build that cannot report its version; reinstall it, or repair the PATH entry shadowing it."; \
			ERRORS=$$((ERRORS + 1)); \
		else \
			echo "$(COLOR_GREEN)✅ Found: $$OPENSSL_VER$(COLOR_RESET)"; \
			case "$$OPENSSL_VER" in \
				OpenSSL*) : ;; \
				*) \
					echo "$(COLOR_YELLOW)⚠️  Not OpenSSL: the Root CA step needs it (ca_generator.py passes -addext, which LibreSSL does not support), so this build may still fail.$(COLOR_RESET)"; \
					echo "   Install real OpenSSL — on macOS: brew install openssl; on Debian/Ubuntu: sudo apt install openssl.$(COLOR_RESET)"; \
					WARNINGS=$$((WARNINGS + 1)); \
					;; \
			esac; \
		fi; \
	else \
		echo "$(COLOR_RED)❌ Missing: openssl not found$(COLOR_RESET)"; \
		echo "   OpenSSL is required for token reduction Root CA generation (ca_generator.py)."; \
		if [ "$(DETECTED_OS)" = "Darwin" ]; then \
			echo "   Install OpenSSL via: brew install openssl"; \
		else \
			echo "   Install OpenSSL via: sudo apt install openssl"; \
		fi; \
		ERRORS=$$((ERRORS + 1)); \
	fi; \
	\
	$(MAKE) check-docker AUTO_INSTALL=false || WARNINGS=$$((WARNINGS + 1)); \
	\
	echo "$(COLOR_BOLD)====================================================$(COLOR_RESET)"; \
	if [ $$ERRORS -gt 0 ]; then \
		echo "$(COLOR_RED)❌ $$ERRORS critical prerequisite(s) missing. Please install the required tools above.$(COLOR_RESET)"; \
		exit 1; \
	elif [ $$WARNINGS -gt 0 ]; then \
		echo "$(COLOR_YELLOW)⚠️  Core prerequisites satisfied, but $$WARNINGS optional/advisory check(s) raised warnings.$(COLOR_RESET)"; \
		exit 0; \
	else \
		echo "$(COLOR_GREEN)✅ All prerequisites are satisfied!$(COLOR_RESET)"; \
	fi

# Alias for check-prerequisites
prerequisites: check-prerequisites
