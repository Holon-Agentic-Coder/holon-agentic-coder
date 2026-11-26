"""``holon flow`` takes the intent's run lock before it writes anything: refusal, other slugs, takeover and release."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sandbox_executor.cli import main as cli_main
from sandbox_executor.flow import FlowContext, get_default_checkpoint_path, save_checkpoint
from sandbox_executor.flow.run_lock import acquire_flow_run_lock, run_lock_path

from tests.flow.conftest import child_python_env, wait_for_line
from tests.flow.helpers import _git

_RUNNER_ARGS = ["--agent", "antigravity-agent", "--model", "gemini-3.8-flash-medium", "--dry-run", "--json"]

#: Runs ``holon flow argv[2:]`` with a pipeline stub: ``ok`` returns, ``fail`` records a failed stage (exit 1), and
#: ``block`` prints ``ready`` and waits for a signal.
_FLOW_CHILD = """
import sys, time
from unittest.mock import patch
from sandbox_executor.cli import main
from sandbox_executor.flow import FlowStage, StageResult, StageStatus
MODE = sys.argv[1]

def pipeline(context, from_stage, checkpoint_path):
    if MODE == "fail":
        context.stage_results["intent"] = StageResult(FlowStage.INTENT, StageStatus.FAILED, error="boom")
    if MODE == "block":
        print("ready", flush=True)
        time.sleep(60)
    return {"status": "ok"}

with patch("sandbox_executor.cli.run_flow_pipeline", pipeline), patch("sys.argv", ["holon", "flow", *sys.argv[2:]]):
    main()
"""


@pytest.fixture
def flow_setup(flow_repos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """The host checkout on main, an intent file per slug and a fake ``docker`` on PATH that logs its argv."""
    host, origin = flow_repos
    for slug in ("alpha", "beta"):
        (tmp_path / f"{slug}.json").write_text(json.dumps({"slug": slug, "target_branch": "main"}), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\necho "$*" >> "$FAKE_DOCKER_LOG"\nexit 0\n', encoding="utf-8")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    return {"host": host, "origin": origin, "root": tmp_path}


def _argv(setup: dict[str, Path], slug: str, *extra: str) -> list[str]:
    return [str(setup["root"] / f"{slug}.json"), "--target-worktree", str(setup["host"]), *_RUNNER_ARGS, *extra]


def _flow(argv: list[str]) -> tuple[int | str | None, list[object]]:
    """Run ``holon flow`` in-process with the pipeline recorded; return the exit code and the pipeline calls."""
    calls: list[object] = []

    def pipeline(**kwargs: object) -> dict[str, str]:
        calls.append(kwargs)
        return {"status": "ok"}

    with (
        patch("sandbox_executor.cli.run_flow_pipeline", pipeline),
        patch("sys.argv", ["holon", "flow", *argv]),
        pytest.raises(SystemExit) as exited,
    ):
        cli_main()
    return exited.value.code, calls


def _repository_state(setup: dict[str, Path]) -> dict[str, object]:
    """Refs of the host and origin, the worktree list, every flow checkpoint and the host's ledger digests."""
    host, root = setup["host"], setup["root"]
    ledgers = sorted((host / "holon-knowledge" / "ledger").rglob("*.jsonl"))
    return {
        "host_refs": _git(host, "for-each-ref"),
        "origin_refs": _git(setup["origin"], "for-each-ref"),
        "worktrees": _git(host, "worktree", "list", "--porcelain"),
        "checkpoints": {str(p): p.read_bytes() for p in root.rglob(".holon/flow/checkpoint-*.json")},
        "ledgers": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in ledgers},
        "siblings": sorted(p.name for p in root.iterdir() if p.name.startswith("flow-")),
    }


def _lock_record(setup: dict[str, Path], slug: str) -> dict[str, object]:
    return json.loads(Path(str(run_lock_path(str(setup["host"]), slug))).read_text(encoding="utf-8"))


