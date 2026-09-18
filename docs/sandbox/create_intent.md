# Running Intent Creation via Docker

This guide explains how to run the Intent Creation role using the Docker sandbox environment.

The Intent Creator initializes a new development branch, appends the intent to the append-only ledger
`holon-knowledge/ledger/intents.jsonl`, and pushes the branch to the remote repository.

---

## Prerequisites

Before running the command, ensure you have:

1. Built the Docker images using the build script:
   ```bash
   ./apps/sandbox-executor/build_all_images.sh
   ```
2. Set up SSH keys on your host machine configured for GitHub access.
3. Configured SSH keys and ensured your local SSH agent is running and has your keys loaded (i.e., `ssh-add -l` displays
   your key), as SSH Agent Forwarding inside the container relies on host authentication.

---

## 1. Prepare the Intent JSON File

Create a JSON file on your host machine (e.g., `intent.json`) representing the metadata for the intent you wish to
create.

### JSON Schema

- **`branch`** (string, optional): The target branch name (e.g., `I-1771890389-refactor-metrics`). If omitted, it will
  be automatically generated on the fly as `I-{timestamp}-{slug}`.
- **`slug`** (string, required if `branch` is omitted): A short URL-safe identifier for the task.
- **`description`** (string, optional): A high-level description of what changes are being made.
- **`goal`** (string, optional): The target objective or test criteria.
- **`target_branch`** (string, optional): The base branch to check out and fork the new branch from (e.g. `develop`). If
  omitted, defaults to `main`.

### Example (`intent.json`)

```json
{
  "branch": "I-1771890389-refactor-metrics",
  "slug": "refactor-metrics",
  "description": "Clean up metrics estimators and configuration structure",
  "goal": "Refactor local metrics calculations to reduce entropy",
  "target_branch": "develop"
}
```

---

## Recommended Execution Methods (Holon CLI)

> [!IMPORTANT] **Use the Holon CLI instead of raw `docker run` commands.** The CLI automatically discovers GitHub
> credentials, API keys, and host SSH agent sockets, mounting them safely into the sandboxed container.

You can run the Intent Creator via any of the following standard pathways:

### Option A: Globally Installed Tool (`uv tool`)

If installed globally via `uv tool install apps/sandbox-executor` (or
`uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"`):

```bash
holon intent intents/intent.json
```

### Option B: Ephemeral Execution (`uvx`)

Run on-demand without prior installation:

```bash
# Run from local repository clone
uvx --from ./apps/sandbox-executor holon intent intents/intent.json

# Or directly from remote Git repository
uvx --from "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor" holon intent intents/intent.json
```

### Option C: Repository Convenience Script (`./holon`)

Run from the repository root using the [`./holon`](../../holon) host wrapper:

```bash
./holon intent intents/intent.json
```

---

## Low-Level Execution (Manual `docker run`)

If you need to invoke Docker manually, run the following command, replacing the volume mount path with the path to the
JSON file you created.

> [!TIP] Use `"$PWD"` for mounting the volume to ensure compatibility across Bash, Zsh, and Fish shells. Avoid using
> `$(pwd)` in Fish shell as command substitution does not evaluate inside double quotes.

### For macOS (Docker Desktop)

```bash
docker run --rm \
  -e HOLON_ROLE=intent-creator \
  -v "$PWD/intents/intent.json:/tmp/intent.json" \
  -v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock \
  -e SSH_AUTH_SOCK=/run/host-services/ssh-auth.sock \
  holon/orchestrator
```

### For Linux

> [!IMPORTANT] Ensure your `SSH_AUTH_SOCK` environment variable is set and non-empty (e.g., `echo $SSH_AUTH_SOCK` shows
> a path) before running this command. If it is empty, the bind mount `-v "$SSH_AUTH_SOCK:/run/ssh-agent"` will resolve
> to a syntax error.

```bash
docker run --rm \
  -e HOLON_ROLE=intent-creator \
  -v "$PWD/intents/intent.json:/tmp/intent.json" \
  -v "$SSH_AUTH_SOCK:/run/ssh-agent" \
  -e SSH_AUTH_SOCK=/run/ssh-agent \
  holon/orchestrator
```

### Argument Breakdown:

- **`-e HOLON_ROLE=intent-creator`**: Routes the entrypoint call to the Python intent creator module inside the
  container.
- **`-v /path/to/intent.json:/tmp/intent.json`**: Mounts your host-defined JSON file into the container at the expected
  path.
- **`-v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock` / `-v "$SSH_AUTH_SOCK:/run/ssh-agent"`**:
  Mounts the host's SSH agent socket into the container to enable SSH Agent Forwarding. This allows the container to
  authenticate using your host's SSH credentials securely without exposing private key files directly to the container.
  (The container automatically configures `StrictHostKeyChecking=no` internally).

---

## 3. Verification

Once execution completes, you should verify:

1. A new remote branch has been created: `I-1771890389-refactor-metrics/_`.
2. The intent is appended to `holon-knowledge/ledger/intents.jsonl` on that branch with status `"proposed"` and a UTC
   `"created_at"` timestamp.
