# Running Plan Generation via Docker

This guide explains how to run the Plan Generation role using the Docker sandbox environment.

The Planner initializes a new plan branch off of the active intent branch, compiles a prompt using system templates and
intent metadata, runs the AI agent (e.g.,`pi`) to write a structured markdown plan, extracts predicted metrics, logs
them to the plans ledger `holon-knowledge/ledger/plans.jsonl`, and pushes the new plan branch to the remote repository.

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
4. Created an intent branch (e.g., `I-1771890389-refactor-metrics/_`) and registered it in the intent ledger.

---

## Recommended Execution Methods (Holon CLI)

> [!IMPORTANT] **Use the Holon CLI instead of raw `docker run` commands.** The CLI automatically manages image mapping,
> credential passing, and SSH agent socket mounts.

You can run Plan Generation via any of the following standard pathways:

### Option A: Globally Installed Tool (`uv tool`)

If installed globally via `uv tool install ./apps/sandbox-executor` (from repository root) or
`uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"`
(can pin `@main`, release `@<tag>`, or immutable `@<commit-sha>`):

```bash
holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.5-flash
```

### Option B: Ephemeral Execution (`uvx`)

Run on-demand without prior installation:

> [!WARNING] **Always specify `--from` when invoking via `uvx`.** Do not execute bare `uvx holon`. The package name
> `holon` is registered on public PyPI by an unrelated third-party project; omitting `--from` introduces a dependency
> confusion and namespace collision risk where `uvx` will attempt to download and run the third-party PyPI package.
> Additionally, local path invocations (`uvx --from ./apps/sandbox-executor`) must be run from the repository root.

```bash
# Run from local repository clone (must be run from repository root)
uvx --from ./apps/sandbox-executor holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.5-flash

# Or directly from remote Git repository (can pin @main, release @<tag>, or immutable @<commit-sha>)
uvx --from "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor" holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.5-flash
```

### Option C: Repository Convenience Script (`./holon`)

Run from the repository root using the [`./holon`](../../holon) host wrapper:

```bash
./holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.5-flash
```

---

## Optional: Token Reduction Proxy (`--token-reduce`)

Pass `--token-reduce` to route the planner sandbox's HTTP(S) egress through a locally-owned mitmproxy sidecar, so
responses can be compacted before the agent spends tokens on them.

```bash
./holon plan "I-1782654790-bootstrap-holon-cli-intent/_" --agent pi-agent --model gemini-3.5-flash --token-reduce
```

> [!WARNING] **`--token-reduce` is experimental and not yet functional.** The Phase 2 mitmproxy addon (`mitm_addon.py`)
> is not shipped yet, so the preflight raises `FileNotFoundError`, the CLI logs an actionable error, and the run
> continues with **direct egress** — no interception takes place.

> [!WARNING] Once functional, `--token-reduce` performs **local TLS interception**. A Holon Root CA is generated at
> `~/.holon/certs/holon-root-ca.crt` (with `basicConstraints=critical,CA:TRUE` and
> `keyUsage=critical,keyCertSign,cRLSign`, and rotated automatically once it would expire within 30 days) so it can
> decrypt and re-encrypt agent traffic.
>
> **Trust mechanism (merged bundle, not `update-ca-certificates`)**: the sandbox image runs as the unprivileged `holon`
> user, so the Debian trust store can never be refreshed. Instead the entrypoint concatenates the image's system store
> (`/etc/ssl/certs/ca-certificates.crt`) with the Holon CA into `/home/holon/.holon-ca-bundle.crt`, and `SSL_CERT_FILE`
> / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` point at that merged file — those variables _replace_ the trust store, so
> pointing them at the single-cert Holon mount would break every legitimate HTTPS endpoint. `NODE_EXTRA_CA_CERTS` points
> at the Holon CA alone because it _augments_ Node's built-in roots.
>
> **Key exposure**: a MITM proxy inherently requires the CA **private key** to sign forged leaves, so
> `~/.holon/proxy-ca/mitmproxy-ca.pem` (key + cert, mode `0600`) and `mitmproxy-ca-cert.pem` are mounted **read-only
> into the proxy sidecar only** (`/home/mitmproxy/.mitmproxy`). The private key is never mounted into the _agent_
> container, which only ever receives the public certificate.
>
> **Retention / redaction posture**: the proxy cache (`~/.holon/proxy-cache`) is mounted read-only into the sidecar and
> sidecar logs are size-bounded, but **no credential redaction is implemented yet** (Phase 2). `--token-reduce` must
> therefore only be used against a locally-owned proxy.

- **Prerequisites**: the `docker` and `openssl` host binaries. If either is missing, the addon script is absent, the
  sidecar fails to start, or it never becomes ready, the CLI logs an actionable error and the run continues with
  **direct egress** — a dead proxy is never injected into the sandbox.
- **Isolation**: the sidecar runs on a per-run Docker network (`holon-net-<pid>-<uuid>`), is capped at
  `--memory=256m --cpus=0.5` with bounded log rotation, and both it and its network are removed when the run finishes
  (on every exit path, including early failures while assembling the `docker run` command).

### Environment contract

| Variable             | Effect                                                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HOLON_TOKEN_REDUCE` | Opt-in without the flag (`1`, `true`, `yes`, `on`). Attaches to an already-running proxy; never starts one.                                               |
| `HOLON_PROXY_URL`    | Proxy URL used in the `HOLON_TOKEN_REDUCE` path. Defaults to the host gateway (`host.docker.internal:8080` on macOS/Windows, `172.17.0.1:8080` on Linux). |

