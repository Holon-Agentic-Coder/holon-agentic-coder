"""Pre-flight for claude with the OS keychain: the fail-closed remedy and the one-time import of the plaintext file.

Every probe passes a `runner_fn`, so no test starts Docker; the keychain is the in-memory fake of `conftest.py`.
"""

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from sandbox_executor import claude_keychain, preflight
from sandbox_executor.preflight import PreflightStatus, run_preflight_probe

from tests.leak_assertions import assert_no_leak, make_probe

OAUTH_PREFIX = "sk-ant-" + "oat"
PLATFORMS = ("darwin", "linux")
MODEL = "claude-opus-5-5"
NO_ENTRY = "has no entry for service 'holon', account 'claude-oauth-token'"
NO_STORE = "no usable OS secret store was found"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home with no sandbox marker and no claude credential in the environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (
        "HOLON_ROLE",
        "HOLON_IN_SANDBOX",
        "HOLON_SKIP_PREFLIGHT",
        "HOLON_AGENT_SETTINGS",
        "HOLON_AGENT_KEY",
        "HOLON_AGENT_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_legacy_file(home, content):
    path = home / ".holon" / "secrets" / "claude-oauth-token"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _answering_runner():
    """A stand-in for the docker call that records the env-file lines and answers like a ready runner."""
    seen: dict[str, list[str]] = {}

    def run(cmd, **_kwargs):
        seen["cmd"] = list(cmd)
        with open(cmd[cmd.index("--env-file") + 1], encoding="utf-8") as fh:
            seen["lines"] = fh.read().splitlines()
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="OK\n", stderr="")

    return MagicMock(side_effect=run), seen


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize(("store_answers", "cause"), [(True, NO_ENTRY), (False, NO_STORE)])
def test_probe_fails_closed_with_the_platform_remedy(home, fake_keychain, platform, store_answers, cause):
    fake_keychain.platform = platform
    fake_keychain.available = store_answers
    runner_fn = MagicMock()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.NOT_LOGGED_IN
    runner_fn.assert_not_called()
    one_liner = claude_keychain.STORE_COMMANDS[platform]
    for text in (res.output_excerpt, res.hint, res.format_report()):
        assert one_liner in text
        assert "claude setup-token" in text
        assert ".holon/secrets" not in text
    other = NO_STORE if cause == NO_ENTRY else NO_ENTRY
    assert cause in res.output_excerpt and other not in res.output_excerpt
    assert fake_keychain.store_calls == []


def test_probe_on_an_unsupported_platform_shows_both_remedies_and_starts_no_tool(home, fake_keychain):
    fake_keychain.platform = "unsupported"
    runner_fn = MagicMock()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.NOT_LOGGED_IN
    runner_fn.assert_not_called()
    for text in (res.output_excerpt, res.hint, res.format_report()):
        for one_liner in claude_keychain.STORE_COMMANDS.values():
            assert one_liner in text
        assert "claude setup-token" in text
    assert NO_STORE in res.output_excerpt
    assert fake_keychain.calls == []


@pytest.mark.parametrize("platform", PLATFORMS)
def test_probe_imports_the_legacy_file_then_runs_with_the_keychain_value(
    home, fake_keychain, isolated_env_file_dir, capsys, platform
):
    sample = OAUTH_PREFIX + make_probe("p")
    fake_keychain.platform = platform
    path = _write_legacy_file(home, sample + "\n")
    runner_fn, seen = _answering_runner()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.OK
    assert not path.exists()
    assert fake_keychain.entry == sample
    assert f"CLAUDE_CODE_OAUTH_TOKEN={sample}" in seen["lines"]
    err = capsys.readouterr().err
    assert "Imported the claude credential from ~/.holon/secrets/claude-oauth-token" in err
    assert "the plaintext file was deleted" in err
    assert_no_leak(sample, err, seen["cmd"], *fake_keychain.argvs)


