import json
import os
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor import preflight, preflight_cooldown
from sandbox_executor.cli.preflight import preflight_gate
from sandbox_executor.preflight import PreflightStatus, run_preflight_probe
from sandbox_executor.preflight_cooldown import (
    COOLDOWN_HINT_PREFIX,
    DEFAULT_COOLDOWN_SECONDS,
    MAX_COOLDOWN_SECONDS,
    MIN_COOLDOWN_SECONDS,
    clear_cooldown,
    marker_path,
    parse_cooldown_ttl,
    read_active_cooldown,
    record_cooldown,
)


@pytest.fixture
def isolated_cooldown_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("HOLON_ROLE", "HOLON_IN_SANDBOX", "HOLON_SKIP_PREFLIGHT", "HOLON_AGENT_SETTINGS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLON_AGENT_KEY", "sk-cooldown-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-cooldown-secret-token")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(preflight, "DEFAULT_RETRY_DELAY_SECONDS", 0.0)
    return tmp_path


def test_record_cooldown_writes_atomic_redacted_marker_with_strict_permissions(isolated_cooldown_home):
    now = 1_700_000_000.0
    output = "Org spend limit reached for key sk-cooldown-secret-value and sk-ant-cooldown-secret-token"

    marker = record_cooldown("claude-agent", "claude-opus-5-5", output, now=now, ttl_seconds=120.0)

    assert marker is not None
    assert marker.provider == "claude"
    assert marker.recorded_at == now
    assert marker.expires_at == now + 120.0
    path = marker_path("claude-agent")
    assert path == str(isolated_cooldown_home / ".holon" / "preflight-cooldown" / "claude.json")
    assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["provider"] == "claude"
    assert payload["expires_at"] == now + 120.0
    assert "sk-cooldown-secret-value" not in payload["output_excerpt"]
    assert "sk-ant-cooldown-secret-token" not in payload["output_excerpt"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("HTTP 429 Retry-After: 120", 120.0),
        ("retry-after=45s", 45.0),
        ("Rate limit hit; retry in 5m", 300.0),
        ("Usage limit reached; resets in 15 minutes", 900.0),
        ("Quota exceeded; try again in 2 hours", MAX_COOLDOWN_SECONDS),
        ("Retry-After: 3", MIN_COOLDOWN_SECONDS),
        ("Retry-After: 99999", MAX_COOLDOWN_SECONDS),
        ("429 Too Many Requests", DEFAULT_COOLDOWN_SECONDS),
        ("Organization spending limit reached", DEFAULT_COOLDOWN_SECONDS),
    ],
)
def test_parse_cooldown_ttl_handles_retry_after_relative_clamping_and_default(output, expected):
    assert parse_cooldown_ttl(output, now=1_700_000_000.0) == expected


def test_parse_cooldown_ttl_handles_iso_utc_reset_timestamp():
    base = datetime(2026, 11, 27, 22, 0, 0, tzinfo=UTC).timestamp()
    assert parse_cooldown_ttl("Spend limit resets at 2026-11-27T22:10:00Z", now=base) == 600.0
    assert parse_cooldown_ttl("Spend limit resets at 2026-11-27T21:55:00Z", now=base) == DEFAULT_COOLDOWN_SECONDS
    assert parse_cooldown_ttl("Spend limit resets at 2026-11-28T05:00:00Z", now=base) == MAX_COOLDOWN_SECONDS


def test_read_active_cooldown_expires_and_cleans_up_marker(isolated_cooldown_home):
    now = 1_700_000_000.0
    record_cooldown("codex-agent", "gpt-5.4", "429 Too Many Requests", now=now, ttl_seconds=60.0)
    path = marker_path("codex-agent")
    assert os.path.exists(path)

    active = read_active_cooldown("codex-agent", now=now + 59.0)
    assert active is not None
    assert active.provider == "codex"

    assert read_active_cooldown("codex-agent", now=now + 60.0) is None
    assert not os.path.exists(path)


@pytest.mark.parametrize(
    "raw_content",
    [
        "{not valid json",
        json.dumps({"provider": "claude"}),
        json.dumps(
            {
                "provider": "claude",
                "model": "m",
                "recorded_at": 100.0,
                "expires_at": 50.0,
                "reason": "quota_exhausted",
                "output_excerpt": "",
            }
        ),
        json.dumps(["unexpected", "list"]),
    ],
    ids=["invalid-json", "missing-fields", "inverted-window", "non-dict"],
)
def test_read_active_cooldown_fails_open_on_corrupt_marker(isolated_cooldown_home, raw_content):
    path = marker_path("claude-agent")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(raw_content)

    assert read_active_cooldown("claude-agent", now=1_700_000_000.0) is None
    assert not os.path.exists(path)


