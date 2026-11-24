"""The OS secret store seam for the claude credential: lookup, verified store, and that the value stays on stdin."""

import subprocess
from pathlib import Path

import pytest
from sandbox_executor import claude_keychain
from sandbox_executor.claude_keychain import KeychainLookup, LookupStatus

from tests.fake_keychain import REAL_CURRENT_PLATFORM, REAL_RUN_KEYCHAIN_TOOL
from tests.leak_assertions import assert_no_leak, make_probe

REPO = Path(__file__).resolve().parents[3]
OAUTH_PREFIX = "sk-ant-" + "oat"
PLATFORMS = ("darwin", "linux")
LOOKUP_ARGV = {
    "darwin": ["security", "find-generic-password", "-s", "holon", "-a", "claude-oauth-token", "-w"],
    "linux": ["secret-tool", "lookup", "service", "holon", "account", "claude-oauth-token"],
}
STORE_ARGV = {
    "darwin": ["security", "-i"],
    "linux": [
        "secret-tool",
        "store",
        "--label=Holon claude credential",
        "service",
        "holon",
        "account",
        "claude-oauth-token",
    ],
}


def _sample(label: str = "k") -> str:
    return OAUTH_PREFIX + make_probe(label)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_lookup_returns_the_trimmed_value_with_the_documented_command(fake_keychain, platform):
    sample = _sample()
    fake_keychain.platform = platform
    fake_keychain.lookup_override = (0, f"  {sample}\n", "")

    assert claude_keychain.lookup_token() == KeychainLookup(LookupStatus.FOUND, sample)

    assert fake_keychain.calls == [(LOOKUP_ARGV[platform], None)]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_lookup_reports_missing_for_an_empty_store(fake_keychain, platform):
    fake_keychain.platform = platform
    assert claude_keychain.lookup_token() == KeychainLookup(LookupStatus.MISSING)
    fake_keychain.lookup_override = (0, " \n", "")
    assert claude_keychain.lookup_token() == KeychainLookup(LookupStatus.MISSING)


@pytest.mark.parametrize(
    ("platform", "override"),
    [
        ("darwin", None),  # the tool is missing or timed out: the seam returns None
        ("linux", None),
        ("darwin", (36, "", "security: SecKeychainSearchCopyNext: User interaction is not allowed.\n")),
        ("darwin", (1, "", "")),
        ("linux", (1, "", "secret-tool: Cannot autolaunch D-Bus without X11 $DISPLAY\n")),
        ("linux", (2, "", "")),
    ],
)
def test_lookup_reports_unavailable_when_no_store_answers(fake_keychain, platform, override):
    fake_keychain.platform = platform
    fake_keychain.entry = _sample()
    fake_keychain.available = override is not None
    fake_keychain.lookup_override = override

    assert claude_keychain.lookup_token() == KeychainLookup(LookupStatus.UNAVAILABLE)


def test_a_failed_lookup_never_returns_what_the_tool_printed(fake_keychain):
    sample = _sample()
    fake_keychain.lookup_override = (2, sample, "")
    assert claude_keychain.lookup_token().token == ""


def test_an_unsupported_platform_has_no_store_and_starts_nothing(fake_keychain):
    fake_keychain.platform = "unsupported"

    assert claude_keychain.lookup_token() == KeychainLookup(LookupStatus.UNAVAILABLE)
    assert claude_keychain.store_token(_sample()) is False

    assert fake_keychain.calls == []


@pytest.mark.parametrize("platform", PLATFORMS)
def test_store_sends_the_value_on_standard_input_only_and_reads_it_back(fake_keychain, platform):
    sample = _sample()
    fake_keychain.platform = platform

    assert claude_keychain.store_token(sample) is True

    assert fake_keychain.entry == sample
    assert fake_keychain.argvs == [STORE_ARGV[platform], LOOKUP_ARGV[platform]]
    assert_no_leak(sample, *fake_keychain.argvs)
    stdin = fake_keychain.calls[0][1]
    expected_stdin = f"add-generic-password -U -s holon -a claude-oauth-token -w {sample}\n"
    assert stdin == (expected_stdin if platform == "darwin" else sample)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("failure", ["write_fails", "write_is_silent_noop"])
def test_store_is_false_when_the_write_did_not_happen(fake_keychain, platform, failure):
    fake_keychain.platform = platform
    setattr(fake_keychain, failure, True)

    assert claude_keychain.store_token(_sample()) is False

    assert fake_keychain.entry is None


@pytest.mark.parametrize("platform", PLATFORMS)
def test_store_is_false_when_the_tool_is_missing_or_the_read_back_differs(fake_keychain, platform):
    fake_keychain.platform = platform
    fake_keychain.available = False
    assert claude_keychain.store_token(_sample()) is False

    fake_keychain.available = True
    fake_keychain.entry = _sample("o")
    fake_keychain.write_is_silent_noop = True
    assert claude_keychain.store_token(_sample()) is False


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("damage", ["", " ", "'", '"', "\\", "\n", "\r", ";", "$", "é"])
def test_store_refuses_a_value_outside_the_allow_list_without_starting_a_tool(fake_keychain, platform, damage):
    fake_keychain.platform = platform
    sample = _sample()
    values = [damage] if not damage else [sample + damage, damage + sample, sample[:12] + damage + sample[12:]]

    for value in values:
        assert claude_keychain.store_token(value) is False

    assert fake_keychain.calls == []
    assert fake_keychain.entry is None


