# Holon Flow End-to-End Test Harness & Unconditional Teardown

This document describes the opt-in end-to-end (`E2E`) test harness and unconditional teardown utility for `holon flow`,
implemented in `apps/sandbox-executor/src/sandbox_executor/e2e_harness.py` and exercised by
`apps/sandbox-executor/tests/e2e/test_flow_e2e.py` (opt-in live local model runs) and
`apps/sandbox-executor/tests/test_flow_e2e_harness.py` (hermetic unit tests run in default `uv run pytest`).

## Purpose & Scope

Before cutting a release or changing stage boundaries, operators can exercise real `holon flow` stage machinery, git
worktree isolation, append-only ledger invariants, and resumption logic against a disposable intent served by a
host-local OpenAI-compatible inference server (`local_llm`) without polluting the repository or GitHub remote with
orphan worktrees, branches, or pull requests.

The harness enforces five core safety invariants:

1. **Opt-In Execution & Default Suite Exclusion**: The live E2E test suite lives in
   `apps/sandbox-executor/tests/e2e/test_flow_e2e.py` and is marked with `@pytest.mark.e2e`. Both root `pyproject.toml`
   and `apps/sandbox-executor/pyproject.toml` configure `norecursedirs = ["e2e"]`, ensuring default `uv run pytest`,
   `uv run task test`, stress/hermetic child suites, and CI never collect or execute the live E2E test suite unless
   explicitly invoked via `uv run task test-e2e`.
2. **Pre-PR Stage 4 Boundary Without `--skip-review`**: Because `--skip-review` was removed from `holon flow` to protect
   the Stage 4 consensus boundary, the E2E harness reconciles with that removal in two ways:
   - `assert_no_skip_review_flag` rejects `--skip-review` in CLI arguments or `skip_review` in options with
     `ZeroPRGuardViolationError`.
   - `E2EPipelineEngine` subclasses `PipelineEngine` to run Stage 1 (`INTENT`), Stage 2 (`PLAN`), and Stage 3
     (`EXECUTE`) in full, then halts cleanly at the Stage 4 (`REVIEW`) pre-PR boundary (`StageStatus.HALTED_FOR_HUMAN`
     with `stopped_before_pr = True`) before `GitHubAdapter.open_or_update` or `create_or_update_pr` is called, while
     skipping Stage 5 (`CALIBRATE`).
3. **Zero-PR Guard (`enforce_zero_pr_guard`)**: After every attempt, the harness queries `gh pr list --state all` for
   any pull request whose `headRefName` or `baseRefName` falls within the test intent prefix. If a stray open PR is
   found, the guard immediately closes it via `gh pr close <number>` and raises `ZeroPRGuardViolationError`.
4. **Retry Classification (`classify_attempt_failure` & `run_e2e_with_retry`)**: Model-output assertion failures
   (`FailureCategory.MODEL_OUTPUT`, such as missing plan metrics, empty diff, touch-set violation, over-budget plan, or
   `E2EModelOutputError`) retry up to a bounded cap (`DEFAULT_E2E_MAX_ATTEMPTS = 3`, hard ceiling
   `MAX_E2E_ATTEMPTS_CAP = 5`) with full worktree and branch teardown after each attempt. Flow-machinery failures
   (`FailureCategory.MACHINERY`, such as `E2EMachineryError`, `LocalLLMConfigError`, `LedgerRewriteError`,
   `PrefixScopingError`, `ZeroPRGuardViolationError`, git fetch/push/worktree failures, or unhandled exceptions) fail
   immediately on the first occurrence without retry.
5. **Unconditional Prefix-Scoped Teardown (`teardown_e2e_intent`)**: Wrapped inside the innermost `try ... finally`
   block of every attempt, teardown removes the disposable worktree (`flow-<slug>` and any `flow-<slug>-rev*` reviewer
   siblings), deletes all local branches under the run's intent prefix, and deletes all remote branches under that
   prefix on `origin`. Every deleted item is enumerated in a `TeardownReport`.

---

## Host-Local Model Configuration (`local_llm`)

The opt-in E2E suite uses the existing host-local LLM support (`sandbox_executor.local_llm`). Containerised role agents
connect to the host inference server through the Docker gateway (`host.docker.internal`), which the launcher rewrites
automatically from loopback or allow-listed host authorities.

### Environment Variables

| Variable                       | Required | Default | Description                                                                                             |
| ------------------------------ | -------- | ------- | ------------------------------------------------------------------------------------------------------- |
| `HOLON_LOCAL_LLM`              | Yes      | `0`     | Opt-in switch (`1`, `true`, `yes`, or `on`) enabling host-local LLM mode.                               |
| `HOLON_AGENT`                  | No       | `pi`    | Agent runner identifier (`pi` / `pi-agent` for OpenAI-compatible local servers).                        |
| `HOLON_MODEL`                  | No       | first   | Model ID served by the local endpoint (e.g. `qwen2.5-coder:14b`).                                       |
| `HOLON_LOCAL_BASE_URL`         | Cond.    | unset   | Base URL when `~/.pi/agent/models.json` is absent (e.g. `http://localhost:11434/v1`).                   |
| `HOLON_LOCAL_MODELS`           | Cond.    | unset   | Comma-separated model IDs when synthesizing configuration without a host `models.json`.                 |
| `HOLON_LOCAL_PROVIDER`         | No       | `local` | Provider key in the synthesized `models.json`.                                                          |
| `HOLON_LOCAL_CONTEXT_WINDOW`   | Cond.    | unset   | Fallback `contextWindow` (positive integer `<= 2147483647`) when the model entry omits `contextWindow`. |
| `HOLON_CONTEXT_RESERVE_TOKENS` | No       | `4096`  | Prompt token reserve subtracted from `contextWindow` when bounding `maxTokens`.                         |
| `HOLON_REASONING_MODE`         | No       | `off`   | Reasoning mode (`off`, `minimal`, `low`, `medium`, `high`).                                             |
| `HOLON_HOST_LOCAL_HOSTS`       | No       | unset   | Comma-separated allow list of non-loopback host authorities eligible for gateway rewriting.             |

