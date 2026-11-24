"""The one-time import of the plaintext claude token file into the OS secret store, and the pre-flight wording."""

import os
import stat

import pytest
from sandbox_executor import claude_keychain, claude_token_migration
from sandbox_executor.claude_token_migration import MigrationOutcome, MigrationStatus, migrate_legacy_token_file

from tests.leak_assertions import assert_no_leak, make_probe

OAUTH_PREFIX = "sk-ant-" + "oat"
PLATFORMS = ("darwin", "linux")
FILE_DISPLAY = "~/.holon/secrets/claude-oauth-token"
REAL_LEGACY_TOKEN_FILE_PATH = claude_token_migration.legacy_token_file_path


def _sample(label: str = "m") -> str:
    return OAUTH_PREFIX + make_probe(label)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _write_legacy_file(home, content, mode=0o600):
    path = home / ".holon" / "secrets" / "claude-oauth-token"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _state(path):
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def nothing_shows(fake_keychain, capsys, caplog):
    """A check that no stream, log record, message or argument list holds the value; it returns standard error."""

    def check(sample: str, outcome: MigrationOutcome) -> str:
        captured = capsys.readouterr()
        shown = (captured.out, captured.err, caplog.text, outcome.message, repr(outcome))
        assert_no_leak(sample, *shown, *fake_keychain.argvs)
        return captured.err

    return check


def test_the_legacy_path_is_under_the_current_home(home):
    expected = os.path.join(str(home), ".holon", "secrets", "claude-oauth-token")
    assert REAL_LEGACY_TOKEN_FILE_PATH() == expected
    assert claude_token_migration.legacy_token_file_path() == expected
    assert claude_token_migration.LEGACY_TOKEN_FILE_DISPLAY == FILE_DISPLAY


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_file_is_imported_then_deleted_and_the_output_says_so(home, fake_keychain, nothing_shows, platform):
    sample = _sample()
    fake_keychain.platform = platform
    path = _write_legacy_file(home, f"  {sample}\n")

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.MIGRATED
    assert fake_keychain.entry == sample
    assert not path.exists() and path.parent.is_dir()
    err = nothing_shows(sample, outcome)
    assert err == outcome.message + "\n"
    for expected in (FILE_DISPLAY, claude_keychain.store_name(), "'holon'", "'claude-oauth-token'", "Imported"):
        assert expected in err
    assert "deleted" in err and "HOLON_AGENT_OAUTH_TOKEN_FILE" in err


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("failure", ["write_fails", "write_is_silent_noop"])
def test_an_unverified_write_keeps_the_file_untouched(home, fake_keychain, nothing_shows, platform, failure):
    sample = _sample()
    fake_keychain.platform = platform
    setattr(fake_keychain, failure, True)
    path = _write_legacy_file(home, sample + "\n")
    before = _state(path)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert _state(path) == before
    assert fake_keychain.entry is None and len(fake_keychain.store_calls) == 1
    err = nothing_shows(sample, outcome)
    assert err.count("\n") == 1
    assert FILE_DISPLAY in err and "could not be verified" in err
    assert "left untouched" in err and "no longer read" in err
    assert claude_keychain.store_command() in err


@pytest.mark.parametrize("platform", [*PLATFORMS, "unsupported"])
def test_without_a_usable_store_the_file_is_kept_and_no_write_is_attempted(
    home, fake_keychain, nothing_shows, platform
):
    sample = _sample()
    fake_keychain.platform = platform
    fake_keychain.available = False
    path = _write_legacy_file(home, sample)
    before = _state(path)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert _state(path) == before
    assert fake_keychain.store_calls == []
    err = nothing_shows(sample, outcome)
    assert "no usable secret store" in err and "left untouched" in err
    if platform == "unsupported":
        assert fake_keychain.calls == [] and claude_keychain.store_commands_text() in err
    else:
        assert claude_keychain.store_command() in err


def test_without_a_file_nothing_happens_and_no_tool_is_started(home, fake_keychain, capsys):
    fake_keychain.entry = _sample()

    assert migrate_legacy_token_file() == MigrationOutcome(MigrationStatus.NOT_NEEDED, "")

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert fake_keychain.calls == []


def test_a_second_run_after_a_successful_import_does_nothing(home, fake_keychain, capsys):
    sample = _sample()
    _write_legacy_file(home, sample)
    assert migrate_legacy_token_file().status is MigrationStatus.MIGRATED
    capsys.readouterr()
    calls_after_first = len(fake_keychain.calls)

    assert migrate_legacy_token_file().status is MigrationStatus.NOT_NEEDED

    assert capsys.readouterr().err == ""
    assert len(fake_keychain.calls) == calls_after_first and fake_keychain.entry == sample


@pytest.mark.parametrize("platform", PLATFORMS)
def test_an_identical_stored_entry_deletes_the_file_without_writing(home, fake_keychain, nothing_shows, platform):
    sample = _sample()
    fake_keychain.platform = platform
    fake_keychain.entry = sample
    path = _write_legacy_file(home, sample + "\n")

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.MIGRATED
    assert not path.exists()
    assert fake_keychain.store_calls == [] and fake_keychain.entry == sample
    err = nothing_shows(sample, outcome)
    assert "already stored" in err and "nothing was written" in err and "deleted" in err


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_different_stored_entry_is_never_overwritten(home, fake_keychain, nothing_shows, platform):
    sample, stored = _sample(), _sample("s")
    fake_keychain.platform = platform
    fake_keychain.entry = stored
    path = _write_legacy_file(home, sample)
    before = _state(path)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert _state(path) == before
    assert fake_keychain.entry == stored and fake_keychain.store_calls == []
    err = nothing_shows(sample, outcome)
    assert_no_leak(stored, err, outcome.message)
    assert "already holds a different claude credential" in err and "left untouched" in err


