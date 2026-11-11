# Intents Directory

This directory is used for drafting local intent configuration files (`*.json`) on your host machine before executing
them in the sandboxed Docker environment.

> [!NOTE] This directory is added to `.gitignore` to prevent draft configurations from being committed. The official,
> permanent ledger of executed intents is automatically saved and committed inside the repository at
> `holon-knowledge/ledger/intents.jsonl`.

## Schema for `intent.json`

Every intent configuration file must be a JSON object with the following fields:

- **`branch`** (string, optional): The target branch name (e.g., `I-1771890389-refactor-metrics`). If omitted, it will
  be automatically generated on the fly as `I-{timestamp}-{slug}`.
- **`slug`** (string, required if `branch` is omitted): A short URL-safe identifier for the task.
- **`description`** (string, optional): A high-level description of what changes are being made.
- **`goal`** (string, optional): The target objective or test criteria.
- **`target_branch`** (string, optional): The base branch to check out and fork the new branch from (e.g. `develop`). If
  omitted, defaults to `main`.

### Example (`intents/refactor-metrics.json`)

```json
{
  "slug": "refactor-metrics",
  "description": "Clean up metrics estimators and configuration structure",
  "goal": "Refactor local metrics calculations to reduce entropy",
  "target_branch": "develop"
}
```

## How to Run

> [!IMPORTANT] **Use `./holon` instead of raw `docker run` commands.** Always execute sandbox environments using the
> `./holon` host CLI script from the repository root:

```bash
./holon intent intents/your-intent-file.json
```

The `./holon` script automatically handles credential discovery, volume mounts, and container routing.

---

### Low-Level Manual Docker Execution

The container never reaches the git remote: the host fetches the base before the container starts and pushes the
committed branch after it returns, using the host's own git credentials, so no SSH agent socket, SSH key or credential
helper is mounted or needed. Prefer `./holon intent` (above), which performs those host-side remote operations. A bare
`docker run` only commits inside the mounted worktree and publishes nothing; it needs the intent file and the flow's
host `flow-<slug>` worktree mounted (see
[create_intent.md](../docs/sandbox/create_intent.md#low-level-execution-manual-docker-run) for the full argument
breakdown):

```bash
docker run --rm \
  -e HOLON_ROLE=intent-creator \
  -v "$PWD/intents/intent.json:/tmp/intent.json" \
  -v <worktree>:/home/holon/.holon-sandbox/workspace \
  -e HOLON_TARGET_WORKTREE=/home/holon/.holon-sandbox/workspace \
  holon/orchestrator
```

---

## Nested JSON Limitation & Workarounds

When defining intents that require structured JSON or code blocks inside fields like `goal` or `description`, nesting
raw JSON inside a JSON string is syntactically invalid (it must be escaped as a string), leading to ugly escaping
friction (e.g. `\"`).

Here are the standard workarounds:

### Workaround 1: YAML Drafts (Recommended)

You can write your local drafts in YAML (`intent.yaml`), which natively supports clean, unescaped multi-line strings and
nested structures:

```yaml
slug: "config-setup"
goal: |
  Deploy the naive config:
  {
    "port": 8080,
    "debug": true
  }
```

_Note: If using this workaround, the host CLI wrapper converts the YAML config to `/tmp/intent.json` on the fly before
mounting it into the container._

### Workaround 2: External File Reference

Store the nested JSON in a separate file (e.g., `intents/payload.json`) and reference its path:

```json
{
  "slug": "config-setup",
  "goal": "Deploy configuration defined in external file",
  "payload_ref": "./payload.json"
}
```

### Workaround 3: Base64 Encoding

Encode the inner JSON string into Base64 to bypass all syntax and character escaping constraints:

```json
{
  "slug": "config-setup",
  "goal": "eyogICJwb3J0IjogODA4MCwKICAiZGVidWciOiB0cnVlCn0="
}
```

### Workaround 4: Standard String Escaping

For very simple payloads, use escaped strings:

```json
{
  "slug": "config-setup",
  "goal": "Deploy config: {\"port\": 8080, \"debug\": true}"
}
```
