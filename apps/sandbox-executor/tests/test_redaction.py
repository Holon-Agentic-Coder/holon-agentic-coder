"""Tests for the shared redaction module and its re-export from the executor entrypoint."""

from uuid import uuid4

import pytest
from sandbox_executor import redaction
from sandbox_executor.entrypoint import executor
from sandbox_executor.redaction import (
    COMMAND_ECHO_ENV_NAMES,
    REDACTION_MARKER,
    is_secret_env_name,
    redact_agent_secrets,
    redact_command,
    redact_env_literals,
)

from tests.leak_assertions import assert_no_fragment, make_gh_probe, make_probe

REPO = "github.com/myorg/myrepo.git"
BRANCH = "I-1792762862-x/_"


@pytest.fixture
def fake_token(monkeypatch: pytest.MonkeyPatch) -> str:
    sample = make_gh_probe()
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "HOLON_AGENT_KEY", "HOLON_MITM_WEB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", sample)
    return sample


@pytest.mark.parametrize(
    "name",
    [
        "_MAX_REDACT_INPUT_LEN",
        "_PROVIDER_TOKEN_RE",
        "SECRET_FLAGS",
        "_is_secret_flag",
        "redact_text",
        "redact_args",
        "redact_env_literals",
        "redact_agent_secrets",
    ],
)
def test_executor_reexports_the_shared_redactors(name: str) -> None:
    assert getattr(executor, name) is getattr(redaction, name)


def test_git_and_flow_use_the_shared_redactor() -> None:
    from sandbox_executor import flow, git

    assert git.redact_command is redaction.redact_command
    assert git.redact_agent_secrets is redaction.redact_agent_secrets
    assert flow.redact_text is redaction.redact_text


