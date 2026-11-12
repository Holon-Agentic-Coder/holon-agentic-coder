"""The claude runner's credential: secrets-file discovery, type mapping and its wiring into the container env."""

import os
import pathlib
import stat
from unittest.mock import patch

import pytest
from sandbox_executor import claude_credential
from sandbox_executor.agent_runner import get_runner

from tests.leak_assertions import assert_no_fragment, make_probe

OAUTH_PREFIX = "sk-ant-" + "oat"
API_PREFIX = "sk-ant-" + "api"
DISPATCHER = pathlib.Path(__file__).parent.parent / "entrypoint" / "role_dispatcher.sh"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "HOLON_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_secret(home, content, mode=0o600):
    path = home / ".holon" / "secrets" / "claude-oauth-token"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_discovery_reads_the_trimmed_file_and_leaves_it_in_place(home):
    value = OAUTH_PREFIX + make_probe("file")
    path = _write_secret(home, f"  {value}\n")
    before = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    assert claude_credential.resolve_claude_credential() == ("CLAUDE_CODE_OAUTH_TOKEN", value)

    assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) == before


def test_secrets_path_is_target_native(home):
    assert claude_credential.secrets_file_path() == os.path.join(str(home), ".holon", "secrets", "claude-oauth-token")


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [(OAUTH_PREFIX, "CLAUDE_CODE_OAUTH_TOKEN"), (API_PREFIX, "ANTHROPIC_API_KEY"), ("other-", "ANTHROPIC_API_KEY")],
)
def test_value_format_decides_the_variable(home, prefix, expected):
    value = prefix + make_probe("t")
    _write_secret(home, value)
    assert claude_credential.resolve_claude_credential() == (expected, value)


def test_explicit_environment_value_wins_and_holon_agent_key_is_ignored(home, monkeypatch):
    _write_secret(home, OAUTH_PREFIX + make_probe("file"))
    explicit = API_PREFIX + make_probe("env")
    monkeypatch.setenv("HOLON_AGENT_KEY", make_probe("generic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", explicit)
    assert claude_credential.resolve_claude_credential() == ("ANTHROPIC_API_KEY", explicit)


def test_explicit_value_is_retyped_by_its_format_in_either_variable(home, monkeypatch):
    oat = OAUTH_PREFIX + make_probe("env-oat")
    monkeypatch.setenv("ANTHROPIC_API_KEY", oat)
    assert claude_credential.resolve_claude_credential() == ("CLAUDE_CODE_OAUTH_TOKEN", oat)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    api = API_PREFIX + make_probe("env-api")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", api)
    assert claude_credential.resolve_claude_credential() == ("ANTHROPIC_API_KEY", api)


def test_holon_agent_key_alone_is_not_a_claude_credential(home, monkeypatch):
    monkeypatch.setenv("HOLON_AGENT_KEY", make_probe("generic"))
    assert claude_credential.resolve_claude_credential() is None


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_missing_or_empty_file_yields_no_credential(home, content):
    if content is not None:
        _write_secret(home, content)
    assert claude_credential.resolve_claude_credential() is None


def test_loose_mode_warns_with_path_and_mode_but_never_the_value(home, capsys):
    value = OAUTH_PREFIX + make_probe("loose")
    path = _write_secret(home, value, mode=0o644)

    assert claude_credential.read_secrets_file() == value

    err = capsys.readouterr().err
    assert str(path) in err and "0644" in err
    assert_no_fragment(err, value)


def test_owner_only_mode_does_not_warn(home, capsys):
    _write_secret(home, OAUTH_PREFIX + make_probe("ok"))
    claude_credential.read_secrets_file()
    assert capsys.readouterr().err == ""


def test_claude_runner_no_longer_requires_holon_agent_key_but_others_do():
    assert "HOLON_AGENT_KEY" not in get_runner("claude").required_keys
    assert set(get_runner("claude").required_keys) == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    assert "HOLON_AGENT_KEY" in get_runner("pi").required_keys


def test_claude_validation_ignores_holon_agent_key_and_names_the_secrets_file(home, monkeypatch, capsys):
    monkeypatch.setenv("HOLON_AGENT_KEY", make_probe("generic"))
    monkeypatch.setenv("HOLON_SECRET_BUNDLE_PATH", str(home / "absent.json"))
    with patch("os.path.exists", return_value=False), pytest.raises(SystemExit):
        get_runner("claude").validate()
    err = capsys.readouterr().err
    assert "~/.holon/secrets/claude-oauth-token" in err and "HOLON_AGENT_KEY' is not used" in err


def test_claude_validation_passes_with_a_claude_variable(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH_PREFIX + make_probe("x"))
    monkeypatch.setenv("HOLON_SECRET_BUNDLE_PATH", str(home / "absent.json"))
    with patch("os.path.exists", return_value=False):
        get_runner("claude").validate()


def test_dispatcher_claude_branch_no_longer_maps_holon_agent_key():
    text = DISPATCHER.read_text(encoding="utf-8")
    claude_branch = text.split("        claude)", 1)[1].split(";;", 1)[0]
    assert "HOLON_AGENT_KEY" not in claude_branch.replace("The claude runner never consults HOLON_AGENT_KEY", "")
    assert "export ANTHROPIC_API_KEY" not in claude_branch
    for var, runner in (("PI_API_KEY", "pi"), ("OPENAI_API_KEY", "codex"), ("GEMINI_API_KEY", "gemini")):
        assert f'export {var}="${{HOLON_AGENT_KEY}}"' in text, runner


def test_loose_mode_warns_once_per_process(home, capsys):
    _write_secret(home, OAUTH_PREFIX + make_probe("once"), mode=0o644)
    for _ in range(3):
        claude_credential.read_secrets_file()
    assert capsys.readouterr().err.count("expected 0600") == 1


@pytest.mark.parametrize("content", ["tok en\n", "token\n# a trailing comment\n"])
def test_multi_token_file_is_ignored_with_a_message_naming_the_path_not_the_value(home, capsys, content):
    path = _write_secret(home, OAUTH_PREFIX + make_probe("multi") + " " + content)
    assert claude_credential.read_secrets_file() is None
    err = capsys.readouterr().err
    assert str(path) in err and "single credential token" in err
    assert_no_fragment(err, make_probe("multi"))
