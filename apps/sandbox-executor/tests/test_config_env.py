"""The layered dotenv configuration: parsing, per-variable precedence, the allow-list, warnings and secret files.

Temporary files only: every layer lives under `tmp_path` at an explicit mode, and the credential is a fake assembled
at runtime (see `tests.leak_assertions`), so no token-shaped literal is committed.
"""

import os
import pathlib
import re
import subprocess

import pytest
from sandbox_executor import claude_credential, config_env, config_env_report, config_env_secrets
from sandbox_executor.config_env import project_env_path, user_env_path
from sandbox_executor.config_env_keys import HOST_SET_KEYS
from sandbox_executor.scaffold import init_project

from tests.leak_assertions import assert_no_fragment, make_probe

PACKAGE = pathlib.Path(__file__).resolve().parent.parent
SOURCE = PACKAGE / "src" / "sandbox_executor"
README = PACKAGE / "README.md"


def fake_token() -> str:
    """A fresh fake claude subscription token, shaped like one but never committed as a literal."""
    return "sk-ant-" + "oat01-" + make_probe("f")


def write(path: pathlib.Path, text: str, mode: int = 0o600) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


class Layers:
    """A temporary home with `~/.holon/.env` and a repository with `<repo>/.env`, wired into `config_env`."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.home = tmp_path / "home"
        self.repo = tmp_path / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.user = self.home / ".holon" / ".env"
        self.project = self.repo / ".env"

    def load(self, user: str | None = None, project: str | None = None, environ: dict[str, str] | None = None):
        if user is not None:
            write(self.user, user)
        if project is not None:
            write(self.project, project)
        return config_env.load_effective_config(environ={} if environ is None else environ, cwd=str(self.repo))


@pytest.fixture
def layers(tmp_path, monkeypatch) -> Layers:
    result = Layers(tmp_path)
    monkeypatch.setenv("HOME", str(result.home))
    monkeypatch.setattr(config_env, "user_env_path", user_env_path)
    monkeypatch.setattr(config_env, "project_env_path", project_env_path)
    return result


def setting(config, key):
    found = config.settings[key]
    return found.value, found.source, found.shadowed


# --- precedence -----------------------------------------------------------------------------------------------------


def test_key_only_in_the_user_layer_comes_from_user(layers):
    assert setting(layers.load(user="HOLON_X=1\n"), "HOLON_X") == ("1", "user", ())


def test_key_only_in_the_project_layer_comes_from_project(layers):
    assert setting(layers.load(project="HOLON_X=2\n"), "HOLON_X") == ("2", "project", ())


def test_key_in_both_file_layers_comes_from_project_and_shadows_user(layers):
    assert setting(layers.load(user="HOLON_X=1\n", project="HOLON_X=2\n"), "HOLON_X") == ("2", "project", ("user",))


def test_empty_project_value_falls_back_to_the_user_layer(layers):
    config = layers.load(user="HOLON_X=1\n", project="HOLON_X=\n")
    assert setting(config, "HOLON_X") == ("1", "user", ("project (empty)",))
    environ: dict[str, str] = {}
    assert config_env.apply_to_environ(config, environ) == ["HOLON_X"]
    assert environ == {"HOLON_X": "1"}


def test_empty_project_value_falls_back_to_the_built_in_default(layers):
    config = layers.load(project="HOLON_PREFLIGHT_TIMEOUT=\n")
    value, source, shadowed = setting(config, "HOLON_PREFLIGHT_TIMEOUT")
    assert (source, shadowed) == ("default", ("project (empty)",)) and value


def test_key_empty_in_every_layer_is_unset_and_never_exported(layers):
    config = layers.load(user="HOLON_X=\n", project="HOLON_X=\n")
    assert setting(config, "HOLON_X") == (None, "project", ("user (empty)",))
    assert config_env.apply_to_environ(config, {}) == []


def test_empty_process_value_falls_back_to_a_file_layer(layers):
    config = layers.load(user="HOLON_X=1\n", environ={"HOLON_X": ""})
    assert setting(config, "HOLON_X") == ("1", "user", ("process (empty)",))
    environ = {"HOLON_X": ""}
    assert config_env.apply_to_environ(config, environ) == ["HOLON_X"]
    assert environ == {"HOLON_X": "1"}


def test_process_value_beats_both_file_layers(layers):
    config = layers.load(user="HOLON_X=1\n", project="HOLON_X=2\n", environ={"HOLON_X": "3"})
    assert setting(config, "HOLON_X") == ("3", "process", ("user", "project"))


def test_cli_override_beats_the_process_value(layers):
    config = layers.load(user="HOLON_AGENT=pi\n", environ={"HOLON_AGENT": "codex"})
    config = config_env.with_cli_overrides(config, {"HOLON_AGENT": "claude"})
    assert setting(config, "HOLON_AGENT") == ("claude", "cli", ("user", "process"))
    direct = config_env.resolve({"HOLON_X": "1"}, {}, {"HOLON_X": "2"}, cli_overrides={"HOLON_X": "3"})
    assert setting(direct, "HOLON_X") == ("3", "cli", ("user", "process"))


def test_cli_override_labels_an_all_empty_previous_source_as_empty(layers):
    config = layers.load(user="HOLON_X=\n", project="HOLON_X=\n")
    config = config_env.with_cli_overrides(config, {"HOLON_X": "3"})
    assert setting(config, "HOLON_X") == ("3", "cli", ("user (empty)", "project (empty)"))


def test_each_variable_resolves_independently(layers):
    config = layers.load(user="HOLON_A=user-a\nHOLON_B=user-b\n", project="HOLON_A=project-a\n")
    assert setting(config, "HOLON_A") == ("project-a", "project", ("user",))
    assert setting(config, "HOLON_B") == ("user-b", "user", ())


def test_built_in_default_is_shadowed_but_never_exported(layers):
    config = layers.load()
    assert config.settings["HOLON_PREFLIGHT_TIMEOUT"].source == "default"
    assert config_env.apply_to_environ(config, {}) == []
    shadowed = layers.load(user="HOLON_PREFLIGHT_TIMEOUT=5\n")
    assert setting(shadowed, "HOLON_PREFLIGHT_TIMEOUT") == ("5", "user", ("default",))


def test_apply_fills_only_absent_keys(layers):
    config = layers.load(user="HOLON_A=1\nHOLON_B=2\n")
    environ = {"HOLON_B": "process", "PATH": "/bin"}
    assert config_env.apply_to_environ(config, environ) == ["HOLON_A"]
    assert environ == {"HOLON_A": "1", "HOLON_B": "process", "PATH": "/bin"}


def test_missing_files_are_empty_layers(layers):
    assert layers.load().warnings == []


def test_project_path_follows_a_git_file_and_is_none_outside_a_repository(tmp_path):
    worktree = tmp_path / "wt"
    write(worktree / ".git", "gitdir: elsewhere\n")
    (worktree / "pkg" / "sub").mkdir(parents=True)
    assert project_env_path(worktree / "pkg" / "sub") == worktree / ".env"
    (tmp_path / "plain").mkdir()
    if not any((parent / ".git").exists() for parent in (tmp_path, *tmp_path.parents)):
        assert project_env_path(tmp_path / "plain") is None


# --- parsing --------------------------------------------------------------------------------------------------------


def parse(text: str):
    return config_env.parse_env_text(text, "f.env")


def test_comments_blank_lines_and_quotes():
    parsed = parse(
        "# comment\n   # indented comment\n\n"
        "HOLON_A='single # kept'\nHOLON_B=\"double\"\nHOLON_D=plain value  # trailing\nHOLON_E=a#b\n"
    )
    assert parsed.values == {
        "HOLON_A": "single # kept",
        "HOLON_B": "double",
        "HOLON_D": "plain value",
        "HOLON_E": "a#b",
    }
    assert parsed.warnings == []


def test_export_prefix_is_rejected_by_line_number():
    probe = make_probe("e")
    parsed = parse(f"HOLON_A=1\nexport HOLON_C={probe}\n")
    assert parsed.values == {"HOLON_A": "1"}
    assert parsed.warnings == ["Warning: f.env:2: line ignored (an export prefix is not supported)."]


def test_nothing_is_expanded_or_executed():
    parsed = parse('HOLON_A="$HOME"\nHOLON_B=${X}\nHOLON_C=`touch /tmp/pwned`\nHOLON_D=$(id)\n')
    assert parsed.values == {"HOLON_A": "$HOME", "HOLON_B": "${X}", "HOLON_C": "`touch /tmp/pwned`", "HOLON_D": "$(id)"}


def test_crlf_line_endings_and_a_bom():
    assert parse("﻿HOLON_A=1\r\nHOLON_B='2'\r\n").values == {"HOLON_A": "1", "HOLON_B": "2"}


def test_malformed_lines_warn_with_the_line_number_and_never_the_value():
    probe = make_probe("m")
    parsed = parse(f'HOLON_A="{probe}\nnot an assignment {probe}\n1BAD={probe}\nHOLON_B="{probe}" tail\n')
    assert parsed.values == {}
    assert [w.split(": line")[0] for w in parsed.warnings] == [f"Warning: f.env:{n}" for n in (1, 2, 3, 4)]
    assert "unmatched quote" in parsed.warnings[0]
    for warning in parsed.warnings:
        assert_no_fragment(warning, probe)


def test_duplicate_key_keeps_the_last_value_and_warns_with_the_key():
    parsed = parse("HOLON_A=first\nHOLON_A=second\n")
    assert parsed.values == {"HOLON_A": "second"}
    assert parsed.warnings == ["Warning: f.env: duplicate key HOLON_A; the last value wins."]


def test_loader_source_never_expands_or_executes():
    for name in ("config_env.py", "config_env_parse.py", "config_env_secrets.py"):
        text = (SOURCE / name).read_text(encoding="utf-8")
        for forbidden in ("expandvars", "shell=True", "eval(", "exec(", "shlex"):
            assert forbidden not in text, (name, forbidden)


# --- allow-list and rejection ---------------------------------------------------------------------------------------


def test_non_holon_and_reserved_keys_are_rejected_by_name_only(layers):
    probe = make_probe("r")
    text = "".join(
        f"{key}={probe}\n"
        for key in ("PATH", "AWS_SECRET", "holon_x", "HOLON_ROLE", "HOLON_CONFIG_RESOLVED", "HOLON_REPO_URL")
    )
    config = layers.load(user=text + "GITHUB_TOKEN=gh-fake\nHOLON_OK=1\n")
    assert set(config.settings) - set(config_env.DEFAULTS) == {"GITHUB_TOKEN", "HOLON_OK"}
    rejected = [w for w in config.warnings if ": ignoring " in w]
    assert [w.split(": ignoring ")[1].split(":")[0] for w in rejected] == [
        "PATH",
        "AWS_SECRET",
        "holon_x",
        "HOLON_ROLE",
        "HOLON_CONFIG_RESOLVED",
        "HOLON_REPO_URL",
    ]
    for warning in config.warnings:
        assert_no_fragment(warning, probe)


def test_reserved_process_keys_are_not_reported(layers):
    config = layers.load(environ={"HOLON_ROLE": "planner", "HOLON_X": "1", "HOME": "/h"})
    assert "HOLON_ROLE" not in config.settings and "HOME" not in config.settings
    assert setting(config, "HOLON_X") == ("1", "process", ())


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("HOLON_AGENT_OAUTH_TOKEN", True),
        ("HOLON_AGENT_KEY", True),
        ("GITHUB_TOKEN", True),
        ("HOLON_AGENT_SETTINGS", True),
        ("HOLON_MITM_WEB_PASSWORD", True),
        ("HOLON_OTHER_SECRET", True),
        ("HOLON_AGENT_OAUTH_TOKEN_FILE", False),
        ("HOLON_MODEL", False),
    ],
)
def test_secret_classification(key, secret):
    assert config_env.is_secret_key(key) is secret


# --- permissions and git --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("layer", ["user", "project"])
def test_loose_layer_file_warns_with_path_and_mode(layers, layer):
    path = getattr(layers, layer)
    write(path, "HOLON_A=1\n", mode=0o644)
    config = layers.load()
    assert config.warnings == [f"Warning: {path} has mode 0644; expected 0600. The file is left unchanged."]


def test_owner_only_files_do_not_warn(layers, tmp_path):
    token_file = write(tmp_path / "token", fake_token())
    assert layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={token_file}\n", project="HOLON_A=1\n").warnings == []


def test_loose_token_file_warns_once_without_the_value(layers, tmp_path):
    token = fake_token()
    token_file = write(tmp_path / "token", token, mode=0o640)
    config = layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={token_file}\n")
    assert config.warnings == [f"Warning: {token_file} has mode 0640; expected 0600. The file is left unchanged."]
    assert_no_fragment("\n".join(config.warnings), token)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_tracked_secret_bearing_project_file_warns(layers):
    (layers.repo / ".git").rmdir()
    _git(layers.repo, "init", "-q")
    write(layers.project, "HOLON_AGENT_KEY=fake-agent-key\n")
    assert not any("tracked by git" in w for w in layers.load().warnings)
    _git(layers.repo, "add", ".env")
    warnings = layers.load().warnings
    assert warnings[0] == (
        f"Warning: {layers.project} is tracked by git and holds secret keys (HOLON_AGENT_KEY); untrack it."
    )


def test_tracked_project_file_without_secret_keys_does_not_warn(layers):
    (layers.repo / ".git").rmdir()
    _git(layers.repo, "init", "-q")
    write(layers.project, "HOLON_MODEL=m\n")
    _git(layers.repo, "add", ".env")
    assert layers.load().warnings == []


def test_project_token_file_reference_is_ignored_with_a_warning(layers, tmp_path):
    (layers.repo / ".git").rmdir()
    _git(layers.repo, "init", "-q")
    token = fake_token()
    token_file = write(tmp_path / "token", token)
    write(layers.project, f"HOLON_AGENT_OAUTH_TOKEN_FILE={token_file}\n")
    _git(layers.repo, "add", ".env")
    config = layers.load()
    assert "HOLON_AGENT_OAUTH_TOKEN" not in config.settings
    assert "HOLON_AGENT_OAUTH_TOKEN_FILE" not in config.settings
    assert len(config.warnings) == 1
    assert "ignoring HOLON_AGENT_OAUTH_TOKEN_FILE" in config.warnings[0] and "tracked" not in config.warnings[0]
    assert_no_fragment(config.warnings[0], token)


@pytest.mark.parametrize("key", config_env_secrets.PROJECT_REFUSED_KEYS)
def test_project_host_reach_key_is_ignored_with_a_warning(layers, key):
    probe = make_probe("h")
    config = layers.load(user=f"{key}=user-value\n", project=f"{key}={probe}\n")
    assert setting(config, key) == ("user-value", "user", ())
    assert config.warnings == [
        f"Warning: {layers.project}: ignoring {key}: it may only be set in ~/.holon/.env or the process environment."
    ]
    assert_no_fragment(config.warnings[0], probe)


@pytest.mark.parametrize("key", HOST_SET_KEYS)
def test_every_host_set_key_is_reserved(layers, key):
    config = layers.load(project=f"{key}=1\n")
    assert key not in config.settings
    assert any(f"ignoring {key}: the host owns this key" in w for w in config.warnings)


def test_project_token_file_reference_does_not_displace_the_user_reference(layers, tmp_path):
    user_token = fake_token()
    user_file = write(tmp_path / "user-token", user_token)
    project_file = write(tmp_path / "project-token", fake_token())
    config = layers.load(
        user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={user_file}\n", project=f"HOLON_AGENT_OAUTH_TOKEN_FILE={project_file}\n"
    )
    assert setting(config, "HOLON_AGENT_OAUTH_TOKEN") == (user_token, "user", ())
    assert setting(config, "HOLON_AGENT_OAUTH_TOKEN_FILE") == (str(user_file), "user", ())


# --- secret files and credential pinning ----------------------------------------------------------------------------


def test_secret_file_mapping_names_match_the_credential_constants():
    from sandbox_executor.config_env_secrets import SECRET_FILE_MAPPINGS

    assert SECRET_FILE_MAPPINGS == (
        ("HOLON_AGENT_OAUTH_TOKEN_FILE", claude_credential.HOLON_OAUTH_TOKEN_ENV, claude_credential.OAUTH_TOKEN_ENV),
    )


def test_token_file_yields_a_secret_user_value(layers, tmp_path):
    token = fake_token()
    token_file = write(tmp_path / "token", f"{token}\n")
    config = layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={token_file}\n")
    found = config.settings["HOLON_AGENT_OAUTH_TOKEN"]
    assert (found.value, found.source, found.masked) == (token, "user", True)
    assert config.settings["HOLON_AGENT_OAUTH_TOKEN_FILE"].masked is False


def test_process_token_file_beats_the_user_token_file_and_is_applied(layers, tmp_path):
    user_token, process_token = fake_token(), fake_token()
    user_file = write(tmp_path / "user-token", user_token)
    process_file = write(tmp_path / "process-token", process_token)
    environ = {"HOLON_AGENT_OAUTH_TOKEN_FILE": str(process_file)}
    config = layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={user_file}\n", environ=environ)
    assert setting(config, "HOLON_AGENT_OAUTH_TOKEN") == (process_token, "process", ("user",))
    assert setting(config, "HOLON_AGENT_OAUTH_TOKEN_FILE")[1] == "process"
    assert config_env.apply_to_environ(config, environ) == ["HOLON_AGENT_OAUTH_TOKEN"]
    assert environ["HOLON_AGENT_OAUTH_TOKEN"] == process_token


def test_token_file_path_expands_the_home_only(layers):
    token = fake_token()
    write(layers.home / ".holon" / "secrets" / "claude-oauth-token", token)
    config = layers.load(user="HOLON_AGENT_OAUTH_TOKEN_FILE=~/.holon/secrets/claude-oauth-token\n")
    assert config.settings["HOLON_AGENT_OAUTH_TOKEN"].value == token


def test_missing_or_multi_token_file_warns_by_path(layers, tmp_path):
    multi = write(tmp_path / "multi", f"{fake_token()} extra\n")
    config = layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={tmp_path / 'absent'}\n")
    assert "HOLON_AGENT_OAUTH_TOKEN" not in config.settings
    assert "missing, unreadable or empty" in config.warnings[0]
    config = layers.load(user=f"HOLON_AGENT_OAUTH_TOKEN_FILE={multi}\n")
    assert "HOLON_AGENT_OAUTH_TOKEN" not in config.settings
    assert "single credential token" in config.warnings[0]


def test_pinning_forwards_exactly_the_layered_token(monkeypatch):
    token = fake_token()
    monkeypatch.setenv("HOLON_AGENT_OAUTH_TOKEN", token)
    env = {"HOLON_AGENT_OAUTH_TOKEN": token, "HOLON_AGENT_KEY": "stale", "CLAUDE_CODE_OAUTH_TOKEN": "stale"}
    claude_credential.pin_claude_credential(env, "claude-agent")
    assert env == {"HOLON_AGENT_OAUTH_TOKEN": token}


def test_explicit_claude_variable_wins_and_drops_the_layered_token(monkeypatch):
    explicit = fake_token()
    monkeypatch.setenv("HOLON_AGENT_OAUTH_TOKEN", fake_token())
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", explicit)
    env = {"HOLON_AGENT_OAUTH_TOKEN": "layered"}
    claude_credential.pin_claude_credential(env, "claude")
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": explicit}


def test_other_runners_never_receive_the_layered_token(monkeypatch):
    env = {"HOLON_AGENT_OAUTH_TOKEN": fake_token(), "HOLON_AGENT_KEY": "pi-key"}
    claude_credential.pin_claude_credential(env, "pi")
    assert env == {"HOLON_AGENT_KEY": "pi-key"}


def test_layered_token_without_the_oauth_prefix_warns_by_name(monkeypatch, capsys):
    probe = make_probe("k")
    monkeypatch.setattr(claude_credential, "_WARNED_FORMAT", set())
    monkeypatch.setenv("HOLON_AGENT_OAUTH_TOKEN", probe)
    assert claude_credential.resolve_claude_credential() == ("CLAUDE_CODE_OAUTH_TOKEN", probe)
    err = capsys.readouterr().err
    assert "HOLON_AGENT_OAUTH_TOKEN is not a subscription OAuth token" in err
    assert_no_fragment(err, probe)


def test_layered_token_ranks_between_the_explicit_variables_and_the_secrets_file(monkeypatch):
    file_token = fake_token()
    write(pathlib.Path(claude_credential.secrets_file_path()), file_token)
    assert claude_credential.resolve_claude_credential_source()[0] == "file"
    layered = fake_token()
    monkeypatch.setenv("HOLON_AGENT_OAUTH_TOKEN", layered)
    assert claude_credential.resolve_claude_credential_source() == (
        "HOLON_AGENT_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        layered,
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "api03-" + make_probe("a"))
    assert claude_credential.resolve_claude_credential_source()[0] == "ANTHROPIC_API_KEY"


# --- nested invocations ---------------------------------------------------------------------------------------------


def test_nested_invocation_reads_no_layer_file(layers):
    write(layers.project, "HOLON_X=from-file\n", mode=0o644)
    config = layers.load(environ={"HOLON_CONFIG_RESOLVED": "1", "HOLON_X": "inherited"})
    assert config.inherited and config.warnings == []
    assert setting(config, "HOLON_X") == ("inherited", "process", ())
    assert "HOLON_CONFIG_RESOLVED" not in config.settings
    assert any(line.endswith("[process (inherited)]") for line in config_env_report.format_config_report(config))


# --- init and documentation -----------------------------------------------------------------------------------------


def test_init_scaffolds_a_commented_example_and_ignores_env_once(tmp_path):
    target = tmp_path / "project"
    assert init_project(str(target)) == 0
    assert init_project(str(target)) == 0
    example = (target / ".env.example").read_text(encoding="utf-8").splitlines()
    assert example and all(line.startswith("#") for line in example)
    assert "# HOLON_AGENT_OAUTH_TOKEN_FILE=~/.holon/secrets/claude-oauth-token" in example
    ignored = [line.strip() for line in (target / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ignored.count(".env") == 1 and ".env.example" not in ignored
    assert not (target / ".env").exists()


def test_readme_documents_every_key():
    readme = README.read_text(encoding="utf-8")
    for key, _default, _purpose in config_env.DOCUMENTED_KEYS:
        assert re.search(rf"^\| `{key}` +\|", readme, re.MULTILINE), key
    for key in HOST_SET_KEYS:
        assert f"`{key}`" in readme, key
    assert "`HOLON_AGENT_OAUTH_TOKEN_FILE`" in readme and "`CLAUDE_CODE_OAUTH_TOKEN`" in readme


@pytest.mark.parametrize(
    ("key", "value", "display"),
    [
        ("HOLON_TURN_TIMEOUT_SECONDS", "120", "120"),
        ("HOLON_STAGE_BUDGET_PLAN_MINUTES", "unlimited", "unlimited"),
        ("HOLON_TURN_TIMEOUT_SECONDS", "inf", "set (user; value withheld)"),
        ("HOLON_LOCAL_CONTEXT_WINDOW", "sk-" + "F" * 32, "set (user; value withheld)"),
        ("HOLON_MODEL", "claude-opus-5-5", "claude-opus-5-5"),
        ("HOLON_REASONING_MODE", "nan", "set (user; value withheld)"),
        ("HOLON_LOCAL_BASE_URL", "http://localhost:8081/v1", "set (user; value withheld)"),
        ("HOLON_REASONING_MODE", "alice:x@localhost", "set (user; value withheld)"),
        ("HOLON_REASONING_MODE", "run-0123456789abcdefghijk", "set (user; value withheld)"),
    ],
)
def test_report_shows_only_plain_values(key, value, display):
    config = config_env.resolve({key: value}, {}, {}, defaults={})
    (row,) = config_env_report.setting_rows(config)
    assert row["display"] == display
    assert row["value"] == (value if display == value else None)
