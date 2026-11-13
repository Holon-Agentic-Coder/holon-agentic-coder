"""Hermetic checks of the docker arguments that give pi a writable, user-owned home."""

from pathlib import Path

import pytest
from sandbox_executor.cli import get_agent_session_mounts
from sandbox_executor.preflight import build_probe_docker_cmd

OWNED = "uid=1000,gid=1000"
PI_DIR = "/home/holon/.pi"
AGENT_DIR = "/home/holon/.pi/agent"
MODELS_DEST = f"{AGENT_DIR}/models.json"


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    agent = tmp_path / ".pi" / "agent"
    (agent / "sessions").mkdir(parents=True)
    (agent / "models.json").write_text("{}")
    (agent / "auth.json").write_text("{}")
    (agent / "run-history.jsonl").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOLON_LOCAL_LLM", raising=False)
    return tmp_path


def _tmpfs_specs(mounts: list[str]) -> list[str]:
    return [mounts[i + 1] for i, arg in enumerate(mounts) if arg == "--tmpfs"]


def test_pi_gets_owned_tmpfs_parents_before_read_only_models_json(fake_home: Path) -> None:
    mounts = get_agent_session_mounts("pi")

    tmpfs = _tmpfs_specs(mounts)
    assert [spec.split(":")[0] for spec in tmpfs] == [PI_DIR, AGENT_DIR]
    assert all(OWNED in spec for spec in tmpfs)
    binds = [mounts[i + 1] for i, arg in enumerate(mounts) if arg == "-v"]
    assert binds == [f"{fake_home}/.pi/agent/models.json:{MODELS_DEST}:ro"]
    assert max(i for i, arg in enumerate(mounts) if arg == "--tmpfs") < mounts.index("-v")


def test_pi_mounts_do_not_leak_credentials_sessions_or_history(fake_home: Path) -> None:
    joined = " ".join(get_agent_session_mounts("pi"))

    for forbidden in ("auth.json", "sessions", "run-history"):
        assert forbidden not in joined
    assert f"{fake_home}/.pi/agent:" not in joined


def test_pi_without_host_models_json_still_gets_writable_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOLON_LOCAL_LLM", raising=False)

    mounts = get_agent_session_mounts("pi")

    assert "-v" not in mounts
    assert [spec.split(":")[0] for spec in _tmpfs_specs(mounts)] == [PI_DIR, AGENT_DIR]


def test_legacy_config_location_also_gets_owned_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / ".config" / "pi"
    legacy.mkdir(parents=True)
    (legacy / "models.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOLON_LOCAL_LLM", raising=False)

    mounts = get_agent_session_mounts("pi")

    assert "/home/holon/.config/pi:uid=1000,gid=1000,mode=0700" in _tmpfs_specs(mounts)
    assert "/home/holon/.config:uid=1000,gid=1000,mode=0700" in _tmpfs_specs(mounts)
    assert f"{legacy}/models.json:/home/holon/.config/pi/models.json:ro" in mounts


def test_host_local_mode_mounts_nothing(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_LOCAL_LLM", "1")

    assert get_agent_session_mounts("pi") == []


def test_probe_command_carries_parents_before_the_file_mount(fake_home: Path) -> None:
    mounts = get_agent_session_mounts("pi")

    cmd, _ = build_probe_docker_cmd("pi-agent", "model", "image", "name", mounts)

    start = cmd.index(mounts[0])
    assert cmd[start : start + len(mounts)] == mounts
    destinations = [
        spec.split(":")[0] if cmd[i] == "--tmpfs" else spec.split(":")[1]
        for i, spec in ((i, cmd[i + 1]) for i in range(len(cmd) - 1))
        if cmd[i] in ("--tmpfs", "-v")
    ]
    # Every parent of the sessions directory is an owned tmpfs, so no root-created parent remains.
    assert destinations == [PI_DIR, AGENT_DIR, MODELS_DEST]


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("claude", ["-v", "{home}/.config/claude:/home/holon/.config/claude:ro"]),
        (
            "codex",
            [
                "-v",
                "{home}/.codex:/home/holon/.codex:ro",
                "--tmpfs",
                "/home/holon/.codex/log:uid=1000,gid=1000",
                "--tmpfs",
                "/home/holon/.codex/cache:uid=1000,gid=1000",
            ],
        ),
        ("gemini", ["-v", "{home}/.config/gcloud:/home/holon/.config/gcloud:ro"]),
    ],
)
def test_other_runners_mounts_are_unchanged(
    agent: str, expected: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for sub in (".config/claude", ".codex", ".config/gcloud"):
        (tmp_path / sub).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_agent_session_mounts(agent) == [item.format(home=tmp_path) for item in expected]


def test_antigravity_mounts_are_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = tmp_path / ".holon" / "sessions" / "antigravity"
    session.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOLON_ANTIGRAVITY_SESSION_DIR", raising=False)

    assert get_agent_session_mounts("antigravity") == [
        "-v",
        f"{session}:/home/holon/.gemini/antigravity-cli:ro",
        "--tmpfs",
        "/home/holon/.gemini/config:uid=1000,gid=1000",
        "--tmpfs",
        "/home/holon/.gemini/antigravity-cli/log:uid=1000,gid=1000",
        "--tmpfs",
        "/home/holon/.gemini/antigravity-cli/cache:uid=1000,gid=1000",
    ]
