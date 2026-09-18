# Running Execution via Docker

This guide explains how to run the Sandbox Executor role using the Docker sandbox environment.

The Executor checks out a plan branch, runs the AI coding agent to implement changes, executes validation test suites,
records execution results in the ledger `holon-knowledge/ledger/executions.jsonl`, and pushes the execution branch.

For in-depth specifications of the execution architecture and credentials mapping, refer to:

- [Sandbox Executor Architecture Specification](../executor/execution_architecture_specification.md)
- [Agent Credentials & API Key Requirements](../executor/agent_credentials_requirements.md)

---

## Recommended Execution Methods (Holon CLI)

> [!IMPORTANT] **Use the Holon CLI instead of raw `docker run` commands.** Always run sandbox executions via the Holon
> CLI, which handles setting up the container and environment, mapping directly into the
> [3-Tier Fallback Contract](../executor/agent_credentials_requirements.md#the-3-tier-fallback-contract).

You can run Sandbox Execution via any of the following standard pathways:

### Option A: Globally Installed Tool (`uv tool`)

If installed globally via `uv tool install ./apps/sandbox-executor` (from repository root) or
`uv tool install "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor"`
(can pin `@main`, release `@<tag>`, or immutable `@<commit-sha>`):

```bash
holon execute "I-1782654790-bootstrap-holon-cli-intent/P-1784988130-antigravity-agent-gemini-3.5-flash/_" --agent antigravity-agent --model gemini-3.5-flash
```

### Option B: Ephemeral Execution (`uvx`)

Run on-demand without prior installation:

> [!WARNING] **Always specify `--from` when invoking via `uvx`.** Do not execute bare `uvx holon`. The package name
> `holon` is registered on public PyPI by an unrelated third-party project; omitting `--from` introduces a dependency
> confusion and namespace collision risk where `uvx` will attempt to download and run the third-party PyPI package.
> Additionally, local path invocations (`uvx --from ./apps/sandbox-executor`) must be run from the repository root.

```bash
# Run from local repository clone (must be run from repository root)
uvx --from ./apps/sandbox-executor holon execute "I-1782654790-bootstrap-holon-cli-intent/P-1784988130-antigravity-agent-gemini-3.5-flash/_" --agent antigravity-agent --model gemini-3.5-flash

# Or directly from remote Git repository (can pin @main, release @<tag>, or immutable @<commit-sha>)
uvx --from "git+https://github.com/Holon-Agentic-Coder/holon-agentic-coder.git@main#subdirectory=apps/sandbox-executor" holon execute "I-1782654790-bootstrap-holon-cli-intent/P-1784988130-antigravity-agent-gemini-3.5-flash/_" --agent antigravity-agent --model gemini-3.5-flash
```

### Option C: Repository Convenience Script (`./holon`)

Run from the repository root using the [`./holon`](../../holon) host wrapper:

```bash
./holon execute "I-1782654790-bootstrap-holon-cli-intent/P-1784988130-antigravity-agent-gemini-3.5-flash/_" --agent antigravity-agent --model gemini-3.5-flash
```

The Holon CLI automatically handles setting up the container and environment, mapping directly into the
[3-Tier Fallback Contract](../executor/agent_credentials_requirements.md#the-3-tier-fallback-contract):

### 1. Ephemeral Secret & Env Forwarding (Tier 1 & 2)

The script searches the host environment for active tokens and passes them down as Docker environment flags (`-e`):

- **GitHub Token Discovery**: Auto-detects tokens using `GITHUB_TOKEN` or `GH_TOKEN` environment variables, or
  automatically executes the host `gh auth token` command.
- **Agent Keys & Configuration**: Forwards all environment variables starting with `HOLON_AGENT_` (such as
  `HOLON_AGENT_KEY`, `HOLON_AGENT_OSS_MODE`, and `HOLON_AGENT_EFFORT`).

### 2. Session Directory Mounts (Tier 3)

The wrapper checks the host user's home folder for existing agent credentials/sessions and maps them into the container
under `/home/holon/...` as read-only (`:ro`) mounts:

- **`antigravity`**: Maps `~/.gemini/antigravity-cli` and `~/.config/antigravity`
- **`claude`**: Maps `~/.config/claude`
- **`codex`**: Maps `~/.codex`
- **`pi`**: Maps `~/.config/pi`
- **`gemini`**: Maps `~/.config/gcloud`

### 3. SSH Agent Socket Forwarding

To allow the containerized executor to perform git pushes to remote origins without requiring SSH keys inside the
sandbox:

- **macOS**: Mounts `/run/host-services/ssh-auth.sock` to the container and updates the `SSH_AUTH_SOCK` environment.
- **Linux/Other**: Mounts the host's existing `SSH_AUTH_SOCK` value to `/run/ssh-agent` in the container.

### 4. Optional Token Reduction Proxy (`--token-reduce`)

```bash
./holon execute "I-1782654790-bootstrap-holon-cli-intent/P-1784988130-antigravity-agent-gemini-3.5-flash/_" \
  --agent antigravity-agent --model gemini-3.5-flash --token-reduce
```

`--token-reduce` routes the sandbox's HTTP(S) egress through a locally-owned mitmproxy sidecar so agent responses can be
compacted before they are tokenized.

> [!WARNING] **`--token-reduce` is experimental and not yet functional.** The Phase 2 mitmproxy addon (`mitm_addon.py`)
> is not shipped yet, so the preflight raises `FileNotFoundError`, the CLI logs an actionable error, and the run
> continues with **direct egress** — no interception takes place.

> [!WARNING] Once functional, `--token-reduce` performs **local TLS interception**. A Holon Root CA is generated at
> `~/.holon/certs/holon-root-ca.crt` (with `basicConstraints=critical,CA:TRUE` and
> `keyUsage=critical,keyCertSign,cRLSign`, and rotated automatically once it would expire within 30 days).
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

## Command Breakdown

- **`plan_branch`** (positional, required): The target plan branch to execute.
- **`--agent`** (optional, default: `antigravity-agent`): Agent runner to execute.
- **`--model`** (optional, default: `gemini-3.5-flash`): Target LLM model name.
- **`--token-reduce`** (optional, flag): **experimental / not yet functional** — route sandbox egress through the local
  token-reduction proxy (requires `docker` + `openssl`; performs local TLS interception, see
  [Optional Token Reduction Proxy](#4-optional-token-reduction-proxy---token-reduce)).

---

## Related Documents

- [Executor Architecture Specification](../executor/execution_architecture_specification.md)
- [Agent Credentials & API Key Requirements](../executor/agent_credentials_requirements.md)
- [Planning Architecture Specification](../planner/planning_architecture_specification.md)
- [Creating a Plan in Sandbox](create_plan.md)