def test_store_accepts_every_character_of_the_allow_list(fake_keychain):
    value = "sk-ant-" + "Az09._~+/=-" + make_probe()
    assert claude_keychain.is_storable_token(value) is True
    assert claude_keychain.store_token(value) is True
    assert fake_keychain.entry == value


@pytest.mark.parametrize("value", ["", " ", "two words", "quote'd", "semi;colon", "new\nline", "é"])
def test_the_storability_check_refuses_what_the_store_refuses_and_starts_no_tool(fake_keychain, value):
    assert claude_keychain.is_storable_token(value) is False
    assert claude_keychain.store_token(value) is False
    assert fake_keychain.calls == []


class _Recorder:
    """Stands in for `subprocess.run`: records the call and returns a result or raises."""

    def __init__(self, raises: BaseException | None = None):
        self.raises = raises
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args[0], 0, "out", "")


def test_the_seam_runs_a_lookup_without_a_shell_and_with_standard_input_closed(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "run", recorder)

    proc = REAL_RUN_KEYCHAIN_TOOL(LOOKUP_ARGV["linux"])

    assert proc is not None and proc.stdout == "out"
    ((args, kwargs),) = recorder.calls
    assert args == (LOOKUP_ARGV["linux"],)
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "check": False,
        "stdin": subprocess.DEVNULL,
        "timeout": claude_keychain.LOOKUP_TIMEOUT_SECONDS,
    }


def test_the_seam_passes_a_store_value_as_input_and_never_as_an_argument(monkeypatch):
    sample = _sample()
    recorder = _Recorder()
    monkeypatch.setattr(subprocess, "run", recorder)

    REAL_RUN_KEYCHAIN_TOOL(STORE_ARGV["linux"], sample)

    ((args, kwargs),) = recorder.calls
    assert args == (STORE_ARGV["linux"],)
    assert_no_leak(sample, *args)
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "check": False,
        "input": sample,
        "timeout": claude_keychain.STORE_TIMEOUT_SECONDS,
    }
    assert "shell" not in kwargs and "stdin" not in kwargs


def test_the_timeouts_are_bounded():
    assert 10 <= claude_keychain.LOOKUP_TIMEOUT_SECONDS <= 15
    assert 10 <= claude_keychain.STORE_TIMEOUT_SECONDS <= 15


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("security"),
        subprocess.TimeoutExpired(["security"], 10.0),
        PermissionError("denied"),
        OSError("refused"),
    ],
)
def test_the_seam_returns_none_and_prints_nothing_when_the_tool_cannot_run(monkeypatch, capsys, caplog, error):
    sample = _sample()
    monkeypatch.setattr(subprocess, "run", _Recorder(raises=error))

    assert REAL_RUN_KEYCHAIN_TOOL(STORE_ARGV["darwin"], sample) is None

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "" and caplog.text == ""


def test_the_suite_wide_fixture_replaces_the_seam(fake_keychain):
    assert claude_keychain.run_keychain_tool == fake_keychain.run
    assert claude_keychain.run_keychain_tool is not REAL_RUN_KEYCHAIN_TOOL
    with pytest.raises(AssertionError, match="unexpected keychain command"):
        claude_keychain.run_keychain_tool(["security", "delete-generic-password", "-s", "holon"])


def test_the_real_platform_switch_knows_three_answers(monkeypatch):
    for sys_platform, expected in (("darwin", "darwin"), ("linux", "linux"), ("win32", "unsupported")):
        with monkeypatch.context() as patched:
            patched.setattr(claude_keychain.sys, "platform", sys_platform)
            assert REAL_CURRENT_PLATFORM() == expected


@pytest.mark.parametrize("platform", [*PLATFORMS, "unsupported"])
def test_a_found_lookup_and_the_wording_helpers_show_no_value(fake_keychain, platform):
    sample = _sample()
    fake_keychain.platform = platform
    fake_keychain.entry = sample
    found = KeychainLookup(LookupStatus.FOUND, sample)

    assert_no_leak(
        sample,
        repr(found),
        str(found),
        claude_keychain.store_name(),
        claude_keychain.store_command(),
        claude_keychain.store_commands_text(),
    )
    assert "FOUND" in repr(found)


def test_the_operator_one_liners_prompt_for_the_value(fake_keychain):
    macos, linux = claude_keychain.STORE_COMMANDS["darwin"], claude_keychain.STORE_COMMANDS["linux"]
    assert macos == "security add-generic-password -U -s holon -a claude-oauth-token -w"
    assert linux == 'secret-tool store --label="Holon claude credential" service holon account claude-oauth-token'
    assert macos in claude_keychain.store_commands_text() and linux in claude_keychain.store_commands_text()

    fake_keychain.platform = "darwin"
    assert (claude_keychain.store_command(), claude_keychain.store_name()) == (macos, "the macOS keychain")
    fake_keychain.platform = "linux"
    assert (claude_keychain.store_command(), claude_keychain.store_name()) == (linux, "the Secret Service keyring")
    fake_keychain.platform = "unsupported"
    assert (claude_keychain.store_command(), claude_keychain.store_name()) == ("", "the OS secret store")


@pytest.mark.parametrize("role", ["executor", "planner"])
def test_the_agent_credentials_documents_show_the_store_one_liners_of_the_code(role):
    document = (REPO / "docs" / role / "agent_credentials_requirements.md").read_text(encoding="utf-8")
    lines = [line.strip() for line in document.splitlines()]
    for one_liner in claude_keychain.STORE_COMMANDS.values():
        assert one_liner in lines, f"docs/{role}/agent_credentials_requirements.md lacks a store one-liner"