@pytest.mark.parametrize("content", ["", "  \n", "two words\n", "first\nsecond\n"])
def test_a_file_without_a_single_token_is_kept_and_no_tool_is_started(home, fake_keychain, capsys, content):
    path = _write_legacy_file(home, content)
    before = _state(path)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert _state(path) == before and fake_keychain.calls == []
    err = capsys.readouterr().err
    assert "does not hold a single token" in err and "left untouched" in err


@pytest.mark.parametrize("damage", ["'", '"', "\\", ";", "$(x)"])
def test_a_token_outside_the_allow_list_is_kept_with_no_write(home, fake_keychain, nothing_shows, damage):
    sample = _sample() + damage + make_probe("z")
    path = _write_legacy_file(home, sample)
    before = _state(path)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert _state(path) == before
    assert fake_keychain.store_calls == [] and fake_keychain.entry is None
    err = nothing_shows(sample, outcome)
    assert "could not be verified" in err


def test_an_unreadable_file_is_kept(home, fake_keychain, capsys):
    path = _write_legacy_file(home, "")
    path.write_bytes(b"\xff\xfe\x00not text")
    before = _state(path)

    assert migrate_legacy_token_file().status is MigrationStatus.KEPT

    assert _state(path) == before and fake_keychain.calls == []
    assert "unreadable" in capsys.readouterr().err


def test_a_symbolic_link_or_a_directory_is_kept_and_not_followed(home, fake_keychain, capsys, tmp_path_factory):
    sample = _sample()
    target = tmp_path_factory.mktemp("elsewhere") / "token"
    target.write_text(sample, encoding="utf-8")
    link = home / ".holon" / "secrets" / "claude-oauth-token"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT and "not a regular file" in outcome.message
    assert link.is_symlink() and target.read_text(encoding="utf-8") == sample
    assert fake_keychain.calls == []

    link.unlink()
    link.mkdir()
    assert migrate_legacy_token_file().status is MigrationStatus.KEPT
    assert link.is_dir() and fake_keychain.calls == []
    assert_no_leak(sample, capsys.readouterr().err)


def test_a_failed_delete_after_a_verified_store_asks_for_a_manual_delete(
    home, fake_keychain, nothing_shows, monkeypatch
):
    sample = _sample()
    path = _write_legacy_file(home, sample)
    before = _state(path)

    def refuse(_path):
        raise OSError(f"cannot remove a file holding {sample}")

    monkeypatch.setattr(claude_token_migration.os, "remove", refuse)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.KEPT
    assert fake_keychain.entry == sample and _state(path) == before
    err = nothing_shows(sample, outcome)
    assert "delete it by hand" in err and "is stored in" in err and FILE_DISPLAY in err


def test_a_loose_file_mode_does_not_block_the_import(home, fake_keychain, nothing_shows):
    sample = _sample()
    path = _write_legacy_file(home, sample, mode=0o644)

    outcome = migrate_legacy_token_file()

    assert outcome.status is MigrationStatus.MIGRATED and not path.exists()
    nothing_shows(sample, outcome)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_missing_credential_message_tells_no_entry_from_no_store(home, fake_keychain, platform):
    fake_keychain.platform = platform
    no_entry = claude_token_migration.claude_missing_credential_message()
    fake_keychain.available = False
    no_store = claude_token_migration.claude_missing_credential_message()

    assert f"{claude_keychain.store_name()} has no entry for service 'holon', account 'claude-oauth-token'" in no_entry
    assert "no usable OS secret store was found" in no_store and "has no entry" not in no_store
    for message in (no_entry, no_store):
        assert "neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set" in message
        assert "claude setup-token" in message
        assert message.endswith(claude_keychain.store_command())
        assert ".holon/secrets" not in message and "HOLON_AGENT_KEY" not in message
        assert len(message) < 700  # pre-flight keeps the last 800 characters of its output


def test_the_missing_credential_message_shows_both_commands_on_an_unsupported_platform(home, fake_keychain):
    fake_keychain.platform = "unsupported"

    message = claude_token_migration.claude_missing_credential_message()

    assert "no usable OS secret store was found" in message
    assert message.endswith(claude_keychain.store_commands_text())
    assert len(message) < 700 and fake_keychain.calls == []


def test_the_missing_credential_message_reports_a_legacy_file_that_is_still_there(home, fake_keychain):
    sample = _sample()
    sentence = f"The plaintext file {FILE_DISPLAY} was not imported, was left untouched and is no longer read."
    assert sentence not in claude_token_migration.claude_missing_credential_message()

    _write_legacy_file(home, sample)
    message = claude_token_migration.claude_missing_credential_message()

    assert sentence in message and len(message) < 700
    assert_no_leak(sample, message)


@pytest.mark.parametrize("platform", [*PLATFORMS, "unsupported"])
def test_the_hint_orders_the_keychain_before_the_explicit_variables(home, fake_keychain, platform):
    fake_keychain.platform = platform
    command = claude_keychain.store_command() or claude_keychain.store_commands_text()

    hint = claude_token_migration.claude_not_logged_in_hint()

    assert (
        hint.index("claude setup-token")
        < hint.index(command)
        < hint.index("CLAUDE_CODE_OAUTH_TOKEN")
        < hint.index("ANTHROPIC_API_KEY")
        < hint.index("mount the session")
        < hint.index("--agent")
    )
    assert "HOLON_AGENT_KEY" not in hint and ".holon/secrets" not in hint
    assert fake_keychain.calls == []
