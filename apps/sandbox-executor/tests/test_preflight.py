"""Tests for the pre-flight readiness probe, `holon doctor` and the gating of plan, execute and flow."""

import json
import logging
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import preflight
from sandbox_executor.cli import PACKAGE_LOGGER_NAME, _CurrentStderrHandler, configure_cli_logging, main
from sandbox_executor.flow import get_default_checkpoint_path
from sandbox_executor.preflight import (
    PROBE_PROMPT,
    PreflightResult,
    PreflightStatus,
    classify_probe_output,
    resolve_timeout,
    run_preflight_probe,
)


@pytest.fixture
def probe_env(monkeypatch, tmp_path):
    """Isolated home, no sandbox markers, a dummy key and the real probe enabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("HOLON_ROLE", "HOLON_IN_SANDBOX", "HOLON_SKIP_PREFLIGHT", "HOLON_AGENT_SETTINGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_AGENT_KEY", "sk-test-very-secret-value")
    return tmp_path


def _result(status: PreflightStatus, agent: str = "antigravity-agent") -> PreflightResult:
    return PreflightResult(
        status=status,
        agent=agent,
        model="m",
        image="holon/agent-antigravity",
        exit_code=0 if status is PreflightStatus.OK else 1,
        output_excerpt="",
        hint="",
    )


@pytest.mark.parametrize(
    ("exit_code", "output", "expected"),
    [
        (0, "OK", PreflightStatus.OK),
        (0, "", PreflightStatus.UNKNOWN),
        (0, "   \n", PreflightStatus.UNKNOWN),
        (1, "Error 429: Too Many Requests", PreflightStatus.QUOTA_EXHAUSTED),
        (1, "status: RESOURCE_EXHAUSTED quota", PreflightStatus.QUOTA_EXHAUSTED),
        (1, "You exceeded your current quota, please check your plan", PreflightStatus.QUOTA_EXHAUSTED),
        (0, "rate limit reached", PreflightStatus.QUOTA_EXHAUSTED),
        (1, "Error: not logged in. Please run agy login", PreflightStatus.NOT_LOGGED_IN),
        (1, "Error: Missing required API credentials for agent 'antigravity'.", PreflightStatus.NOT_LOGGED_IN),
        (1, "session directory does not exist", PreflightStatus.NOT_LOGGED_IN),
        (1, "Your API key has expired", PreflightStatus.EXPIRED_KEY),
        (1, "401 Unauthorized: invalid x-api-key", PreflightStatus.EXPIRED_KEY),
        (1, "rpc error: UNAUTHENTICATED", PreflightStatus.EXPIRED_KEY),
        (1, "invalid model selection (--model 'x' --effort 'medium')", PreflightStatus.UNKNOWN),
        (None, "", PreflightStatus.UNKNOWN),
        (125, "docker: Error response from daemon: pull access denied", PreflightStatus.UNKNOWN),
        # An exit-0 probe that answered is ok, whatever incidental output the runner logged around the reply.
        (0, "OK\nwarning: cached session token expired, refreshed", PreflightStatus.OK),
        (0, "OK\n...token expired...", PreflightStatus.OK),
        (0, "[debug] HTTP 401 retry succeeded\nOK.", PreflightStatus.OK),
        (0, "OK\nrate limit headroom: 12 requests", PreflightStatus.OK),
        # Bare status codes and a bare "expired" are not failure signals on their own.
        (1, "request id req-429-abc failed", PreflightStatus.UNKNOWN),
        (1, "cache expired; aborting", PreflightStatus.UNKNOWN),
        (1, "HTTP 401", PreflightStatus.EXPIRED_KEY),
        (1, "status code: 429", PreflightStatus.QUOTA_EXHAUSTED),
        (1, "OK", PreflightStatus.UNKNOWN),
    ],
)
def test_classifier_truth_table(exit_code, output, expected):
    assert classify_probe_output(exit_code, output) is expected


def test_timeout_resolution(monkeypatch):
    monkeypatch.delenv("HOLON_PREFLIGHT_TIMEOUT", raising=False)
    assert resolve_timeout() == 60.0
    monkeypatch.setenv("HOLON_PREFLIGHT_TIMEOUT", "5")
    assert resolve_timeout() == 5.0
    assert resolve_timeout(2) == 2.0
    monkeypatch.setenv("HOLON_PREFLIGHT_TIMEOUT", "nonsense")
    assert resolve_timeout() == 60.0


def test_probe_runs_runner_in_its_container_and_redacts(probe_env):
    runner_fn = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="429 quota hit for key sk-test-very-secret-value"
        )
    )
    res = run_preflight_probe("antigravity-agent", "gemini-3.8-flash-medium", timeout=7, runner_fn=runner_fn)

    assert res.status is PreflightStatus.QUOTA_EXHAUSTED
    assert "sk-test-very-secret-value" not in res.output_excerpt
    assert "sk-test-very-secret-value" not in res.format_report()
    assert "sk-test-very-secret-value" not in json.dumps(res.to_dict())

    cmd = runner_fn.call_args[0][0]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert runner_fn.call_args[1]["timeout"] == 7
    assert "holon/agent-antigravity" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "-e" in cmd and "HOLON_AGENT_ID=antigravity" in cmd
    assert not any(item.startswith("HOLON_ROLE=") for item in cmd)
    tail = cmd[cmd.index("holon/agent-antigravity") + 1 :]
    assert tail[0] == "agy"
    assert tail[tail.index("--model") : tail.index("--model") + 2] == ["--model", "gemini-3.8-flash-medium"]
    assert "--effort" in tail
    assert tail[-1] == PROBE_PROMPT


def test_redact_output_masks_agent_settings_and_bare_anthropic_tokens(probe_env, monkeypatch):
    settings = '{"env":{"CLAUDE_CODE_OAUTH_TOKEN":"fake-settings-oauth-value"}}'
    monkeypatch.setenv("HOLON_AGENT_SETTINGS", settings)
    text = f"settings {settings} rejected; token sk-ant-oat01-fakeFAKE_value-123 expired"

    redacted = preflight.redact_output(text)

    assert settings not in redacted
    assert "fake-settings-oauth-value" not in redacted
    assert "sk-ant-" not in redacted
    assert "expired" in redacted


def test_probe_ok(probe_env):
    runner_fn = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr=""))
    res = run_preflight_probe("claude-agent", "claude-opus-5-5", runner_fn=runner_fn)
    assert res.ok
    assert res.image == "holon/agent-claude"


def test_probe_without_any_credential_reports_not_logged_in_without_calling(probe_env, monkeypatch):
    monkeypatch.delenv("HOLON_AGENT_KEY")
    runner_fn = MagicMock()
    res = run_preflight_probe("claude-agent", "claude-opus-5-5", runner_fn=runner_fn)
    assert res.status is PreflightStatus.NOT_LOGGED_IN
    assert "HOLON_AGENT_KEY" in res.hint
    runner_fn.assert_not_called()


def test_probe_timeout_removes_container(probe_env):
    calls = []

    def runner_fn(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    res = run_preflight_probe("antigravity-agent", "m", timeout=1, runner_fn=runner_fn)
    assert res.status is PreflightStatus.UNKNOWN
    assert "interactive login" in res.hint
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", name]


def test_probe_without_docker(probe_env):
    with patch("sandbox_executor.preflight.shutil.which", return_value=None):
        res = run_preflight_probe("antigravity-agent", "m")
    assert res.status is PreflightStatus.UNKNOWN
    assert "docker" in res.hint


def test_probe_skipped_in_pi_host_local_mode(probe_env, monkeypatch):
    with patch("sandbox_executor.preflight.local_llm.local_llm_requested", return_value=True):
        res = run_preflight_probe("pi-agent", "m", runner_fn=MagicMock())
    assert res.ok and res.skipped


# --- holon doctor -------------------------------------------------------------------------------------------------


def _run_main(argv: list[str]) -> int:
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


@pytest.mark.parametrize(("status", "code"), [(PreflightStatus.OK, 0), (PreflightStatus.EXPIRED_KEY, 1)])
def test_doctor_exit_codes_and_json(probe_env, capsys, status, code):
    with patch("sandbox_executor.cli.run_preflight_probe", return_value=_result(status)) as probe:
        assert _run_main(["holon", "doctor", "--json", "--timeout", "3"]) == code
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == {
        "value": "antigravity-agent",
        "source": "fallback",
        "reason": "no --agent, HOLON_AGENT or harness marker",
    }
    assert payload["model"]["value"] == "gemini-3.8-flash-medium"
    assert payload["model"]["source"] == "table"
    assert payload["preflight"]["status"] == status.value
    assert probe.call_args[1]["timeout"] == 3


def test_doctor_reports_harness_detection(probe_env, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    with patch("sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.OK)) as probe:
        assert _run_main(["holon", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "claude-agent (source: harness, harness detection, marker CLAUDECODE)" in out
    assert probe.call_args[0][:2] == ("claude-agent", "claude-opus-5-5")


def test_doctor_unmapped_runner(probe_env, monkeypatch, capsys):
    monkeypatch.setenv("HOLON_AGENT", "foo-agent")
    with patch("sandbox_executor.cli.run_preflight_probe") as probe:
        assert _run_main(["holon", "doctor"]) == 1
    probe.assert_not_called()
    assert "HOLON_AGENT" in capsys.readouterr().err


# --- gating of plan / execute -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("command", "target"), [("plan", "I-1/_"), ("execute", "I-1/P-2/_")])
def test_plan_and_execute_abort_before_container_on_failed_probe(probe_env, capsys, command, target):
    with (
        patch(
            "sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.QUOTA_EXHAUSTED)
        ) as probe,
        patch("sandbox_executor.cli.run_docker_container") as docker,
    ):
        assert _run_main(["holon", command, target]) == 1
    probe.assert_called_once()
    docker.assert_not_called()
    assert "QUOTA_EXHAUSTED" in capsys.readouterr().err


@pytest.mark.parametrize("bypass", ["flag", "env"])
def test_skip_preflight_bypasses_probe(probe_env, monkeypatch, bypass):
    argv = ["holon", "plan", "I-1/_"]
    if bypass == "flag":
        argv.append("--skip-preflight")
    else:
        monkeypatch.setenv("HOLON_SKIP_PREFLIGHT", "1")
    with (
        patch("sandbox_executor.cli.run_preflight_probe") as probe,
        patch("sandbox_executor.cli.run_docker_container", return_value=0) as docker,
    ):
        assert _run_main(argv) == 0
    probe.assert_not_called()
    docker.assert_called_once()


@pytest.fixture
def cli_logging():
    """Restore the package logger's level and handlers after a test that runs the CLI logging setup."""
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    saved_level, saved_handlers = package_logger.level, list(package_logger.handlers)
    package_logger.handlers = [h for h in saved_handlers if not isinstance(h, _CurrentStderrHandler)]
    package_logger.setLevel(logging.NOTSET)
    yield package_logger
    package_logger.handlers = saved_handlers
    package_logger.setLevel(saved_level)


