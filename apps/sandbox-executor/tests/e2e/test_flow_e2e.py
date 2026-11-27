from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.e2e_harness import (
    DEFAULT_E2E_MAX_ATTEMPTS,
    E2EModelOutputError,
    is_ref_within_intent_prefix,
    run_e2e_flow_test,
    validate_e2e_local_llm_env,
)
from sandbox_executor.flow.context import FlowContext, StageResult, StageStatus
from sandbox_executor.git import run_git


def _seed_disposable_e2e_repository(root: Path) -> tuple[Path, Path]:
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    repo = root / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    for key, val in (("user.email", "e2e@holon.invalid"), ("user.name", "Holon E2E"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", key, val], cwd=repo, check=True, capture_output=True)

    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for name in ("intents.jsonl", "plans.jsonl", "executions.jsonl"):
        (ledger_dir / name).write_text("", encoding="utf-8")

    (repo / ".gitignore").write_text(".holon/\nholon-knowledge/.index/\n", encoding="utf-8")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "app.py").write_text(
        '"""Disposable E2E application module."""\n\n\ndef greet(name: str) -> str:\n    return f"Hello, {name}!"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed disposable E2E repo"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=repo, check=True, capture_output=True)
    return repo, origin


def _assert_e2e_stage_outputs(ctx: FlowContext, results: dict[str, StageResult]) -> None:
    for stage_name in ("intent", "plan", "execute"):
        res = results.get(stage_name)
        if res is None or res.status != StageStatus.SUCCESS:
            raise E2EModelOutputError(f"E2E stage {stage_name!r} did not succeed: {res.error if res else 'missing'}")

    review_res = results.get("review")
    if review_res is None or not review_res.payload.get("stopped_before_pr"):
        raise E2EModelOutputError("E2E run did not halt at the Stage 4 pre-PR boundary.")

    if not ctx.intent_branch or not ctx.plan_branch or not ctx.execution_branch:
        raise E2EModelOutputError("E2E context is missing expected intent, plan, or execution branch names.")


# why: norecursedirs keeps this out of default runs; run it via uv run task test-e2e with HOLON_LOCAL_LLM=1.
@pytest.mark.e2e
def test_flow_e2e_local_llm_with_unconditional_teardown(tmp_path: Path) -> None:
    ready, message, envelopes = validate_e2e_local_llm_env()
    if not ready:
        pytest.skip(message)

    repo, origin = _seed_disposable_e2e_repository(tmp_path)
    default_model = os.getenv("HOLON_MODEL", "").strip() or envelopes[0].model_id
    default_agent = os.getenv("HOLON_AGENT", "").strip() or "pi-agent"

    outcome = run_e2e_flow_test(
        str(repo),
        base_slug="local-llm-e2e",
        max_attempts=DEFAULT_E2E_MAX_ATTEMPTS,
        agent=default_agent,
        model=default_model,
        assertion_fn=_assert_e2e_stage_outputs,
    )

    assert outcome.succeeded is True
    assert 1 <= outcome.attempts_run <= DEFAULT_E2E_MAX_ATTEMPTS
    assert len(outcome.teardown_reports) == outcome.attempts_run

    for report in outcome.teardown_reports:
        assert report.errors == ()
        local_refs = run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], cwd=str(repo), check=False
        ).stdout.splitlines()
        remote_refs = run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads/"], cwd=str(origin), check=False
        ).stdout.splitlines()
        for ref in [*local_refs, *remote_refs]:
            assert not is_ref_within_intent_prefix(ref.strip(), report.intent_prefix)
