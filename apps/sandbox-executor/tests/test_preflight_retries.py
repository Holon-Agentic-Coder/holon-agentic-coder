"""Tests for pre-flight probe timeout handling, slow-exit OK classification, and transient retries."""

import logging
import os
import subprocess
from unittest.mock import MagicMock

import pytest
from sandbox_executor import preflight, preflight_cooldown
from sandbox_executor.preflight import PreflightStatus, run_preflight_probe

from tests.flow.sleep_patch import scoped_sleep_patch


@pytest.fixture
def probe_env(monkeypatch, tmp_path):
    """Isolated home, no sandbox markers, a dummy key and the real probe enabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("HOLON_ROLE", "HOLON_IN_SANDBOX", "HOLON_SKIP_PREFLIGHT", "HOLON_AGENT_SETTINGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_AGENT_KEY", "sk-test-very-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test-very-secret-value")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 0.0)
    return tmp_path


def _env_file_of(cmd):
    return cmd[cmd.index("--env-file") + 1]


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


def test_probe_timeout_cleanup_is_bounded_and_its_timeout_is_swallowed(probe_env, caplog):
    calls = []

    def runner_fn(cmd, **kwargs):
        calls.append((cmd, kwargs))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.preflight"):
        res = run_preflight_probe("antigravity-agent", "m", timeout=1, runner_fn=runner_fn)
    assert res.status is PreflightStatus.UNKNOWN
    assert "interactive login" in res.hint
    cleanup_cmd, cleanup_kwargs = calls[1]
    assert cleanup_cmd[:3] == ["docker", "rm", "-f"]
    assert cleanup_kwargs["timeout"] == preflight.CLEANUP_TIMEOUT_SECONDS
    assert "Could not remove pre-flight container" in caplog.text


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("OK\n", ""),
        (b"OK\n", b""),
        ("warning: slow shutdown\nOK.\n", ""),
        ("", b"OK!\n"),
        ("OK", "warning: slow shutdown\n"),
        (b"OK", b"warning: slow shutdown\n"),
    ],
    ids=[
        "str-stdout",
        "bytes-stdout",
        "multiline-stdout",
        "bytes-stderr",
        "unterminated-str-stdout-with-stderr",
        "unterminated-bytes-stdout-with-stderr",
    ],
)
def test_probe_timeout_with_ok_reply_classifies_as_ok_with_slow_exit_note(probe_env, caplog, stdout, stderr):
    calls = []

    def runner_fn(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.preflight"):
        res = run_preflight_probe("antigravity-agent", "m", timeout=7, runner_fn=runner_fn)

    assert res.status is PreflightStatus.OK
    assert res.ok
    assert res.exit_code is None
    assert "slow exit" in res.hint.lower()
    assert "7s" in res.hint
    assert "interactive login" not in res.hint.lower()
    assert "slow exit" in caplog.text.lower()
    run_calls = [cmd for cmd in calls if cmd[:2] == ["docker", "run"]]
    rm_calls = [cmd for cmd in calls if cmd[:3] == ["docker", "rm", "-f"]]
    assert len(run_calls) == 1
    assert rm_calls == [["docker", "rm", "-f", run_calls[0][run_calls[0].index("--name") + 1]]]


def test_probe_timeout_with_captured_non_ok_output_reports_timeout_without_interactive_login_hint(probe_env):
    def runner_fn(cmd, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="starting runner...\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    res = run_preflight_probe("antigravity-agent", "m", timeout=12, runner_fn=runner_fn, max_attempts=1)

    assert res.status is PreflightStatus.UNKNOWN
    assert "12s" in res.hint
    assert "interactive login" not in res.hint.lower()
    assert "starting runner..." in res.output_excerpt


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("401 Unauthorized", PreflightStatus.EXPIRED_KEY),
        ("429 Too Many Requests", PreflightStatus.QUOTA_EXHAUSTED),
    ],
)
def test_probe_timeout_preserves_known_auth_or_quota_error_without_retrying(probe_env, stderr, expected):
    run_calls = []

    def runner_fn(cmd, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            run_calls.append(cmd)
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="", stderr=stderr)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    res = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)

    assert res.status is expected
    assert len(run_calls) == 1


def test_probe_retries_transient_unknown_and_succeeds_on_later_attempt(probe_env, monkeypatch, isolated_env_file_dir):
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 1.5)
    run_calls = []
    env_file_paths = []

    def runner_fn(cmd, **kwargs):
        if cmd[:2] != ["docker", "run"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        run_calls.append(cmd)
        env_file_paths.append(_env_file_of(cmd))
        assert os.path.exists(env_file_paths[-1])
        attempt = len(run_calls)
        if attempt == 1:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="starting runner...\n", stderr="")
        if attempt == 2:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="OK\n", stderr="")

    with scoped_sleep_patch("sandbox_executor.preflight") as sleep_mock:
        res = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)

    assert res.ok
    assert len(run_calls) == 3
    names = [cmd[cmd.index("--name") + 1] for cmd in run_calls]
    assert len(set(names)) == 3
    assert sleep_mock.call_count == 2
    assert all(call.args == (1.5,) for call in sleep_mock.call_args_list)
    assert not os.path.exists(env_file_paths[0])
    assert os.listdir(isolated_env_file_dir) == []


def test_probe_exhausted_transient_unknown_retries_fail_closed(probe_env, monkeypatch, isolated_env_file_dir):
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 0.5)
    run_calls = []
    env_file_paths = []

    def runner_fn(cmd, **kwargs):
        run_calls.append(cmd)
        env_file_paths.append(_env_file_of(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with scoped_sleep_patch("sandbox_executor.preflight") as sleep_mock:
        res = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)

    assert res.status is PreflightStatus.UNKNOWN
    assert len(run_calls) == preflight.DEFAULT_MAX_ATTEMPTS == 3
    names = [cmd[cmd.index("--name") + 1] for cmd in run_calls]
    assert len(set(names)) == 3
    assert sleep_mock.call_count == 2
    assert all(call.args == (0.5,) for call in sleep_mock.call_args_list)
    assert not os.path.exists(env_file_paths[0])
    assert os.listdir(isolated_env_file_dir) == []


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("Error: not logged in. Please run agy login", PreflightStatus.NOT_LOGGED_IN),
        ("401 Unauthorized: invalid x-api-key", PreflightStatus.EXPIRED_KEY),
        ("Error 429: Too Many Requests", PreflightStatus.QUOTA_EXHAUSTED),
        ("invalid model selection (--model 'x' --effort 'medium')", PreflightStatus.UNKNOWN),
    ],
    ids=["not-logged-in", "expired-key", "quota-exhausted", "invalid-model"],
)
def test_probe_clear_auth_quota_and_model_errors_fail_immediately_without_retry(
    probe_env, monkeypatch, stderr, expected
):
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 2.0)
    runner_fn = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr))

    with scoped_sleep_patch("sandbox_executor.preflight") as sleep_mock:
        res = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)

    assert res.status is expected
    assert runner_fn.call_count == 1
    sleep_mock.assert_not_called()


@pytest.mark.parametrize("timed_out", [False, True], ids=["exit-nonzero", "timeout"])
def test_spend_limit_fails_immediately_records_cooldown_and_short_circuits_next_probe(
    probe_env, monkeypatch, timed_out
):
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 2.0)
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: 4000.0)
    stderr = "Organization spend limit reached. Retry-After: 180"
    run_calls = []

    def runner_fn(cmd, **kwargs):
        if cmd[:2] != ["docker", "run"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        run_calls.append(cmd)
        if timed_out:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="", stderr=stderr)
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=stderr)

    with scoped_sleep_patch("sandbox_executor.preflight") as sleep_mock:
        first = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)
        second = run_preflight_probe("antigravity-agent", "m", timeout=5, runner_fn=runner_fn)

    assert first.status is PreflightStatus.QUOTA_EXHAUSTED
    assert second.status is PreflightStatus.QUOTA_EXHAUSTED
    assert second.exit_code is None
    assert "cooldown" in second.hint.lower()
    assert len(run_calls) == 1
    sleep_mock.assert_not_called()
    marker_file = probe_env / ".holon" / "preflight" / "cooldowns" / "antigravity.json"
    assert marker_file.is_file()
