# Multi-stage Dockerfile for holon agent images
# For reference of coding agents https://github.com/bradAGI/awesome-cli-coding-agents/blob/main/README.md
# TODO: do we want to bake in the agents or decide at runtime? we can run agent at runtime by running `npx @anthropic-ai/claude-code`
# TODO: if we bake the agents in we need to rebuild the image everytime there is an update
# TODO: if we choose the agent at runtime we can choose certain version or latest at runtime
# Build targets: holon-base, agent-claude, agent-gemini, agent-opencode, agent-codex, agent-pi, agent-antigravity, holon-orchestrator

# --- Base Layer ---
FROM python:3.13-slim AS holon-base
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl jq build-essential ca-certificates ssh vim && \
    rm -rf /var/lib/apt/lists/*

# Configure SSH globally inside the container to automatically accept host keys
# NOTE: Disabling StrictHostKeyChecking globally is acceptable for throwaway sandboxes.
# If this image is reused in a persistent or production environment, it should be
# hardened by pinning known hosts instead of disabling checking entirely.
RUN mkdir -p /etc/ssh && \
    printf "\nHost *\n    StrictHostKeyChecking no\n    UserKnownHostsFile /dev/null\n" >> /etc/ssh/ssh_config

# Install Node.js v24.18.0 (multi-architecture support)
RUN dpkgArch="$(dpkg --print-architecture)" && \
    case "${dpkgArch##*-}" in \
      amd64) ARCH='x64';; \
      arm64) ARCH='arm64';; \
      *) echo "unsupported architecture"; exit 1 ;; \
    esac && \
    curl -fsSL "https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-${ARCH}.tar.gz" | tar -xz --strip-components=1 -C /usr/local && \
    npm install -g npm@11.18.0

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

ENTRYPOINT ["/home/holon/entrypoint/role_dispatcher.sh"]

USER holon
ENV PATH="/home/holon/.local/bin:/home/holon/.local/node_modules/.bin:${PATH}"
RUN mkdir -p /home/holon/.cache

# --- Claude Agent Layer ---
FROM holon-base AS agent-claude
RUN --mount=type=cache,id=npm-claude,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@anthropic-ai/claude-code" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @anthropic-ai/claude-code@2.1.247

# --- Gemini Agent Layer ---
FROM holon-base AS agent-gemini
RUN --mount=type=cache,id=npm-gemini,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@github/keytar,node-pty" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @google/gemini-cli@0.57.0

# --- OpenCode Agent Layer (opencode) ---
FROM holon-base AS agent-opencode
RUN --mount=type=cache,id=npm-opencode,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=opencode-ai" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund opencode-ai@1.18.23

# --- Pi Agent Layer ---
FROM holon-base AS agent-pi
RUN --mount=type=cache,id=npm-pi,uid=1000,gid=1000,target=/home/holon/.npm \
    echo "allow-scripts=@google/genai,protobufjs" > /home/holon/.npmrc && \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @earendil-works/pi-coding-agent@0.84.3

# --- Codex Agent Layer ---
FROM holon-base AS agent-codex
RUN --mount=type=cache,id=npm-codex,uid=1000,gid=1000,target=/home/holon/.npm \
    npm install --prefix /home/holon/.local --prefer-offline --no-audit --no-fund @openai/codex@0.150.1

# --- Antigravity Agent Layer ---
FROM holon-base AS agent-antigravity
RUN --mount=type=cache,id=uv-antigravity,uid=1000,gid=1000,target=/home/holon/.cache/uv \
    --mount=type=cache,id=npm-antigravity,uid=1000,gid=1000,target=/home/holon/.npm \
    curl -fsSL --compressed https://antigravity.google/cli/install.sh | bash

# --- Final Orchestrator Image ---
FROM holon-base AS holon-orchestrator