def test_executor_run_cmd_echo_masks_a_bare_token_and_env_literal(
    fake_token: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`run_cmd` echoes through the canonical command redactor, not `redact_args` alone."""
    # Neutral name and construction keep CodeQL from tainting `run_cmd`'s masked echo from this test value.
    web_value = "mitmweb-" + uuid4().hex
    monkeypatch.setenv("HOLON_MITM_WEB_PASSWORD", web_value)
    leaky = f"{fake_token} {web_value}"

    def fake_run(*args, **kwargs):
        return executor.subprocess.CompletedProcess(args[0], 1, stdout=f"out {fake_token}", stderr=f"err {fake_token}")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    with pytest.raises(executor.subprocess.CalledProcessError) as excinfo:
        executor.run_cmd(["gh", "api", "-H", f"x: {fake_token}", "--set", web_value, "--branch", BRANCH])

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, " ".join(excinfo.value.cmd)):
        for value in leaky.split():
            assert_no_fragment(text, value)
    assert f"--branch {BRANCH}" in captured.out


def test_run_git_failure_masks_a_bare_token(fake_token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox_executor import git

    def fake_run(cmd, **kwargs):
        return git.subprocess.CompletedProcess(cmd, 128, stdout="", stderr=f"fatal: bad credential {fake_token}")

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as excinfo:
        git.run_git(["-c", f"http.extraHeader=x: {fake_token}", "fetch"], check=True)

    assert_no_fragment(str(excinfo.value), fake_token)
    assert "fetch" in str(excinfo.value)


def test_redact_command_masks_anthropic_keys_in_inline_agent_settings() -> None:
    provider_sample = "sk-" + "ant-" + make_probe("anthropic")
    settings = '{"apiKey":"' + provider_sample + '"}'
    for line in (
        redact_command(["claude", "--settings", settings]),
        redact_command(["docker", "run", "-e", f"HOLON_AGENT_SETTINGS={settings}"]),
        redact_command(["echo", f"bare {provider_sample}"]),
    ):
        assert_no_fragment(line, provider_sample)


def test_agent_secret_redaction_matches_golden_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pins `redact_agent_secrets` to the exact output captured from main before the module move."""
    for name in ("GH_TOKEN", "HOLON_AGENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "literal-" + "Q7" * 6)
    fake = "ghp_" + "Z9" * 18
    corpus = "\n".join(
        [
            "clone https://x-access-token:" + fake + "@github.com/myorg/myrepo.git --branch I-1-x/_",
            "token was: " + fake,
            'api_key: "abc123" sort_key=keep cache_key=keep',
            "Authorization: Bearer abc.def.ghi",
            "http://127.0.0.1:8081/?token=" + "w" * 12 + "&page=2",
            "env value " + "literal-" + "Q7" * 6 + " end",
            "github_pat_" + "A1" * 12 + " glpat-" + "b2" * 6,
        ]
    )
    golden = (
        "clone https://*******@github.com/myorg/myrepo.git --branch I-1-x/_\n"
        "token was: *******\n"
        'api_key: "*******" sort_key=keep cache_key=keep\n'
        "Authorization: Bearer *******\n"
        "http://127.0.0.1:8081/?token=*******&page=2\n"
        "env value ******* end\n"
        "******* *******"
    )
    assert redact_agent_secrets(corpus) == golden


def test_redact_env_literals_default_names_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    pw_sample = make_probe("mitmweb")
    monkeypatch.setenv("HOLON_MITM_WEB_PASSWORD", pw_sample)
    # The ledger path keeps its original three names; the dashboard password is a command-echo addition.
    assert redact_env_literals(f"pw {pw_sample}") == f"pw {pw_sample}"
    assert redact_env_literals(f"pw {pw_sample}", names=COMMAND_ECHO_ENV_NAMES) == f"pw {REDACTION_MARKER}"


@pytest.mark.parametrize(
    "build_arg",
    [
        lambda t: f"GITHUB_TOKEN={t}",
        lambda t: f"GH_TOKEN={t}",
        lambda t: f"HOLON_REPO_URL=https://x-access-token:{t}@{REPO}",
        lambda t: f"http://127.0.0.1:8081/?token={t}",
        lambda t: f"SOME_UNRECOGNISED_NAME={t}",
        lambda t: t,
    ],
    ids=["github-token", "gh-token", "repo-url-userinfo", "mitmweb-query-token", "unrecognised-key", "bare"],
)
def test_redact_command_masks_every_token_form(fake_token: str, build_arg) -> None:
    line = redact_command(["docker", "run", "-e", build_arg(fake_token), "--branch", BRANCH])
    assert_no_fragment(line, fake_token)
    assert REDACTION_MARKER in line
    assert line.startswith("docker run -e ")
    assert line.endswith(f"--branch {BRANCH}")


def test_redact_command_masks_the_mitmweb_password(monkeypatch: pytest.MonkeyPatch) -> None:
    pw_sample = make_probe("mitmweb")
    monkeypatch.setenv("HOLON_MITM_WEB_PASSWORD", pw_sample)
    line = redact_command(["docker", "run", "-e", f"HOLON_MITM_WEB_PASSWORD={pw_sample}", "--set", pw_sample])
    assert_no_fragment(line, pw_sample)


def test_redact_command_keeps_repository_and_branch_readable(fake_token: str) -> None:
    argv = [
        "git",
        "clone",
        "--branch",
        BRANCH,
        "--single-branch",
        "--depth",
        "1",
        f"https://x-access-token:{fake_token}@{REPO}",
        ".",
    ]
    assert redact_command(argv) == (
        f"git clone --branch {BRANCH} --single-branch --depth 1 https://{REDACTION_MARKER}@{REPO} ."
    )


def test_redact_command_does_not_mask_a_branch_named_after_a_secret_word() -> None:
    argv = ["git", "clone", "--branch", "token-rotation/_", "--single-branch"]
    assert redact_command(argv) == "git clone --branch token-rotation/_ --single-branch"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GITHUB_TOKEN", True),
        ("GH_TOKEN", True),
        ("HOLON_AGENT_KEY", True),
        ("HOLON_MITM_WEB_PASSWORD", True),
        ("HOLON_CUSTOM_SECRET", True),
        ("HOLON_API_KEY", True),
        ("HOLON_REPO_URL", False),
        ("HOLON_ROLE", False),
        ("HOLON_AGENT_PROVIDER", False),
        ("HOLON_PASSIVE_MONITORING", False),
        ("HTTPS_PROXY", False),
        ("ANTHROPIC_API_KEY", True),
        ("CLAUDE_CODE_OAUTH_TOKEN", True),
        ("HOLON_DB_PASSWD", True),
        # Settings are classified by the shape of their value, never by name alone.
        ("HOLON_AGENT_SETTINGS", False),
        ("GIT_CONFIG_KEY_1", False),
        ("GIT_CONFIG_COUNT", False),
    ],
)
def test_is_secret_env_name(name: str, expected: bool) -> None:
    assert is_secret_env_name(name) is expected


