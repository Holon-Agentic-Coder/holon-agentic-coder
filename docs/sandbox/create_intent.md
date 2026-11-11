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
2. Configured git access to GitHub on your host machine. The host fetches and pushes with its own credentials; the
   container never contacts the remote.

---

## 1. Prepare the Intent JSON File

Create a JSON file on your host machine (e.g., `intent.json`) representing the metadata for the intent you wish to
create.

### JSON Schema

- **`branch`** (string, optional): The intent's base name (e.g., `I-1771890389-refactor-metrics`), without the `/_`
  leaf; the intent branch itself is `{branch}/_`. If omitted, it will be automatically generated on the fly as
  `I-{timestamp}-{slug}`.
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
> credentials and API keys, and the flow's host worktree, mounting them safely into the sandboxed container.

You can run the Intent Creator via any of the following standard pathways:

### Option A: Globally Installed Tool (`uv tool`)

If installed globally via `uv tool install ./apps/sandbox-executor` (from repository root) or
`uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"`
(can pin `@main`, release `@<tag>`, or immutable `@<commit-sha>`):

```bash
holon intent intents/intent.json
```

### Option B: Ephemeral Execution (`uvx`)

Run on-demand without prior installation:

> [!WARNING] **Always specify `--from` when invoking via `uvx`.** Do not execute bare `uvx holon`. The package name
> `holon` is registered on public PyPI by an unrelated third-party project; omitting `--from` introduces a dependency
> confusion and namespace collision risk where `uvx` will attempt to download and run the third-party PyPI package.
> Additionally, local path invocations (`uvx --from ./apps/sandbox-executor`) must be run from the repository root.

```bash
# Run from local repository clone (must be run from repository root)
uvx --from ./apps/sandbox-executor holon intent intents/intent.json

# Or directly from remote Git repository (can pin @main, release @<tag>, or immutable @<commit-sha>)
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

```bash
docker run --rm \
  -e HOLON_ROLE=intent-creator \
  -v "$PWD/intents/intent.json:/tmp/intent.json" \
  -v <worktree>:/home/holon/.holon-sandbox/workspace \
  -e HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace \
  holon/orchestrator
```

The container never runs git against a remote. The host, with its own git credentials, fetches the base before the
container starts and pushes the committed branch after it returns, so no SSH key or agent is needed for the container.

### Argument Breakdown:

- **`-e HOLON_ROLE=intent-creator`**: Routes the entrypoint call to the Python intent creator module inside the
  container.
- **`-v /path/to/intent.json:/tmp/intent.json`**: Mounts your host-defined JSON file into the container at the expected
  path.
- **`-v <worktree>:/home/holon/.holon-sandbox/workspace`** and
  **`-e HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace`**: Bind-mounts the flow's host-provisioned
  `flow-<slug>` worktree at the sandbox workspace path. The intent creator works in place there and only commits; it
  never clones, fetches or pushes. The host fetches the base before the container starts and pushes the committed branch
  after it returns, using the host's own git credentials. Because no git command inside the container contacts a remote,
  no `ssh-agent`, SSH key or credential-helper is mounted or needed.

---

## 3. Verification

Once execution completes, you should verify:

1. A new remote branch has been created: `I-1771890389-refactor-metrics/_`.
2. The intent is appended to `holon-knowledge/ledger/intents.jsonl` on that branch with status `"proposed"` and a UTC
   `"created_at"` timestamp.
