"""Tests for the pre-flight connectivity outage wait, driven by an injected sleep and clock (never a real sleep)."""

import logging
from unittest.mock import patch

import pytest
from sandbox_executor import preflight_cooldown
from sandbox_executor.cli.preflight import preflight_gate
from sandbox_executor.preflight import PreflightResult, PreflightStatus
from sandbox_executor.preflight_outage import backoff_schedule, connectivity_reason, wait_out_outage

_PROBE = "sandbox_executor.cli.preflight.run_preflight_probe"
_NO_OUTPUT_TIMEOUT_HINT = (
    "The probe did not finish within 60s; the runner may be waiting on an interactive login. "
    "Initialise the session interactively or raise HOLON_PREFLIGHT_TIMEOUT."
)


def _result(
    status: PreflightStatus = PreflightStatus.UNKNOWN, output: str = "", hint: str = "", exit_code: int | None = 1
) -> PreflightResult:
    return PreflightResult(
        status=status,
        agent="claude-agent",
        model="m",
        image="holon/agent-claude",
        exit_code=exit_code,
        output_excerpt=output,
        hint=hint,
    )


class _FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> _FakeTime:
    monkeypatch.delenv("HOLON_SKIP_PREFLIGHT", raising=False)
    return _FakeTime()


def _gate(fake: _FakeTime, skip: bool = False) -> PreflightResult | None:
    return preflight_gate("claude-agent", "m", skip, sleep=fake.sleep, clock=fake.clock)


def test_ok_first_probe_returns_unchanged_without_sleep(fake_time):
    ok = _result(PreflightStatus.OK, output="OK", exit_code=0)
    with patch(_PROBE, return_value=ok) as probe:
        assert _gate(fake_time) is ok
    assert probe.call_count == 1
    assert fake_time.sleeps == []


def test_connectivity_then_ok_waits_30_then_60_and_logs_each_wait(fake_time, caplog):
    ok = _result(PreflightStatus.OK, output="OK", exit_code=0)
    results = [_result(output="getaddrinfo ENOTFOUND api.anthropic.com"), _result(output="EAI_AGAIN"), ok]
    with (
        patch(_PROBE, side_effect=results) as probe,
        caplog.at_level(logging.WARNING, logger="sandbox_executor.preflight_outage"),
    ):
        assert _gate(fake_time) is ok
    assert fake_time.sleeps == [30, 60]
    assert probe.call_count == 3
    messages = [r.getMessage() for r in caplog.records if r.name == "sandbox_executor.preflight_outage"]
    assert len(messages) == 2
    assert all("DNS resolution failure" in m for m in messages)
    assert "attempt 2" in messages[0]
    assert "attempt 3" in messages[1]


def test_persistent_outage_gives_up_after_60_minutes(fake_time, capsys):
    down = _result(output="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.", exit_code=125)
    with patch(_PROBE, return_value=down) as probe:
        assert _gate(fake_time) is down
    sleeps = fake_time.sleeps
    assert sum(sleeps) == 3600
    assert max(sleeps) <= 300
    assert sleeps[:5] == [30, 60, 120, 240, 300]
    assert probe.call_count == len(sleeps) + 1
    err = capsys.readouterr().err
    assert "UNKNOWN" in err
    assert "Connectivity outage lasted 1h 0m 0s" in err
    assert "Aborting before any branch" in err
    assert err.index("UNKNOWN") < err.index("Connectivity outage lasted") < err.index("Aborting before any branch")


@pytest.mark.parametrize(
    "result",
    [
        _result(PreflightStatus.QUOTA_EXHAUSTED, output="429 Too Many Requests"),
        _result(PreflightStatus.EXPIRED_KEY, output="401 Unauthorized"),
        _result(PreflightStatus.NOT_LOGGED_IN, output="Not logged in"),
        _result(output="Error: invalid model 'm'"),
        _result(hint="'docker' CLI command not found. Please install Docker.", exit_code=None),
        _result(hint="Unsupported agent runner 'x'.", exit_code=None),
    ],
    ids=["quota", "expired-key", "not-logged-in", "model-name", "missing-docker-cli", "unsupported-runner"],
)
def test_non_connectivity_failure_returns_on_first_probe(fake_time, capsys, result):
    with patch(_PROBE, return_value=result) as probe:
        assert _gate(fake_time) is result
    assert probe.call_count == 1
    assert fake_time.sleeps == []
    assert "Connectivity outage lasted" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "result",
    [
        _result(output="getaddrinfo ENOTFOUND api.anthropic.com"),
        _result(output="getaddrinfo EAI_AGAIN api.openai.com"),
        _result(output="curl: (6) Could not resolve host: api.anthropic.com"),
        _result(output="connect ECONNREFUSED 127.0.0.1:443"),
        _result(output="read ECONNRESET"),
        _result(output="connect ETIMEDOUT 104.18.0.1:443"),
        _result(output="dial tcp: Network is unreachable"),
        _result(output="Cannot connect to the Docker daemon. Is the docker daemon running?"),
        _result(exit_code=None, hint=_NO_OUTPUT_TIMEOUT_HINT),
        _result(exit_code=1),
    ],
)
def test_connectivity_failures_are_classified(result):
    assert connectivity_reason(result) is not None