You can configure the endpoint either via `~/.pi/agent/models.json` on the host or purely through environment variables:

```bash
HOLON_LOCAL_LLM=1 \
HOLON_AGENT=pi \
HOLON_MODEL=qwen2.5-coder:14b \
HOLON_LOCAL_BASE_URL=http://localhost:11434/v1 \
HOLON_LOCAL_MODELS=qwen2.5-coder:14b \
HOLON_LOCAL_CONTEXT_WINDOW=32768 \
HOLON_CONTEXT_RESERVE_TOKENS=4096 \
HOLON_REASONING_MODE=off \
uv run task test-e2e
```

---

## Deterministic Settings for Reproducible E2E Runs

Small or quantized local models exhibit higher output variance than frontier cloud models. To keep E2E runs
deterministic and avoid token-budget truncation:

- **Disable extended thinking during authoring stages**: Set `HOLON_REASONING_MODE=off` (the default in `local_llm`), so
  output tokens are spent on structured JSON/Markdown artifacts rather than long reasoning traces.
- **Pin context and reserve envelopes**: Set `HOLON_LOCAL_CONTEXT_WINDOW=32768` (or the model's true trained window) and
  `HOLON_CONTEXT_RESERVE_TOKENS=4096`. When `maxTokens` is omitted from the host config, `local_llm` synthesises
  `maxTokens = min(16384, contextWindow // 4)` and verifies `maxTokens <= contextWindow - reserve`.
- **Configure greedy decoding on the host server**: Configure your local server (Ollama, vLLM, llama.cpp, or LM Studio)
  with `temperature = 0` (and `top_p = 1.0`, fixed `seed`) where supported.
- **Bounded model-output retries**: `run_e2e_with_retry` defaults to `DEFAULT_E2E_MAX_ATTEMPTS = 3`
  (`1 <= max_attempts <= 5`), absorbs transient formatting/metric misses from local models by tearing down and retrying
  with a fresh timestamped intent prefix, and never retries `FailureCategory.MACHINERY` errors.

---

## Invocation

Always run commands from the repository root directory:

1. **Run the opt-in E2E flow suite** (requires `HOLON_LOCAL_LLM=1`):

   ```bash
   uv run task test-e2e
   ```

   Under the hood, this runs:

   ```bash
   uv run pytest -o norecursedirs= -m e2e apps/sandbox-executor/tests/e2e/test_flow_e2e.py
   ```

   When `HOLON_LOCAL_LLM` is not enabled, `test_flow_e2e.py` skips cleanly with an actionable configuration message.

2. **Run the hermetic unit tests for the E2E harness** (no network, Docker daemon, or local LLM required):

   ```bash
   uv run pytest apps/sandbox-executor/tests/test_flow_e2e_harness.py
   ```

3. **Run the default unit test suite** (automatically runs `test_flow_e2e_harness.py` and excludes `tests/e2e/`):

   ```bash
   uv run task test
   ```

---

## Teardown, Prefix-Scoping & Zero-PR Guarantees

### Strict Prefix Validation & Boundary Isolation

- `validate_e2e_intent_prefix` requires the prefix to match `^I-\d+-[a-z0-9][a-z0-9-]*$`. It rejects empty strings,
  wildcards (`*`, `?`), slashes (`/`), path traversal (`..`), and protected refs (`main`, `master`, `HEAD`, `origin`).
- `is_ref_within_intent_prefix` normalises leading `refs/heads/`, `refs/remotes/origin/`, and `origin/` prefixes and
  requires either an exact match to `intent_prefix` or a trailing slash (`f"{intent_prefix}/"`), plus optional matching
  for the run's dedicated disposable worktree branch `flow-<slug>`.
- A sibling prefix that shares a leading character sequence without a `/` boundary (for example
  `I-123456-e2e-run-other/_` vs `I-123456-e2e-run`) is strictly rejected.
- `assert_refs_within_intent_prefix` pre-validates any explicit candidate ref list before any git command runs, raising
  `PrefixScopingError` if any ref falls outside the prefix.

### Deletion Enumeration (`TeardownReport`)

`teardown_e2e_intent` returns a frozen `TeardownReport` dataclass containing:

- `intent_prefix`: The validated root intent prefix (`I-<timestamp>-<slug>`).
- `removed_worktrees`: Tuple of absolute worktree paths removed via `git worktree remove --force` and pruned via
  `git worktree prune`.
- `deleted_local_branches`: Tuple of local branch names deleted via `git branch -D`.
- `deleted_remote_branches`: Tuple of remote branch names deleted from `origin` via `git push origin --delete` and
  `git update-ref -d refs/remotes/origin/<branch>`.
- `errors`: Tuple of any non-zero git teardown diagnostics.
- `summary_line()`: Human-readable enumeration of all removed worktrees and deleted local/remote branches.