def _assert_refused(setup: dict[str, Path], argv: list[str], holder: subprocess.Popen[str], capsys) -> None:
    before = _repository_state(setup)

    code, calls = _flow(argv)

    assert code == 1
    assert calls == []
    err = capsys.readouterr().err
    assert f"pid {holder.pid} on " in err
    assert str(run_lock_path(str(setup["host"]), "alpha")) in err
    assert _repository_state(setup) == before


def test_a_second_fresh_run_of_the_slug_refuses_before_writing_anything(flow_setup, lock_holder, capsys) -> None:
    holder = lock_holder(flow_setup["host"], "alpha", mode="hold")

    _assert_refused(flow_setup, _argv(flow_setup, "alpha"), holder, capsys)

    assert not (flow_setup["root"] / "flow-alpha").exists()
    assert "flow-alpha" not in _git(flow_setup["host"], "branch", "--list")


def test_a_second_run_from_a_stage_refuses_too(flow_setup, lock_holder, capsys) -> None:
    holder = lock_holder(flow_setup["host"], "alpha", mode="hold")

    _assert_refused(flow_setup, _argv(flow_setup, "alpha", "--from-stage", "plan"), holder, capsys)


def test_a_second_run_from_the_holders_checkpoint_refuses_and_leaves_it_unchanged(
    flow_setup, lock_holder, capsys
) -> None:
    flow_worktree = flow_setup["root"] / "flow-alpha"
    _git(flow_setup["host"], "worktree", "add", "-q", "-b", "flow-alpha", str(flow_worktree), "main")
    intent = {"slug": "alpha", "target_branch": "main"}
    context = FlowContext(intent_data=intent, agent="antigravity-agent", target_worktree=str(flow_worktree))
    checkpoint = save_checkpoint(context, get_default_checkpoint_path(str(flow_worktree), "alpha"))
    holder = lock_holder(flow_worktree, "alpha", mode="hold")

    _assert_refused(flow_setup, ["--checkpoint", checkpoint, *_RUNNER_ARGS], holder, capsys)


def test_another_slug_runs_while_the_first_is_held(flow_setup, lock_holder) -> None:
    holder = lock_holder(flow_setup["host"], "alpha", mode="hold")

    code, calls = _flow(_argv(flow_setup, "beta"))

    assert code == 0
    assert len(calls) == 1
    assert _lock_record(flow_setup, "alpha")["pid"] == holder.pid
    assert _lock_record(flow_setup, "beta")["pid"] == os.getpid()


def test_the_lock_of_a_killed_holder_is_taken_over_and_its_containers_swept(flow_setup, lock_holder, caplog) -> None:
    holder = lock_holder(flow_setup["host"], "alpha")
    holder.kill()
    holder.wait(timeout=10)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        code, calls = _flow(_argv(flow_setup, "alpha"))

    assert code == 0
    assert len(calls) == 1
    assert f"from pid {holder.pid}" in caplog.text
    assert _lock_record(flow_setup, "alpha")["pid"] == os.getpid()
    docker_log = (flow_setup["root"] / "docker.log").read_text(encoding="utf-8")
    assert "ps -aq --filter label=holon.flow.run=holder0run" in docker_log


@pytest.mark.parametrize(
    ("mode", "halt", "codes"),
    [
        ("ok", None, {0}),
        ("fail", None, {1}),
        ("block", signal.SIGINT, {-signal.SIGINT, 130}),
        ("block", signal.SIGTERM, {143}),
    ],
    ids=["normal", "stage-failure", "sigint", "sigterm"],
)
def test_every_exit_releases_the_lock_for_the_next_run(flow_setup, mode, halt, codes, caplog) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", _FLOW_CHILD, mode, *_argv(flow_setup, "alpha")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=child_python_env(),
    )
    try:
        if halt is not None:
            assert wait_for_line(child, timeout=30) == "ready"
            child.send_signal(halt)
        assert child.wait(timeout=30) in codes
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)

    with caplog.at_level(logging.WARNING, logger="sandbox_executor.flow.run_lock"):
        held = acquire_flow_run_lock(str(flow_setup["host"]), "alpha", "next")
    assert held is not None and held.stale is None
    assert "Taking over" not in caplog.text