> [!IMPORTANT] Host `HTTP_PROXY` / `HTTPS_PROXY` are **never** interpreted as opt-in. Sandbox networking is only changed
> when you pass `--token-reduce` or set `HOLON_TOKEN_REDUCE` explicitly.

---

## Low-Level Execution (Manual `docker run`)

If you need to invoke Docker manually, run the following command to start the planner container, replacing arguments as
needed:

### For macOS (Docker Desktop)

```bash
docker run --rm \
  -e HOLON_ROLE=planner \
  -v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock \
  -e SSH_AUTH_SOCK=/run/host-services/ssh-auth.sock \
  holon/agent-pi \
  "I-1782654790-bootstrap-holon-cli-intent/_" \
  "pi-agent" \
  "gemini-3.5-flash"
```

### For Linux

> [!IMPORTANT] Ensure your `SSH_AUTH_SOCK` environment variable is set and non-empty (e.g., `echo $SSH_AUTH_SOCK` shows
> a path) before running this command. If it is empty, the bind mount `-v "$SSH_AUTH_SOCK:/run/ssh-agent"` will resolve
> to a syntax error.

```bash
docker run --rm \
  -e HOLON_ROLE=planner \
  -v "$SSH_AUTH_SOCK:/run/ssh-agent" \
  -e SSH_AUTH_SOCK=/run/ssh-agent \
  holon/agent-pi \
  "I-1782654790-bootstrap-holon-cli-intent/_" \
  "pi-agent" \
  "gemini-3.5-flash"
```

### Argument Breakdown:

- **`-e HOLON_ROLE=planner`**: Routes the entrypoint call to the Python planner module (`planner.py`) inside the
  container.
- **`-v /run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock` / `-v "$SSH_AUTH_SOCK:/run/ssh-agent"`**:
  Mounts the host's SSH agent socket into the container to enable SSH Agent Forwarding. This allows the container to
  authenticate using your host's SSH credentials securely without exposing private key files directly to the container.
  (The container automatically configures `StrictHostKeyChecking=no` internally).
- **`holon/agent-pi`**: The name of the built Docker image for the chosen agent (e.g., `holon/agent-pi`,
  `holon/agent-claude`).
- **Container Arguments:**
  1. **`intent_branch`** (e.g., `"I-1771890389-refactor-metrics/_"`): The target intent branch to branch off from.
  2. **`agent_name`** (e.g., `"pi-agent"`): The name of the planning agent.
  3. **`model_name`** (e.g., `"gemini-3.5-flash"`): The model used for planning.

---

## 2. Execution Details & Fail-Fast Behavior

During execution, the container performs the following operations:

1. Clones the repository fresh into `/home/holon/repo` targeting the provided `intent_branch`.
2. Creates an ephemeral plan branch: `I-{timestamp}-{slug}/P-{plan_timestamp}-{agent_name}-{model_name}/_`.
3. Compiles a detailed prompt using the template in `holon-config/prompts/planner.template.md` combined with the intent
   data from the ledger.
4. Invokes the AI planning agent (and fails fast, exiting with a non-zero exit code if the agent invocation fails or
   returns empty).
5. Parses metrics (`p_success`, `entropy`, `impact`, `cost`, `learning_value`, `ev`) directly from the markdown plan
   file.
6. Appends a structured plan entry to `holon-knowledge/ledger/plans.jsonl`.
7. Commits the changes and pushes the plan branch to origin.

---

## 3. Verification

Once execution completes, you should verify:

1. A new remote plan branch exists:
   ```bash
   git fetch origin && git branch -r
   # e.g., origin/I-1771890389-refactor-metrics/P-1772691068-pi-agent-gemini-3.5-flash/_
   ```
   > [!NOTE] The agent name in the branch and plan identifiers reflects the `agent_name` argument passed to the
   > container (e.g., `pi-agent`, `claude-agent`, `antigravity-agent`).
2. The generated plan markdown file is present under `plans/`:
   ```markdown
   # plans/P-{timestamp}-{agent}-{model}.md
   ```
3. A plan entry is appended to `holon-knowledge/ledger/plans.jsonl` with parsed metrics and expected value (EV)
   calculations:
   ```json
   {
     "plan_id": "P-1772691068-pi-agent-gemini-3.5-flash",
     "intent_branch": "I-1771890389-refactor-metrics/_",
     "agent": "pi-agent",
     "model": "gemini-3.5-flash",
     "p_success": 0.7,
     "entropy": 2.5,
     "impact": 1.0,
     "cost": 0.5,
     "learning_value": 0.0,
     "ev": 0.45,
     "created_at": "2026-06-27T13:14:15.000Z",
     "plan_file": "plans/P-1772691068-pi-agent-gemini-3.5-flash.md",
     "status": "proposed"
   }
   ```
