"""Zero-PR safety guards and host-local LLM environment validation for ``holon flow`` E2E runs."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from sandbox_executor import local_llm
from sandbox_executor.e2e_teardown import is_ref_within_intent_prefix, validate_e2e_intent_prefix
from sandbox_executor.e2e_types import (
    E2EMachineryError,
    GhRunnerCallable,
    ZeroPRCheckResult,
    ZeroPRGuardViolationError,
)
from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.git import run_git
from sandbox_executor.local_llm import (
    ENV_AGENT_PROVIDER,
    ENV_HOST_LOCAL_HOSTS,
    ENV_LOCAL_BASE_URL,
    ENV_LOCAL_MODE,
    ENV_LOCAL_MODELS,
    ENV_LOCAL_PROVIDER,
    LocalLLMConfigError,
    ModelEnvelope,
)


def assert_no_skip_review_flag(
    cli_args: Sequence[str] = (),
    options: Mapping[str, Any] | None = None,
) -> None:
    """Raise :class:`ZeroPRGuardViolationError` if ``--skip-review`` or ``skip_review`` is present."""
    for arg in cli_args:
        token = str(arg).strip()
        if token == "--skip-review" or token.startswith("--skip-review="):
            raise ZeroPRGuardViolationError(
                "--skip-review was removed from 'holon flow' to protect the Stage 4 consensus boundary; "
                "E2E runs must halt at the Stage 4 pre-PR boundary via E2EPipelineEngine instead."
            )
    if options and "skip_review" in options:
        raise ZeroPRGuardViolationError(
            "'skip_review' is forbidden in E2E flow options; use E2EPipelineEngine to halt before PR creation."
        )


_GITHUB_SCP_REMOTE_RE = re.compile(r"^(?:[^/@:\s]+@)?github\.com:[^\s]+$", re.IGNORECASE)


def _is_github_remote_url(url: str) -> bool:
    """Return True when ``url`` points at ``github.com`` over HTTP(S), SSH, or Git protocol."""
    cleaned = url.strip()
    if not cleaned:
        return False
    if "://" in cleaned:
        parsed = urlsplit(cleaned)
        if parsed.scheme.lower() not in ("https", "http", "ssh", "git"):
            return False
        return (parsed.hostname or "").lower() == "github.com"
    return bool(_GITHUB_SCP_REMOTE_RE.match(cleaned))


def _origin_remote_url(repo_dir: str) -> str | None:
    try:
        proc = run_git(["remote", "get-url", "origin"], cwd=repo_dir, check=False)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    return url or None


def _default_gh_runner(repo_dir: str) -> GhRunnerCallable | None:
    origin_url = _origin_remote_url(repo_dir)
    if origin_url is None or not _is_github_remote_url(origin_url):
        return None
    adapter = GitHubAdapter(repo_dir)
    if not adapter.available:
        raise E2EMachineryError(
            "Zero-PR guard requires the 'gh' CLI for a GitHub origin, but 'gh' was not found on PATH."
        )
    return adapter.gh


def _parse_pr_list_items(stdout: str) -> list[dict[str, Any]]:
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise E2EMachineryError(f"gh pr list returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise E2EMachineryError("gh pr list JSON payload must be a list of pull request objects.")
    return [item for item in parsed if isinstance(item, dict)]


def _close_stray_pr(
    runner: GhRunnerCallable,
    repo_dir: str,
    pr_number: int,
    validated_prefix: str,
) -> tuple[bool, str]:
    close_args = [
        "pr",
        "close",
        str(pr_number),
        "--comment",
        f"Closed stray E2E pull request for disposable intent {validated_prefix}.",
    ]
    close_proc = runner(close_args, cwd=repo_dir)
    if close_proc.returncode == 0:
        return True, ""
    detail = (close_proc.stderr or close_proc.stdout or "").strip() or f"exit {close_proc.returncode}"
    return False, f"#{pr_number} ({detail})"


def enforce_zero_pr_guard(
    repo_dir: str,
    intent_prefix: str,
    *,
    worktree_branch: str | None = None,
    gh_runner: GhRunnerCallable | None = None,
) -> ZeroPRCheckResult:
    """Assert that zero pull requests reference branches under ``intent_prefix``."""
    validated_prefix = validate_e2e_intent_prefix(intent_prefix)
    runner = gh_runner if gh_runner is not None else _default_gh_runner(repo_dir)
    if runner is None:
        return ZeroPRCheckResult(intent_prefix=validated_prefix, passed=True)

    list_args = ["pr", "list", "--state", "all", "--json", "number,headRefName,baseRefName,state"]
    list_proc = runner(list_args, cwd=repo_dir)
    if list_proc.returncode != 0:
        detail = (list_proc.stderr or list_proc.stdout or "").strip() or f"exit code {list_proc.returncode}"
        raise E2EMachineryError(
            f"Zero-PR guard could not inspect pull requests for {validated_prefix!r}: gh pr list failed ({detail})."
        )

    matching_numbers: list[int] = []
    closed_numbers: list[int] = []
    close_failures: list[str] = []
    matching_details: list[str] = []

    for item in _parse_pr_list_items(list_proc.stdout):
        head_ref = str(item.get("headRefName") or "")
        base_ref = str(item.get("baseRefName") or "")
        head_matches = is_ref_within_intent_prefix(head_ref, validated_prefix, worktree_branch=worktree_branch)
        base_matches = is_ref_within_intent_prefix(base_ref, validated_prefix, worktree_branch=worktree_branch)
        raw_num = item.get("number")
        if not (head_matches or base_matches) or not isinstance(raw_num, int) or isinstance(raw_num, bool):
            continue
        matching_numbers.append(raw_num)
        state = str(item.get("state") or "OPEN").upper()
        matching_details.append(f"#{raw_num} (head={head_ref!r}, base={base_ref!r}, state={state})")
        if state == "OPEN":
            closed, err_detail = _close_stray_pr(runner, repo_dir, raw_num, validated_prefix)
            if closed:
                closed_numbers.append(raw_num)
            else:
                close_failures.append(err_detail)

    if matching_numbers:
        failure_suffix = f"; close failure(s): {', '.join(close_failures)}" if close_failures else ""
        raise ZeroPRGuardViolationError(
            f"Zero-PR guard violated for E2E intent prefix {validated_prefix!r}: found stray PR(s) "
            f"{', '.join(matching_details)}; closed PR(s): {closed_numbers}{failure_suffix}."
        )
    return ZeroPRCheckResult(intent_prefix=validated_prefix, passed=True)


def _validate_rewritten_base_url(base_url: str, allow_list: Iterable[str]) -> str:
    """Rewrite ``base_url`` against ``allow_list`` and fail closed if it is not gateway-reachable."""
    rewritten = local_llm.rewrite_authority(base_url, allow_list)
    auth = local_llm._authority(rewritten)
    if auth is None:
        raise LocalLLMConfigError(f"The configured {ENV_LOCAL_BASE_URL} is not a valid URL; ignoring it.")
    if auth[0] != local_llm.GATEWAY_HOST:
        shown = local_llm.shown_authority(base_url)
        target = f"host {shown}" if shown else "host withheld: no unambiguous userinfo boundary"
        raise LocalLLMConfigError(
            f"The configured {ENV_LOCAL_BASE_URL} ({target}) is neither a loopback address nor in "
            f"{ENV_HOST_LOCAL_HOSTS}."
        )
    return rewritten


def _synthesised_host_config_from_env(env: Mapping[str, str], allow_list: Iterable[str]) -> dict[str, Any]:
    """Build a minimal host config dictionary from an explicit ``env`` mapping without ``os.environ`` fallback."""
    local_llm.check_text_settings(env)
    base_url = env.get(ENV_LOCAL_BASE_URL, "").strip()
    if not base_url:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_MODE} is enabled but {ENV_LOCAL_BASE_URL} is not set in the provided environment."
        )
    model_ids = [item.strip() for item in env.get(ENV_LOCAL_MODELS, "").split(",") if item.strip()]
    if not model_ids:
        raise LocalLLMConfigError(f"{ENV_LOCAL_BASE_URL} is set but no model ids were declared in {ENV_LOCAL_MODELS}.")
    rewritten = _validate_rewritten_base_url(base_url, allow_list)
    provider = env.get(ENV_LOCAL_PROVIDER, "").strip() or env.get(ENV_AGENT_PROVIDER, "").strip() or "local"
    return {
        "providers": {
            provider: {
                "baseUrl": rewritten,
                "api": "openai-completions",
                "apiKey": "holon-local",
                "models": [{"id": mid} for mid in model_ids],
            }
        }
    }


def _allow_list_from_env(env: Mapping[str, str]) -> set[str]:
    """Normalise ``HOLON_HOST_LOCAL_HOSTS`` from ``env`` using the same scheme/path stripping as ``local_llm``."""
    raw = env.get(ENV_HOST_LOCAL_HOSTS, "")
    if not local_llm.is_utf8_text(raw):
        raise LocalLLMConfigError(
            f"{ENV_HOST_LOCAL_HOSTS} is not valid UTF-8 text (a lone surrogate or a non-UTF-8 byte); value withheld."
        )
    allow: set[str] = set()
    for entry in raw.split(","):
        cleaned = re.sub(r"^https?://", "", entry.strip().lower()).split("/")[0].strip()
        if cleaned:
            allow.add(cleaned)
    return allow


def validate_e2e_local_llm_env(
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str, list[ModelEnvelope]]:
    """Verify whether host-local LLM mode is requested and validly configured for an opt-in E2E run."""
    effective_env = os.environ if env is None else env
    raw_mode = effective_env.get(ENV_LOCAL_MODE, "").strip().lower()
    if raw_mode not in ("1", "true", "yes", "on"):
        return (
            False,
            (
                f"{ENV_LOCAL_MODE} is not enabled. Set {ENV_LOCAL_MODE}=1 (plus {ENV_LOCAL_BASE_URL} and "
                f"{ENV_LOCAL_MODELS}, or ~/.pi/agent/models.json) and invoke 'uv run task test-e2e' "
                "(see docs/flow_e2e.md)."
            ),
            [],
        )
    allow = local_llm.host_local_allow_list() if env is None else _allow_list_from_env(effective_env)
    host_cfg = local_llm.host_models_json() if env is None else _synthesised_host_config_from_env(effective_env, allow)
    _config, envelopes = local_llm.build_envelope_config(host_cfg, allow, env=effective_env)
    if not envelopes:
        raise LocalLLMConfigError(
            f"{ENV_LOCAL_MODE}=1 is enabled but no model entries were resolved from "
            f"host models.json or {ENV_LOCAL_MODELS}."
        )
    summary = "; ".join(f"{env_item.envelope_line()} {env_item.reasoning_line()}" for env_item in envelopes)
    return True, summary, envelopes
