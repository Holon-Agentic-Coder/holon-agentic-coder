import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.hermetic_fixtures import (
    CHILD_SUITE_SENTINEL,
    KNOWLEDGE_INDEX_SUBDIR,
    REPO_ROOT,
    TESTS_DIR,
    UV,
    knowledge_index_snapshot,
    uv_available,
)
from tests.stress_support import (
    ChildContext,
    ChildRun,
    canary_violations,
    child_context,
    child_env,
    child_suite_command,
    git_head,
    run_child,
    seed_canary_workspace,
    survival_violations,
)

SENTINEL = "HOLON_STRESS_SUPPORT_CHILD"
INDEX_ROOT_VAR = "STRESS_SUPPORT_INDEX_ROOT"
_WORKSPACE = 'Path.home() / ".holon-sandbox" / "workspace"'
HERMETIC_BODY = "(tmp_path / 'scratch.txt').write_text('inside tmp_path')"
CANARY_HAZARDS = {
    "marker-overwritten": (f"({_WORKSPACE} / 'canary.txt').write_text('overwritten')", "Canary marker content"),
    "git-deleted": (f"shutil.rmtree({_WORKSPACE} / '.git')", "Dummy .git directory was deleted"),
    "index-written": (
        f"p = Path(os.environ['{INDEX_ROOT_VAR}']) / {KNOWLEDGE_INDEX_SUBDIR!r} / 'x.db'\n"
        "p.parent.mkdir(parents=True)\n"
        "p.write_bytes(b'db')",
        "holon-knowledge/.index/x.db",
    ),
}
SURVIVAL_HAZARDS = {
    "workspace-removed": (f"shutil.rmtree({_WORKSPACE})", "The executor workspace was deleted"),
    "git-removed": (f"shutil.rmtree({_WORKSPACE} / '.git')", "The workspace .git disappeared"),
    "head-removed": (f"({_WORKSPACE} / '.git' / 'HEAD').unlink()", "git can no longer resolve"),
    "head-moved": (
        f"subprocess.run(['git', 'commit', '-q', '--allow-empty', '-m', 'moved'], cwd={_WORKSPACE}, check=True)",
        "The workspace commit history changed",
    ),
}

SURVIVAL_CANARY_CONTENT = "canary-must-survive-suite-run"

# why: throwaway HOME has no git config and hosts without an FQDN cannot derive an email for git commit
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Holon Stress Control",
    "GIT_AUTHOR_EMAIL": "stress-control@holon.invalid",
    "GIT_COMMITTER_NAME": "Holon Stress Control",
    "GIT_COMMITTER_EMAIL": "stress-control@holon.invalid",
}


def run_generated_child(
    context: ChildContext, body: str, tmp_path: Path, extra_env: dict[str, str] | None = None
) -> ChildRun:
    if not uv_available():
        pytest.skip(f"{UV} is not available; the generated child runs only under '{UV} run pytest'.")
    child_dir = tmp_path / "generated-child"
    child_dir.mkdir()
    (child_dir / "pytest.ini").write_text("[pytest]\n")
    test_body = textwrap.indent(body, "    ")
    (child_dir / "test_generated_child.py").write_text(
        f"import os\nimport shutil\nimport subprocess\nfrom pathlib import Path\n\n\n"
        f"def test_generated_child(tmp_path):\n{test_body}\n"
    )
    env = child_env(context, SENTINEL) | (extra_env or {})
    assert env["HOME"].startswith(context.root + os.sep)
    cmd = [UV, "run", "--no-sync", "--project", REPO_ROOT, "pytest", "-p", "no:cacheprovider", "-q"]
    cmd += ["-c", str(child_dir / "pytest.ini"), "--rootdir", str(child_dir)]
    cmd += ["--basetemp", context.basetemp, str(child_dir / "test_generated_child.py")]
    run = run_child(cmd, label=f"generated-{context.label}", cwd=str(child_dir), env=env, timeout=120)
    assert run.completed.returncode == 0, f"Stdout:\n{run.completed.stdout}\nStderr:\n{run.completed.stderr}"
    return run


def _canary_check(body: str, tmp_path: Path) -> list[str]:
    index_root = tmp_path / "index-root"
    index_root.mkdir()
    with child_context("canary") as context:
        seed = seed_canary_workspace(context)
        index_before = knowledge_index_snapshot(str(index_root))
        run_generated_child(context, body, tmp_path, {INDEX_ROOT_VAR: str(index_root)})
        return canary_violations(context, seed, str(index_root), index_before)


def _git(*args: str, cwd: str) -> None:
    env = os.environ | GIT_IDENTITY
    subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=True)