@pytest.mark.parametrize(
    "result",
    [
        _result(output="429 Too Many Requests"),
        _result(output="401 Unauthorized"),
        _result(output="invalid x-api-key"),
        _result(output="not logged in"),
        _result(output="read ECONNRESET\n401 Unauthorized"),
        _result(output="partial output", exit_code=None, hint="The probe timed out after 60s after capturing output."),
        _result(PreflightStatus.EXPIRED_KEY, output="ECONNRESET"),
        _result(PreflightStatus.OK, output="OK", exit_code=0),
    ],
)
def test_quota_auth_and_other_results_are_not_connectivity(result):
    assert connectivity_reason(result) is None


def test_backoff_schedule_doubles_to_cap_and_sums_to_one_hour():
    schedule = list(backoff_schedule())
    assert schedule[:5] == [30, 60, 120, 240, 300]
    assert max(schedule) <= 300
    assert sum(schedule) == 3600


@pytest.mark.parametrize("bypass", ["flag", "env"])
def test_skip_bypasses_probe_and_wait(fake_time, monkeypatch, bypass):
    if bypass == "env":
        monkeypatch.setenv("HOLON_SKIP_PREFLIGHT", "1")
    with patch(_PROBE) as probe:
        assert _gate(fake_time, skip=bypass == "flag") is None
    probe.assert_not_called()
    assert fake_time.sleeps == []


def test_spend_limit_and_cooldown_short_circuit_are_not_connectivity():
    spend_limit = _result(output="Your credit balance is too low to access the API")
    marker = preflight_cooldown.CooldownMarker(
        agent_id="claude",
        agent="claude-agent",
        model="m",
        reason="spend_limit",
        recorded_at=1000.0,
        expires_at=1300.0,
        output_excerpt="Organization spend limit reached",
    )
    short_circuit = preflight_cooldown.build_short_circuit_result(
        marker, "claude-agent", "m", "holon/agent-claude", now=1050.0
    )
    assert connectivity_reason(spend_limit) is None
    assert connectivity_reason(short_circuit) is None


def test_preflight_gate_records_cooldown_and_short_circuits_subsequent_call(fake_time, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: 2000.0)
    quota = _result(PreflightStatus.QUOTA_EXHAUSTED, output="Organization spend limit reached. Retry-After: 240")
    with patch(_PROBE, return_value=quota) as probe:
        first = _gate(fake_time)
        second = _gate(fake_time)
    assert first is quota
    assert second is not None
    assert second.status is PreflightStatus.QUOTA_EXHAUSTED
    assert second.exit_code is None
    assert "cooldown" in second.hint.lower()
    assert probe.call_count == 1
    assert fake_time.sleeps == []
    assert "Aborting before any branch" in capsys.readouterr().err


def test_wait_out_outage_short_circuits_when_cooldown_appears_during_sleep(fake_time, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: 3000.0)
    outage = _result(output="getaddrinfo ENOTFOUND api.anthropic.com")
    quota = _result(PreflightStatus.QUOTA_EXHAUSTED, output="Billing hard limit reached. Retry-After: 300")

    def sleep_and_record(seconds: float) -> None:
        fake_time.sleep(seconds)
        preflight_cooldown.record_cooldown(quota, now=3000.0)

    calls = 0

    def probe() -> PreflightResult:
        nonlocal calls
        calls += 1
        return outage

    outcome = wait_out_outage(probe, outage, fake_time.clock(), sleep=sleep_and_record, clock=fake_time.clock)
    assert outcome.result.status is PreflightStatus.QUOTA_EXHAUSTED
    assert outcome.result.exit_code is None
    assert outcome.result.short_circuited is True
    assert outcome.gave_up is False
    assert calls == 0
    assert fake_time.sleeps == [30]


def test_preflight_gate_does_not_extend_cooldown_when_wait_out_outage_short_circuits(fake_time, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    wall = {"now": 3000.0}
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: wall["now"])
    outage = _result(output="getaddrinfo ENOTFOUND api.anthropic.com")
    quota = _result(PreflightStatus.QUOTA_EXHAUSTED, output="Billing hard limit reached. Retry-After: 300")

    def sleep_and_record(seconds: float) -> None:
        fake_time.sleep(seconds)
        preflight_cooldown.record_cooldown(quota, now=3000.0)
        wall["now"] = 3120.0

    with patch(_PROBE, return_value=outage) as probe:
        result = preflight_gate("claude-agent", "m", sleep=sleep_and_record, clock=fake_time.clock)

    assert result is not None
    assert result.status is PreflightStatus.QUOTA_EXHAUSTED
    assert result.short_circuited is True
    assert probe.call_count == 1
    marker = preflight_cooldown.read_active_cooldown("claude-agent", now=3120.0)
    assert marker is not None
    assert marker.recorded_at == 3000.0
    assert marker.expires_at == 3300.0


def test_preflight_gate_skips_cooldown_short_circuit_for_pi_local_llm(fake_time, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(preflight_cooldown.time, "time", lambda: 4000.0)
    pi_quota = PreflightResult(
        status=PreflightStatus.QUOTA_EXHAUSTED,
        agent="pi",
        model="cloud-model",
        image="holon/agent-pi",
        exit_code=1,
        output_excerpt="Organization spend limit reached. Retry-After: 300",
        hint="",
    )
    preflight_cooldown.record_cooldown(pi_quota, now=4000.0)
    monkeypatch.setattr("sandbox_executor.local_llm.local_llm_requested", lambda: True)

    result = preflight_gate("pi", "local-model", sleep=fake_time.sleep, clock=fake_time.clock)

    assert result is not None
    assert result.ok
    assert result.skipped is True
    assert preflight_cooldown.read_active_cooldown("pi", now=4000.0) is not None