@pytest.mark.parametrize(
    "error_text",
    [
        "Error: org spend limit reached. Try again in 10m.",
        "HTTP 429 Too Many Requests",
    ],
    ids=["org-spend-limit", "http-429"],
)
def test_run_preflight_probe_records_short_circuits_resumes_and_clears(isolated_cooldown_home, monkeypatch, error_text):
    clock = [1_700_000_000.0]
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: clock[0])

    failing_runner = MagicMock(
        return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=error_text)
    )
    first = run_preflight_probe("claude-agent", "claude-opus-5-5", timeout=5, runner_fn=failing_runner)
    assert first.status is PreflightStatus.QUOTA_EXHAUSTED
    assert failing_runner.call_count == 1
    assert os.path.exists(marker_path("claude-agent"))

    second_runner = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr=""))
    second = run_preflight_probe("claude-agent", "claude-sonnet-5", timeout=5, runner_fn=second_runner)
    assert second.status is PreflightStatus.QUOTA_EXHAUSTED
    assert second.exit_code is None
    assert second.hint.startswith(COOLDOWN_HINT_PREFIX)
    assert second_runner.call_count == 0

    clock[0] += MAX_COOLDOWN_SECONDS + 1.0
    third = run_preflight_probe("claude-agent", "claude-opus-5-5", timeout=5, runner_fn=second_runner)
    assert third.ok
    assert second_runner.call_count == 1
    assert not os.path.exists(marker_path("claude-agent"))


def test_cooldown_is_isolated_per_provider(isolated_cooldown_home):
    record_cooldown("claude-agent", "claude-opus-5-5", "org spend limit reached", ttl_seconds=300.0)
    ok_runner = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr=""))

    codex_result = run_preflight_probe("codex-agent", "gpt-5.4", timeout=5, runner_fn=ok_runner)
    assert codex_result.ok
    assert ok_runner.call_count == 1
    assert read_active_cooldown("claude-agent") is not None
    clear_cooldown("claude-agent")
    assert read_active_cooldown("claude-agent") is None


@pytest.mark.parametrize("bypass", ["flag", "env"])
def test_preflight_gate_skip_bypasses_active_cooldown(isolated_cooldown_home, monkeypatch, bypass):
    record_cooldown("claude-agent", "claude-opus-5-5", "org spend limit reached", ttl_seconds=300.0)
    if bypass == "env":
        monkeypatch.setenv("HOLON_SKIP_PREFLIGHT", "1")
    with patch("sandbox_executor.cli.preflight.run_preflight_probe") as probe:
        result = preflight_gate("claude-agent", "claude-opus-5-5", skip=bypass == "flag")
    assert result is None
    probe.assert_not_called()


def test_preflight_gate_exempts_host_local_pi_from_active_cooldown(isolated_cooldown_home, monkeypatch):
    record_cooldown("pi-agent", "claude-sonnet-5", "HTTP 429 Too Many Requests", ttl_seconds=300.0)
    record_cooldown("claude-agent", "claude-opus-5-5", "org spend limit reached", ttl_seconds=300.0)
    monkeypatch.setenv("HOLON_LOCAL_LLM", "1")

    with patch("sandbox_executor.preflight.subprocess.run") as mock_run:
        pi_result = preflight_gate("pi-agent", "local-model")
        claude_result = preflight_gate("claude-agent", "claude-opus-5-5")

    assert pi_result is not None
    assert pi_result.ok
    assert pi_result.skipped
    mock_run.assert_not_called()
    assert read_active_cooldown("pi-agent") is not None

    assert claude_result is not None
    assert claude_result.status is PreflightStatus.QUOTA_EXHAUSTED
    assert claude_result.hint.startswith(COOLDOWN_HINT_PREFIX)

    monkeypatch.delenv("HOLON_LOCAL_LLM")
    cloud_pi_result = preflight_gate("pi-agent", "claude-sonnet-5")
    assert cloud_pi_result is not None
    assert cloud_pi_result.status is PreflightStatus.QUOTA_EXHAUSTED
    assert cloud_pi_result.hint.startswith(COOLDOWN_HINT_PREFIX)


def test_write_marker_atomic_closes_fd_and_unlinks_tmp_when_chmod_fails(isolated_cooldown_home, monkeypatch):
    path = marker_path("claude-agent")
    real_mkstemp = preflight_cooldown.tempfile.mkstemp
    real_chmod = os.chmod
    opened: list[tuple[int, str]] = []

    def tracking_mkstemp(*args, **kwargs):
        fd, tmp_path = real_mkstemp(*args, **kwargs)
        opened.append((fd, tmp_path))
        return fd, tmp_path

    def failing_chmod(target, mode):
        if opened and target == opened[-1][1]:
            raise OSError("chmod failed")
        return real_chmod(target, mode)

    monkeypatch.setattr(preflight_cooldown.tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(preflight_cooldown.os, "chmod", failing_chmod)

    assert record_cooldown("claude-agent", "claude-opus-5-5", "429 Too Many Requests", ttl_seconds=60.0) is None
    assert not os.path.exists(path)
    assert len(opened) == 1
    fd, tmp_path = opened[0]
    assert not os.path.exists(tmp_path)
    with pytest.raises(OSError):
        os.fstat(fd)