def _survival_check(body: str, tmp_path: Path) -> list[str]:
    with child_context("survival") as context:
        workspace = context.workspace
        os.makedirs(workspace)
        _git("init", "-q", cwd=workspace)
        canary = os.path.join(workspace, "canary-executor-workspace.txt")
        Path(canary).write_text(SURVIVAL_CANARY_CONTENT)
        _git("add", "-A", cwd=workspace)
        _git("commit", "-q", "-m", "seed", cwd=workspace)
        head_sha = git_head(workspace).stdout.strip()
        run_generated_child(context, body, tmp_path, GIT_IDENTITY)
        return survival_violations(workspace, canary, SURVIVAL_CANARY_CONTENT, head_sha)


@pytest.mark.parametrize("hazard", sorted(CANARY_HAZARDS))
def test_canary_check_fails_when_a_child_test_writes_outside_its_sandbox(hazard: str, tmp_path: Path) -> None:
    body, expected = CANARY_HAZARDS[hazard]
    violations = _canary_check(body, tmp_path)
    assert any(expected in violation for violation in violations), violations


@pytest.mark.parametrize("hazard", sorted(SURVIVAL_HAZARDS))
def test_survival_check_fails_when_the_workspace_or_its_head_is_removed(hazard: str, tmp_path: Path) -> None:
    body, expected = SURVIVAL_HAZARDS[hazard]
    violations = _survival_check(body, tmp_path)
    assert any(expected in violation for violation in violations), violations


def test_canary_check_is_clean_for_a_hermetic_child(tmp_path: Path) -> None:
    assert _canary_check(HERMETIC_BODY, tmp_path) == []


def test_survival_check_is_clean_for_a_hermetic_child(tmp_path: Path) -> None:
    assert _survival_check(HERMETIC_BODY, tmp_path) == []


def _paths(context: ChildContext) -> list[str]:
    return [context.root, context.home, context.workspace, context.tmpdir, context.basetemp]


def _inside(path: str, parent: str) -> bool:
    return os.path.commonpath([path, parent]) == parent


def test_concurrent_contexts_share_no_workspace_home_or_temporary_directory() -> None:
    with child_context("survival") as survival, child_context("canary") as canary:
        for mine, other in ((survival, canary), (canary, survival)):
            assert all(_inside(path, mine.root) for path in _paths(mine))
            assert not any(_inside(path, other.root) for path in _paths(mine))
            assert not _inside(mine.tmpdir, mine.home)
            assert not _inside(mine.tmpdir, other.home)
            assert os.path.isdir(mine.home)
            assert os.path.isdir(mine.tmpdir)
        assert len({path for context in (survival, canary) for path in _paths(context)}) == 10
    assert not os.path.exists(survival.root)
    assert not os.path.exists(canary.root)


def test_child_env_pins_home_and_temporary_root_and_drops_outer_xdist_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "8")
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "outer-run")
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
    with child_context("survival") as survival, child_context("canary") as canary:
        envs = {context.label: (context, child_env(context, SENTINEL)) for context in (survival, canary)}
    for context, env in envs.values():
        assert env["HOME"] == context.home
        assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == context.tmpdir
        assert env[SENTINEL] == env[CHILD_SUITE_SENTINEL] == "1"
        assert "PYTEST_CURRENT_TEST" not in env
        assert sorted(key for key in env if key.startswith("PYTEST_XDIST_")) == ["PYTEST_XDIST_AUTO_NUM_WORKERS"]
        assert env["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "3"
    assert envs["survival"][1]["HOME"] != envs["canary"][1]["HOME"]
    assert envs["survival"][1]["TMPDIR"] != envs["canary"][1]["TMPDIR"]


def test_child_env_gives_each_concurrent_child_every_usable_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    with child_context("canary") as context:
        under_outer_worker = child_env(context, SENTINEL)
        monkeypatch.delenv("PYTEST_XDIST_WORKER")
        standalone = child_env(context, SENTINEL)
    assert under_outer_worker["PYTEST_XDIST_AUTO_NUM_WORKERS"] == str(os.process_cpu_count())
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in standalone


def test_child_suite_command_carries_the_context_basetemp() -> None:
    with child_context("canary") as context:
        cmd = child_suite_command(context)
    assert cmd[cmd.index("--basetemp") + 1] == context.basetemp
    assert cmd[:4] == [UV, "run", "--no-sync", "pytest"]


def test_child_suite_command_targets_replace_the_suite_directory() -> None:
    with child_context("canary") as context:
        whole = child_suite_command(context)
        narrowed = child_suite_command(context, targets=["a/test_x.py", "a/test_y.py"])
    assert TESTS_DIR in whole
    assert TESTS_DIR not in narrowed
    assert {"a/test_x.py", "a/test_y.py"} <= set(narrowed)
    assert narrowed[narrowed.index("-m") + 1] == whole[whole.index("-m") + 1]
