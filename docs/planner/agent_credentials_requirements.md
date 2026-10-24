# Agent Credentials & API Key Requirements

This document outlines the API key and credential requirements for each supported coding agent in the Holon planning
environment. To run these agents successfully inside the isolated sandbox docker containers, the required environment
variables must be passed to `docker run`.

---

## 1. Summary of Credentials Requirements

| Agent Name              | Executor/CLI Binary | Default Target Package            | Required API Key / Env Variable                                        | Notes                                                                                                                            |
| :---------------------- | :------------------ | :-------------------------------- | :--------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------- |
| **`pi-agent`**          | `pi`                | `@earendil-works/pi-coding-agent` | `PI_API_KEY`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY`                    | Leverages Google Gemini APIs. Key can be mapped via `PI_API_KEY`.                                                                |
| **`claude-agent`**      | `claude`            | `@anthropic-ai/claude-code`       | `ANTHROPIC_API_KEY`                                                    | Used to run Anthropic Claude Code non-interactively.                                                                             |
| **`gemini-agent`**      | `gemini`            | `@google/gemini-cli`              | `GEMINI_API_KEY` or `GOOGLE_API_KEY`                                   | Google Gemini CLI client tool.                                                                                                   |
| **`opencode-agent`**    | `opencode`          | `opencode-ai`                     | `OPENAI_API_KEY`                                                       | Connects to OpenAI-styled backends.                                                                                              |
| **`codex-agent`**       | `codex`             | `@openai/codex`                   | `OPENAI_API_KEY`                                                       | OpenAI Codex agent integration.                                                                                                  |
| **`antigravity-agent`** | `agy`               | N/A (Downloaded via script)       | `GOOGLE_API_KEY` or Google Cloud Application Default Credentials (ADC) | Antigravity CLI agent. In headless/non-interactive environments, OAuth prompts will timeout; active credentials must be present. |

---

## 2. Detailed Agent Configuration

### Pi Agent (`pi-agent`)

The Pi agent uses the `@earendil-works/pi-coding-agent` package. The planner script automatically maps the `PI_API_KEY`
environment variable to the `--api-key` argument if set:

```bash
docker run --rm \
  -e HOLON_ROLE=planner \
  -e PI_API_KEY="your-gemini-key" \
  holon/agent-pi \
  "I-intent-branch/_" "pi-agent" "gemini-3.8-flash"
```

### Claude Agent (`claude-agent`)

Claude Code requires an Anthropic API key to authenticate:

```bash
docker run --rm \
  -e HOLON_ROLE=planner \
  -e ANTHROPIC_API_KEY="your-anthropic-key" \
  holon/agent-claude \
  "I-intent-branch/_" "claude-agent" "claude-4-6-sonnet"
```

### Antigravity Agent (`antigravity-agent` / `agy`)

The `agy` CLI requires Google Cloud credentials to execute models and retrieve project configurations. In interactive
sessions, it initiates an OAuth browser flow. For headless executions within the Docker sandbox, you must:

1. Provide a `GOOGLE_API_KEY` environment variable.
2. Or mount the local Google credentials config from the host machine:
   ```bash
   docker run --rm \
     -e HOLON_ROLE=planner \
     -v "$HOME/.config/gcloud:/home/holon/.config/gcloud:ro" \
     holon/agent-antigravity \
     "I-intent-branch/_" "antigravity-agent" "gemini-3.8-flash-medium"
   ```

## 3. Readiness Check (`holon doctor`)

`holon plan` checks the selected runner with a one-prompt readiness probe before it starts the planner container, and
aborts with one of `quota_exhausted`, `not_logged_in`, `expired_key` or `unknown` instead of failing part-way through.
Run `holon doctor` to check a runner on its own, `--skip-preflight` (or `HOLON_SKIP_PREFLIGHT=1`) to bypass the probe,
and see [Readiness Probe](../executor/agent_credentials_requirements.md#readiness-probe-holon-doctor) for the statuses
and their remediation.

When `--agent` and `--model` are omitted, the runner and model come from `HOLON_AGENT` / `HOLON_MODEL`, the detected
harness agent, and the per-runner defaults described in
[Runner and model resolution](../agents.md#runner-and-model-resolution).