def test_cli_prints_harness_and_skipped_preflight_info_on_stderr(probe_env, monkeypatch, capsys, cli_logging):
    monkeypatch.setenv("PI_CODING_AGENT", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    with (
        patch("sandbox_executor.cli.run_preflight_probe") as probe,
        patch("sandbox_executor.cli.run_docker_container", return_value=0),
    ):
        assert _run_main(["holon", "plan", "I-1/_", "--skip-preflight"]) == 0
    probe.assert_not_called()
    captured = capsys.readouterr()
    assert "Several agent harnesses detected; using pi from PI_CODING_AGENT and ignoring" in captured.err
    assert "CLAUDECODE (claude)" in captured.err
    assert "Pre-flight probe skipped" in captured.err
    assert captured.err.count("Agent runner resolved to pi-agent") == 1
    assert "Pre-flight" not in captured.out


def test_configure_cli_logging_is_idempotent_and_scoped(cli_logging):
    configure_cli_logging()
    configure_cli_logging()
    assert sum(isinstance(h, _CurrentStderrHandler) for h in cli_logging.handlers) == 1
    assert cli_logging.level == logging.INFO
    assert not any(isinstance(h, _CurrentStderrHandler) for h in logging.getLogger().handlers)


def test_plan_uses_harness_runner_and_its_default_model(probe_env, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    with (
        patch("sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.OK)),
        patch("sandbox_executor.cli.run_docker_container", return_value=0) as docker,
    ):
        assert _run_main(["holon", "plan", "I-1/_"]) == 0
    docker.assert_called_once_with(
        "planner",
        "holon/agent-claude",
        ["I-1/_", "claude-agent", "claude-opus-5-5"],
        agent_id="claude",
        token_reduce=False,
    )


def test_execute_with_unmapped_env_runner_fails_before_docker(probe_env, monkeypatch, capsys):
    monkeypatch.setenv("HOLON_AGENT", "foo-agent")
    with patch("sandbox_executor.cli.run_docker_container") as docker:
        assert _run_main(["holon", "execute", "I-1/P-2/_"]) == 1
    docker.assert_not_called()
    assert "Supported runners" in capsys.readouterr().err


def test_run_docker_container_disables_nested_preflight(probe_env):
    with (
        patch("sandbox_executor.cli.shutil.which", return_value="/usr/bin/docker"),
        patch("sandbox_executor.cli.resolve_target_repo_url", return_value="https://example.invalid/r.git"),
        patch("sandbox_executor.cli.find_github_token", return_value=None),
        patch("sandbox_executor.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run,
    ):
        from sandbox_executor.cli import run_docker_container

        assert run_docker_container("planner", "holon/agent-antigravity", [], agent_id="antigravity") == 0
    assert "HOLON_SKIP_PREFLIGHT=1" in run.call_args[0][0]


# --- review-loop routing ------------------------------------------------------------------------------------------


def test_bare_review_loop_still_runs_in_a_container(probe_env):
    with (
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        patch("sandbox_executor.cli.run_docker_container", return_value=0) as docker,
        patch("sandbox_executor.flow.run_review_loop_cli") as local,
    ):
        assert _run_main(["holon", "review-loop", "I-1/P-2/E-3/_"]) == 0
    local.assert_not_called()
    role, image, container_args = docker.call_args[0]
    assert (role, image) == ("review-loop", "holon/agent-antigravity")
    assert container_args[:3] == ["I-1/P-2/E-3/_", "antigravity-agent", "gemini-3.8-flash-medium"]


def test_local_review_loop_records_resolved_runner(probe_env, monkeypatch):
    monkeypatch.setenv("HOLON_AGENT", "pi-agent")
    with (
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        patch("sandbox_executor.flow.run_review_loop_cli", return_value=0) as local,
    ):
        assert _run_main(["holon", "review-loop", "I-1/P-2/E-3/_", "--local"]) == 0
    args = local.call_args[0][0]
    assert (args.agent, args.model) == ("pi-agent", "claude-opus-5-5")


# --- flow: abort before Stage 1 -----------------------------------------------------------------------------------


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def flow_repo(probe_env):
    repo = probe_env / "repo"
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True)
    for name in ("intents", "plans", "executions"):
        (ledger_dir / f"{name}.jsonl").write_text(json.dumps({"seed": name}) + "\n", encoding="utf-8")
    intent_file = repo / "intent.json"
    intent_file.write_text(json.dumps({"slug": "preflight-abort", "description": "d", "goal": "g"}), encoding="utf-8")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "seed")
    return repo, intent_file


def test_quota_exhausted_probe_aborts_flow_before_stage_one(flow_repo, capsys):
    repo, intent_file = flow_repo
    ledger_dir = repo / "holon-knowledge" / "ledger"
    before = {p.name: p.read_bytes() for p in ledger_dir.iterdir()}
    refs_before = _git(repo, "for-each-ref")

    with (
        patch(
            "sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.QUOTA_EXHAUSTED)
        ) as probe,
        patch("sandbox_executor.flow.run_intent_stage") as intent_stage,
        patch("sandbox_executor.cli.run_flow_pipeline") as pipeline,
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
        patch("sandbox_executor.flow.run_git") as flow_git,
    ):
        code = _run_main(["holon", "flow", str(intent_file), "--repo-dir", str(repo)])

    assert code == 1
    probe.assert_called_once()
    intent_stage.assert_not_called()
    pipeline.assert_not_called()
    flow_git.assert_not_called()
    assert _git(repo, "branch", "--list", "I-*").strip() == ""
    assert _git(repo, "for-each-ref") == refs_before
    assert {p.name: p.read_bytes() for p in ledger_dir.iterdir()} == before
    assert not os.path.exists(get_default_checkpoint_path(str(repo), "preflight-abort"))
    assert not (repo / ".holon").exists()
    assert "Aborting before any branch" in capsys.readouterr().err


def test_dry_run_flow_skips_probe(flow_repo):
    repo, intent_file = flow_repo
    with (
        patch("sandbox_executor.cli.run_preflight_probe") as probe,
        patch("sandbox_executor.cli.run_flow_pipeline", return_value={}) as pipeline,
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
    ):
        assert _run_main(["holon", "flow", str(intent_file), "--repo-dir", str(repo), "--dry-run"]) == 0
    probe.assert_not_called()
    ctx = pipeline.call_args[1]["context"]
    assert (ctx.agent, ctx.model) == ("antigravity-agent", "gemini-3.8-flash-medium")


def test_flow_explicit_runner_provenance_is_unchanged(flow_repo):
    repo, intent_file = flow_repo
    with (
        patch("sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.OK)) as probe,
        patch("sandbox_executor.cli.run_flow_pipeline", return_value={}) as pipeline,
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
    ):
        argv = ["holon", "flow", str(intent_file), "--repo-dir", str(repo)]
        assert _run_main([*argv, "--agent", "antigravity-agent", "--model", "gemini-3.8-flash-medium"]) == 0
    assert probe.call_args[0][:2] == ("antigravity-agent", "gemini-3.8-flash-medium")
    ctx = pipeline.call_args[1]["context"]
    assert (ctx.agent, ctx.model) == ("antigravity-agent", "gemini-3.8-flash-medium")


def _write_checkpoint(repo, stage: str) -> str:
    from sandbox_executor.flow import FlowCheckpoint, FlowContext, FlowStage

    ctx = FlowContext(
        repo_dir=str(repo),
        intent_data={"slug": "resume"},
        agent="antigravity-agent",
        model="gemini-3.8-flash-medium",
    )
    path = repo / "resume_checkpoint.json"
    checkpoint = FlowCheckpoint(intent_slug="resume", current_stage=FlowStage(stage), context=ctx)
    path.write_text(json.dumps(checkpoint.to_dict()), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (["--agent", "claude-agent"], ("claude-agent", "claude-opus-5-5")),
        (["--agent", "claude-agent", "--model", "m-x"], ("claude-agent", "m-x")),
        (["--agent", "antigravity"], ("antigravity-agent", "gemini-3.8-flash-medium")),
        ([], ("antigravity-agent", "gemini-3.8-flash-medium")),
    ],
)
def test_resume_with_new_runner_resolves_its_default_model(flow_repo, monkeypatch, extra, expected):
    repo, _ = flow_repo
    monkeypatch.delenv("HOLON_MODEL", raising=False)
    checkpoint = _write_checkpoint(repo, "plan")
    with (
        patch("sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.OK)) as probe,
        patch("sandbox_executor.cli.run_flow_pipeline", return_value={}) as pipeline,
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
    ):
        assert _run_main(["holon", "flow", "--checkpoint", checkpoint, "--repo-dir", str(repo), *extra]) == 0
    assert probe.call_args[0][:2] == expected
    ctx = pipeline.call_args[1]["context"]
    assert (ctx.agent, ctx.model) == expected


@pytest.mark.parametrize(
    ("stage", "probed"), [("plan", True), ("execute", True), ("review", False), ("calibrate", False)]
)
def test_resume_probes_only_when_an_agent_stage_remains(flow_repo, stage, probed):
    repo, _ = flow_repo
    checkpoint = _write_checkpoint(repo, stage)
    with (
        patch(
            "sandbox_executor.cli.run_preflight_probe", return_value=_result(PreflightStatus.QUOTA_EXHAUSTED)
        ) as probe,
        patch("sandbox_executor.cli.run_flow_pipeline", return_value={}) as pipeline,
        patch("sandbox_executor.cli.detect_holon_coherence", return_value=False),
    ):
        code = _run_main(["holon", "flow", "--checkpoint", checkpoint, "--repo-dir", str(repo)])
    assert probe.called is probed
    assert code == (1 if probed else 0)
    assert pipeline.called is not probed


def test_checkpoint_without_attribution_resumes_with_legacy_default():
    from sandbox_executor.flow import FlowContext

    ctx = FlowContext.from_dict({"repo_dir": ".", "token_reduce": False})
    assert (ctx.agent, ctx.model) == ("antigravity-agent", "gemini-3.8-flash-medium")


def test_module_constants():
    assert preflight.SKIP_ENV_VAR == "HOLON_SKIP_PREFLIGHT"
    assert preflight.TIMEOUT_ENV_VAR == "HOLON_PREFLIGHT_TIMEOUT"