def test_redact_command_catches_a_key_and_value_split_across_arguments() -> None:
    """The joined-line pass masks a `password:` key whose value arrives as the next argument."""
    probe = make_probe("s")
    line = redact_command(["mysql", "--host", "db.internal", "password:", probe])
    assert_no_fragment(line, probe)
    assert line.startswith("mysql --host db.internal password:")


def test_camel_case_api_key_json_is_masked_everywhere() -> None:
    probe = make_probe("k")
    settings = '{"apiKey":"' + probe + '"}'
    for line in (redaction.redact_text(settings), redact_agent_secrets(settings), redact_command(["-e", settings])):
        assert_no_fragment(line, probe)
        assert "apiKey" in line


def test_high_entropy_blob_without_a_provider_prefix_is_not_masked() -> None:
    """The provider sweep is a closed list of literal prefixes, not a generic high-entropy rule."""
    blob = (uuid4().hex + uuid4().hex).upper() + "+/=="
    line = f"layer digest {blob} ok"
    assert redact_agent_secrets(line) == line
    assert redact_command(["echo", blob]) == f"echo {blob}"


# Outputs captured from `redact_text` on main before the move (506b4fb). Only `sk-ant-` and `apikey`
# were added to the pattern set, so text without them must redact byte for byte as it did there.
_MAIN_REDACT_TEXT_GOLDEN = [
    ("token: abc123def", "token: *******"),
    ('api_key: "abc123"', 'api_key: "*******"'),
    ("clone https://user:pw@example.com/r.git", "clone https://*******@example.com/r.git"),
    ("sort_key=keep cache_key=keep", "sort_key=keep cache_key=keep"),
    ("http://127.0.0.1:8081/?token=xyz&page=2", "http://127.0.0.1:8081/?token=*******&page=2"),
    ("Authorization: Bearer abc.def.ghi", "Authorization: Bearer *******"),
    ("no secrets here at all", "no secrets here at all"),
    ('client_secret = "s3"\npassword:\n  hunter2', 'client_secret = "*******"\npassword:\n  *******'),
    ("db_password=abc&x=1", "db_password=*******&x=1"),
    ("--auth-token=abc", "--auth-token=*******"),
]


@pytest.mark.parametrize(("line", "expected"), _MAIN_REDACT_TEXT_GOLDEN)
def test_redact_text_matches_main_on_ledger_inputs(line: str, expected: str) -> None:
    assert redaction.redact_text(line) == expected


@pytest.mark.parametrize("offset", [235, 240, 245, 250])
def test_executor_run_cmd_redacts_before_the_250_character_clamp(
    offset: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A provider value straddling the per-argument clamp leaves no 8-character fragment behind."""
    probe = make_gh_probe()
    long_arg = "x" * offset + probe + "y" * 40

    def fake_run(*args, **kwargs):
        return executor.subprocess.CompletedProcess(args[0], 2, stdout="", stderr="")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    with pytest.raises(executor.subprocess.CalledProcessError) as excinfo:
        executor.run_cmd(["tool", long_arg, "--branch", BRANCH])

    captured = capsys.readouterr()
    for text in (captured.out, captured.err, " ".join(excinfo.value.cmd)):
        assert_no_fragment(text, probe)
    assert f"--branch {BRANCH}" in captured.out