@pytest.mark.parametrize("failure", ["write_fails", "write_is_silent_noop"])
def test_a_failed_import_with_no_other_credential_fails_closed_and_keeps_the_file(home, fake_keychain, capsys, failure):
    sample = OAUTH_PREFIX + make_probe("f")
    setattr(fake_keychain, failure, True)
    path = _write_legacy_file(home, sample + "\n")
    before = path.read_bytes()
    runner_fn = MagicMock()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.NOT_LOGGED_IN
    runner_fn.assert_not_called()
    assert path.read_bytes() == before
    assert fake_keychain.entry is None
    for wording in ("was not imported", "was left untouched", "is no longer read"):
        assert wording in res.output_excerpt
    assert claude_keychain.store_command() in res.output_excerpt
    assert "could not be verified" in capsys.readouterr().err


def test_a_failed_import_does_not_block_a_probe_that_has_an_explicit_credential(
    home, fake_keychain, isolated_env_file_dir, monkeypatch, capsys
):
    sample = OAUTH_PREFIX + make_probe("f")
    explicit = "sk-ant-" + "api" + make_probe("e")
    monkeypatch.setenv("ANTHROPIC_API_KEY", explicit)
    fake_keychain.write_fails = True
    path = _write_legacy_file(home, sample + "\n")
    before = path.read_bytes()
    runner_fn, seen = _answering_runner()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.OK
    assert path.read_bytes() == before
    assert f"ANTHROPIC_API_KEY={explicit}" in seen["lines"]
    assert not any(sample in line for line in seen["lines"])
    assert "left untouched" in capsys.readouterr().err


def test_an_already_migrated_host_probes_without_a_store_command_or_a_migration_line(
    home, fake_keychain, isolated_env_file_dir, capsys
):
    sample = OAUTH_PREFIX + make_probe("a")
    fake_keychain.entry = sample
    runner_fn, seen = _answering_runner()

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    assert res.status is PreflightStatus.OK
    assert f"CLAUDE_CODE_OAUTH_TOKEN={sample}" in seen["lines"]
    assert fake_keychain.store_calls == []
    captured = capsys.readouterr()
    assert "claude-oauth-token" not in captured.err and "claude-oauth-token" not in captured.out


def test_a_mounted_session_still_imports_the_legacy_file(home, fake_keychain, capsys):
    sample = OAUTH_PREFIX + make_probe("s")
    path = _write_legacy_file(home, sample + "\n")

    assert preflight._has_host_credential("claude", ["-v", "/host/session:/home/agent/.claude"]) is True

    assert not path.exists()
    assert fake_keychain.entry == sample
    assert "the plaintext file was deleted" in capsys.readouterr().err


@pytest.mark.parametrize("agent_id", ["codex", "antigravity", "pi"])
def test_another_runner_leaves_the_legacy_file_and_the_keychain_alone(home, fake_keychain, capsys, agent_id):
    path = _write_legacy_file(home, OAUTH_PREFIX + make_probe("o") + "\n")
    before = path.read_bytes()

    preflight._has_host_credential(agent_id, [])

    assert path.read_bytes() == before
    assert fake_keychain.calls == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("outcome", ["imported", "write_fails", "different_entry", "no_store"])
def test_no_part_of_the_value_is_shown_whatever_the_import_did(home, fake_keychain, capsys, caplog, outcome):
    # The unique part is checked alone: the hint names the public `sk-ant-oat...` prefix on purpose.
    unique = make_probe("u")
    kept_entry = make_probe("d")
    fake_keychain.write_fails = outcome == "write_fails"
    fake_keychain.available = outcome != "no_store"
    fake_keychain.entry = OAUTH_PREFIX + kept_entry if outcome == "different_entry" else None
    _write_legacy_file(home, OAUTH_PREFIX + unique + "\n")
    # The runner answers with an error, so the report prints the output excerpt on every path.
    answer = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="401 unauthorized")
    runner_fn = MagicMock(return_value=answer)

    res = run_preflight_probe("claude-agent", MODEL, runner_fn=runner_fn)

    captured = capsys.readouterr()
    shown = (res.output_excerpt, res.hint, res.format_report(), json.dumps(res.to_dict()), captured.out, captured.err)
    docker_argvs = [call.args[0] for call in runner_fn.call_args_list]
    for value in (unique, kept_entry):
        assert_no_leak(value, *shown, caplog.text, *fake_keychain.argvs, *docker_argvs)
    assert runner_fn.called == (outcome in ("imported", "different_entry"))
