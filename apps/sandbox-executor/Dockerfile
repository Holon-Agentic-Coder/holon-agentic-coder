# Multi-stage Dockerfile for holon agent images
# For reference of coding agents https://github.com/bradAGI/awesome-cli-coding-agents/blob/main/README.md
# TODO: do we want to bake in the agents or decide at runtime? we can run agent at runtime by running `npx @anthropic-ai/claude-code`
# TODO: if we bake the agents in we need to rebuild the image everytime there is an update
# TODO: if we choose the agent at runtime we can choose certain version or latest at runtime
# Build targets: holon-base, agent-claude, agent-gemini, agent-opencode, agent-codex, agent-pi, agent-antigravity, holon-orchestrator

# The single uv pin for every image built from holon-base; override with --set *.args.UV_VERSION=<x.y.z>.
ARG UV_VERSION=0.13.7

# --- uv source (not a build target): the official distroless uv image, multi-arch and pinned ---
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

# --- Base Layer ---
FROM python:3.13-slim AS holon-base
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl jq build-essential ca-certificates vim shellcheck gpg && \
    mkdir -p -m 0755 /etc/apt/keyrings && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    chmod 0644 /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js v24.18.0 (multi-architecture support)
RUN dpkgArch="$(dpkg --print-architecture)" && \
    case "${dpkgArch##*-}" in \
      amd64) ARCH='x64';; \
      arm64) ARCH='arm64';; \
      *) echo "unsupported architecture"; exit 1 ;; \
    esac && \
    curl -fsSL "https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-${ARCH}.tar.gz" | tar -xz --strip-components=1 -C /usr/local && \
    npm install -g npm@11.18.0

# Bake in Prettier (the version formatting.py pins): the resolver's pre-push Markdown gate runs 'npx prettier', which
# then resolves offline instead of downloading it inside the container.
RUN npm install -g --no-audit --no-fund prettier@3.8.4

# Install the pinned uv on the PATH of every user; the resolver's pre-push verification runs 'uv run task ...'
COPY --from=uv-bin /uv /uvx /usr/local/bin/

# Create non-root user
RUN useradd -m holon
WORKDIR /home/holon

# Copy orchestrator entrypoints and package
COPY --chown=holon:holon src/ /home/holon/src/
COPY --chown=holon:holon entrypoint/role_dispatcher.sh /home/holon/entrypoint/role_dispatcher.sh

# Configure PYTHONPATH to make sandbox_executor package importable
ENV PYTHONPATH="/home/holon/src"

# Provide a lightweight entry that delegates to role-specific scripts
ENV LEDGER_PATH=/workspace/repo/holon/ledger
ENV DOCKER_CLI_HINTS=false

ENTRYPOINT ["/home/holon/entrypoint/role_dispatcher.sh"]

USER holon
ENV PATH="/home/holon/.local/bin:/home/holon/.local/node_modules/.bin:${PATH}"
RUN mkdir -p /home/holon/.cache
# Build-time smoke check as the holon user: an image without a working uv fails to build
RUN uv --version

# --- Claude Agent Layer ---
FROM holon-base AS agent-claude
RUN --mount=type=cache,id=npm-claude,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@anthropic-ai/claude-code" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @anthropic-ai/claude-code@2.1.302

# --- Gemini Agent Layer ---
FROM holon-base AS agent-gemini
RUN --mount=type=cache,id=npm-gemini,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@github/keytar,node-pty" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @google/gemini-cli@0.64.0

# --- OpenCode Agent Layer (opencode) ---
FROM holon-base AS agent-opencode
RUN --mount=type=cache,id=npm-opencode,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=opencode-ai" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund opencode-ai@1.19.3

# --- Pi Agent Layer ---
FROM holon-base AS agent-pi
RUN --mount=type=cache,id=npm-pi,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@google/genai,protobufjs" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @earendil-works/pi-coding-agent@1.3.0

# --- Codex Agent Layer ---
FROM holon-base AS agent-codex
RUN --mount=type=cache,id=npm-codex,uid=1000,gid=1000,target=/home/holon/.npm \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @openai/codex@0.163.0

# --- Antigravity Agent Layer ---
FROM holon-base AS agent-antigravity
RUN --mount=type=cache,id=uv-antigravity,uid=1000,gid=1000,target=/home/holon/.cache/uv \
    --mount=type=cache,id=npm-antigravity,uid=1000,gid=1000,target=/home/holon/.npm \
    curl --proto '=https' --tlsv1.2 -fsSL --compressed https://antigravity.google/cli/install.sh -o /tmp/install.sh && bash /tmp/install.sh && rm -f /tmp/install.sh

# --- Final Orchestrator Image ---
FROM holon-base AS holon-orchestrator
