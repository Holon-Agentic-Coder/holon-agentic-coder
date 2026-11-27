from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.cli import setup_token_reduction_proxy, teardown_token_reduction_proxy
from sandbox_executor.launcher import proxy_readiness, sidecar_plan
from sandbox_executor.launcher.root_ca import generate_root_ca

from tests.fake_docker import fake_docker_runs
from tests.sidecar_fixtures import FakeDocker, clean_sidecar_state, isolate_host_paths
from tests.test_repository_hygiene import LOCAL_NOTES_DIR


@pytest.fixture(autouse=True)
def _clean_sidecar_state():
    yield from clean_sidecar_state()


def test_default_sidecar_paths_live_in_user_state_dir_and_touch_nothing_in_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WIRE_LOG_DIR", raising=False)
    monkeypatch.delenv("CACHE_DIR", raising=False)
    isolate_host_paths(tmp_path, monkeypatch)

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(worktree)], check=True)
    (worktree / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "config", "user.email", "t@holon.com"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=worktree, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=worktree, check=True)

    monkeypatch.chdir(worktree)
    before_entries = set(worktree.rglob("*"))

    ca_cert, ca_key = generate_root_ca(cert_dir=str(tmp_path / "certs"))
    proxy_cache_dir, _, _, host_wire_log_dir, host_cache_dir = sidecar_plan._proxy_host_dirs(ca_cert, ca_key)

    after_entries = set(worktree.rglob("*"))
    assert after_entries == before_entries
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""

    user_state = str(tmp_path / "home" / ".holon")
    assert proxy_cache_dir == os.path.join(user_state, "proxy-cache")
    assert host_wire_log_dir == os.path.join(user_state, "mitm_wire_logs")
    assert host_cache_dir == os.path.join(user_state, "cache")
    assert os.path.isdir(host_wire_log_dir)
    assert os.path.isdir(host_cache_dir)


def test_wire_log_dir_and_cache_dir_overrides_support_absolute_and_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_host_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(proxy_readiness, "wait_for_proxy", lambda *args, **kwargs: True)

    abs_wire = tmp_path / "abs_overrides" / "wire"
    abs_cache = tmp_path / "abs_overrides" / "cache"
    monkeypatch.setenv("WIRE_LOG_DIR", str(abs_wire))
    monkeypatch.setenv("CACHE_DIR", str(abs_cache))

    fake_abs = FakeDocker()
    fake_docker_runs(monkeypatch, fake_abs)
    setup_token_reduction_proxy(mitm_web=False)
    teardown_token_reduction_proxy()

    run_abs = next(call for call in fake_abs.calls if call[:2] == ["docker", "run"])
    assert f"{abs_wire}:/tmp/wire_logs" in run_abs
    assert f"{abs_cache}:/tmp/cache" in run_abs
    assert abs_wire.is_dir()
    assert abs_cache.is_dir()

    rel_base = tmp_path / "rel_base"
    rel_base.mkdir()
    monkeypatch.chdir(rel_base)
    monkeypatch.setenv("WIRE_LOG_DIR", "rel_wire/logs")
    monkeypatch.setenv("CACHE_DIR", "rel_cache/db")

    fake_rel = FakeDocker()
    fake_docker_runs(monkeypatch, fake_rel)
    setup_token_reduction_proxy(mitm_web=False)
    teardown_token_reduction_proxy()

    run_rel = next(call for call in fake_rel.calls if call[:2] == ["docker", "run"])
    assert f"{rel_base / 'rel_wire' / 'logs'}:/tmp/wire_logs" in run_rel
    assert f"{rel_base / 'rel_cache' / 'db'}:/tmp/cache" in run_rel
    assert (rel_base / "rel_wire" / "logs").is_dir()
    assert (rel_base / "rel_cache" / "db").is_dir()


def test_no_launcher_source_file_names_forbidden_local_notes_directory() -> None:
    launcher_dir = Path(__file__).resolve().parent.parent / "src" / "sandbox_executor" / "launcher"
    py_files = sorted(launcher_dir.rglob("*.py"))
    assert py_files
    for py_file in py_files:
        text = py_file.read_text(encoding="utf-8")
        assert LOCAL_NOTES_DIR not in text, f"{py_file} contains forbidden directory name"
