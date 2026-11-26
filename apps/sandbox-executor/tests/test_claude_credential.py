"""The claude runner's credential: keychain discovery, type mapping and its wiring into the container env."""

import pathlib
import stat
from unittest.mock import patch

import pytest
from sandbox_executor import claude_credential, claude_keychain
from sandbox_executor.agent_runner import get_runner
from sandbox_executor.runners import RUNNER_SPECS

from tests.leak_assertions import assert_no_leak, make_probe

OAUTH_PREFIX = "sk-ant-" + "oat"
API_PREFIX = "sk-ant-" + "api"
DISPATCHER = pathlib.Path(__file__).parent.parent / "entrypoint" / "role_dispatcher.sh"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "HOLON_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_legacy_file(home, content, mode=0o600):
    path = home / ".holon" / "secrets" / "claude-oauth-token"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_discovery_reads_the_trimmed_value_from_the_keychain(home, fake_keychain, platform):
    value = OAUTH_PREFIX + make_probe("k")
    fake_keychain.platform = platform
    fake_keychain.lookup_override = (0, f"  {value}\n", "")

    assert claude_credential.resolve_claude_credential() == ("CLAUDE_CODE_OAUTH_TOKEN", value)
    assert claude_credential.resolve_claude_credential_source() == ("keychain", "CLAUDE_CODE_OAUTH_TOKEN", value)

    assert_no_leak(value, *fake_keychain.argvs)
    assert fake_keychain.store_calls == []


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [(OAUTH_PREFIX, "CLAUDE_CODE_OAUTH_TOKEN"), (API_PREFIX, "ANTHROPIC_API_KEY"), ("other-", "ANTHROPIC_API_KEY")],
)
def test_value_format_decides_the_variable(home, fake_keychain, prefix, expected):
    value = prefix + make_probe("t")
    fake_keychain.entry = value
    assert claude_credential.resolve_claude_credential() == (expected, value)


def test_explicit_environment_value_wins_and_holon_agent_key_is_ignored(home, fake_keychain, monkeypatch):
    fake_keychain.entry = OAUTH_PREFIX + make_probe("k")
    explicit = API_PREFIX + make_probe("env")
    monkeypatch.setenv("HOLON_AGENT_KEY", make_probe("generic"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", explicit)
    assert claude_credential.resolve_claude_credential() == ("ANTHROPIC_API_KEY", explicit)


@pytest.mark.parametrize("name", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "HOLON_AGENT_OAUTH_TOKEN"])
def test_a_value_in_the_environment_is_returned_without_asking_the_keychain(home, fake_keychain, monkeypatch, name):
    fake_keychain.entry = OAUTH_PREFIX + make_probe("k")
    value = OAUTH_PREFIX + make_probe("env")
    monkeypatch.setenv(name, value)

    assert claude_credential.resolve_claude_credential_source() == (name, "CLAUDE_CODE_OAUTH_TOKEN", value)

    assert fake_keychain.calls == []


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


def test_an_empty_or_unavailable_keychain_yields_no_credential(home, fake_keychain):
    assert claude_credential.resolve_claude_credential() is None
    fake_keychain.lookup_override = (0, "  \n", "")
    assert claude_credential.resolve_claude_credential() is None
    fake_keychain.lookup_override = None
    fake_keychain.entry = OAUTH_PREFIX + make_probe("k")
    fake_keychain.available = False
    assert claude_credential.resolve_claude_credential_source() is None
    fake_keychain.platform = "unsupported"
    assert claude_credential.resolve_claude_credential_source() is None


def test_the_resolution_hands_back_the_one_lookup_it_made(home, fake_keychain, monkeypatch):
    resolve = claude_credential.resolve_claude_credential_with_lookup

    resolved, lookup = resolve()
    assert resolved is None and lookup == claude_keychain.KeychainLookup(claude_keychain.LookupStatus.MISSING)
    assert len(fake_keychain.calls) == 1

    fake_keychain.available = False
    resolved, lookup = resolve()
    assert resolved is None and lookup.status is claude_keychain.LookupStatus.UNAVAILABLE

    value = OAUTH_PREFIX + make_probe("k")
    fake_keychain.available = True
    fake_keychain.entry = value
    resolved, lookup = resolve()
    assert resolved == ("keychain", "CLAUDE_CODE_OAUTH_TOKEN", value)
    assert lookup.status is claude_keychain.LookupStatus.FOUND and value not in repr(lookup)
    assert len(fake_keychain.calls) == 3

    # A variable that holds a value answers alone: the keychain is not asked, so there is no lookup to hand back.
    explicit = API_PREFIX + make_probe("env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", explicit)
    assert resolve() == (("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", explicit), None)
    assert len(fake_keychain.calls) == 3


def test_discovery_never_opens_the_plaintext_file(home, fake_keychain, monkeypatch):
    value = OAUTH_PREFIX + make_probe("f")
    path = _write_legacy_file(home, value + "\n")
    before = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    def refuse(_path):
        raise AssertionError("discovery must not read a token file")

    monkeypatch.setattr(claude_credential, "read_token_file", refuse)

    assert claude_credential.resolve_claude_credential() is None
    assert claude_credential.resolve_claude_credential_source() is None

    assert (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) == before
    assert fake_keychain.store_calls == []


def test_discovery_has_no_file_tier_left():
    for name in ("secrets_file_path", "read_secrets_file", "_warn_if_loose", "_WARNED_PATHS"):
        assert not hasattr(claude_credential, name), name


def test_claude_runner_no_longer_requires_holon_agent_key_but_others_do():
    assert "HOLON_AGENT_KEY" not in RUNNER_SPECS["claude"].api_key_env_vars
    assert set(RUNNER_SPECS["claude"].api_key_env_vars) == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    assert "HOLON_AGENT_KEY" in RUNNER_SPECS["pi"].api_key_env_vars
    # The runner exposes the spec's variables read-only; there is no second copy to drift.
    assert get_runner("claude").required_keys == list(RUNNER_SPECS["claude"].api_key_env_vars)


def test_claude_validation_ignores_holon_agent_key_and_names_the_keychain(home, monkeypatch, capsys):
    monkeypatch.setenv("HOLON_AGENT_KEY", make_probe("generic"))
    monkeypatch.setenv("HOLON_SECRET_BUNDLE_PATH", str(home / "absent.json"))
    with patch("os.path.exists", return_value=False), pytest.raises(SystemExit):
        get_runner("claude").validate()
    err = capsys.readouterr().err
    for command in claude_keychain.STORE_COMMANDS.values():
        assert command in err
    assert "claude setup-token" in err and "HOLON_AGENT_KEY' is not used" in err
    assert ".holon/secrets" not in err


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
    for var, runner in (("PI_API_KEY", "pi"), ("OPENAI_API_KEY", "codex")):
        assert f'export {var}="${{HOLON_AGENT_KEY}}"' in text, runner


@pytest.mark.parametrize("content", ["tok en\n", "token\n# a trailing comment\n"])
def test_multi_token_file_is_ignored_with_a_message_naming_the_path_not_the_value(tmp_path, content):
    value = OAUTH_PREFIX + make_probe("multi")
    path = tmp_path / "token-file"
    path.write_text(value + " " + content, encoding="utf-8")

    token, warning = claude_credential.read_token_file(str(path))

    assert token is None
    assert str(path) in warning and "single credential token" in warning
    assert_no_leak(value, warning)


def test_token_file_is_trimmed_and_a_missing_or_empty_one_reads_as_none(tmp_path):
    value = OAUTH_PREFIX + make_probe("one")
    path = tmp_path / "token-file"
    path.write_text(f"  {value}\n", encoding="utf-8")
    assert claude_credential.read_token_file(str(path)) == (value, None)
    path.write_text("  \n", encoding="utf-8")
    assert claude_credential.read_token_file(str(path)) == (None, None)
    assert claude_credential.read_token_file(str(tmp_path / "absent")) == (None, None)
