import io
import json
import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from sandbox_executor.entrypoint import executor
from sandbox_executor.ledger import read_merged_rows
from sandbox_executor.turns import TurnResult

# why: autouse fixtures apply only where their name is imported, so importers opt in by importing them by name
# why: pytest collects a TestCase subclass once per importing module, so shared harnesses are plain mixins


def _turn_via_run_cmd(cmd, **kwargs):
    # why: tests that fake run_cmd keep faking the agent; the real supervisor is covered in test_turn_supervisor.py
    res = executor.run_cmd(cmd, cwd=kwargs.get("cwd"), check=False)
    stdout = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
    stderr = res.stderr if isinstance(getattr(res, "stderr", None), str) else ""
    returncode = res.returncode if isinstance(getattr(res, "returncode", None), int) else 0
    return TurnResult(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _agent_turns_use_run_cmd():
    with patch("sandbox_executor.entrypoint.executor.run_turn", side_effect=_turn_via_run_cmd) as fake:
        yield fake


@pytest.fixture(autouse=True)
def _workspace_is_mounted():
    with patch("sandbox_executor.entrypoint.executor.workspace_mounted", return_value=True):
        yield


def _validate_stub(repo, repo_dir, plan_branch):
    return "tip-sha"


_REMOTE_GIT_VERBS = {"clone", "fetch", "pull", "push", "ls-remote", "remote"}


def _remote_git_cmds(cmds) -> list:
    return [cmd for cmd in cmds if cmd[:1] == ["git"] and _REMOTE_GIT_VERBS & set(cmd[1:3])]


def _cuts_branch(args) -> bool:
    return list(args[:3]) == ["git", "checkout", "-b"]


# why: GitHub push protection refuses any contiguous vendor-format secret, so fakes splice a filler after the prefix
_FAKE_FILLER = "NOTREAL_not-a-live-key_"


def _fake_token(prefix: str, body: str = "9f3Aq7ZxR2tKp8Wm") -> str:
    return prefix + _FAKE_FILLER + body


def _unscoped_add_all(cmds) -> bool:
    return any(c[:3] == ["git", "add", "-A"] and "--" not in c for c in cmds)


def _salvage_add(cmds) -> bool:
    return any(c[:3] == ["git", "add", "-A"] and ":(exclude)holon-knowledge/ledger/executions.jsonl" in c for c in cmds)


def _superseded_bound_then_redact(stream: str, budget: int) -> str:
    # why: the pipeline as shipped at 38fe087, kept so F-IT8-1 counter-examples still leak against it
    from sandbox_executor.entrypoint.executor import (
        _TRUNCATION_MARKER_RESERVE,
        redact_agent_secrets,
        redact_env_literals,
    )

    tail = redact_env_literals(stream).encode("utf-8")[-(budget - _TRUNCATION_MARKER_RESERVE) :]
    newline_at = tail.find(b"\n")
    tail = tail[newline_at + 1 :] if 0 <= newline_at < len(tail) - 1 else b""
    text = redact_agent_secrets(tail.decode("utf-8", errors="replace"))
    notice = f"[Agent output truncated: 0 bytes dropped; showing tail {len(text.encode('utf-8'))} bytes]\n"
    return notice + text


def _exact_filler(total_bytes: int, tag: str) -> str:
    lines, rem = divmod(total_bytes, 13)
    block = "".join(f"{tag}{i:08d}\n" for i in range(lines))
    if rem:
        block += "y" * (rem - 1) + "\n"
    assert len(block) == total_bytes, (len(block), total_bytes)
    return block


def _boundary_cut_offsets(cred: str, secret: str) -> list[int]:
    offsets = {0, 1, len(cred) - 1}
    for idx, ch in enumerate(cred):
        if ch in (":", '"'):
            offsets.add(idx)
        if ch == "\n":
            if idx > 0:
                offsets.add(idx - 1)
            offsets.add(idx)
            if idx + 1 < len(cred):
                offsets.add(idx + 1)
            cur = idx + 1
            while cur < len(cred) and cred[cur].isspace():
                offsets.add(cur)
                cur += 1
            if cur < len(cred):
                offsets.add(cur)
    secret_start = cred.find(secret)
    if secret_start != -1:
        offsets.add(secret_start)
        offsets.add(secret_start + len(secret) // 2)
        offsets.add(secret_start + len(secret))
    return sorted(o for o in offsets if 0 <= o < len(cred))


def _assert_supersedes(testcase, rev1, rev2):
    # why: docs/ledger_schema.md reader contract requires a superseding row to repeat every authoritative field
    for key in ("execution_notes", "execution_notes_valid", "elapsed_seconds", "budget_seconds"):
        testcase.assertIn(key, rev1)
    changed = {"status", "summary", "created_at", "ledger_revision"}
    testcase.assertEqual(
        {k: v for k, v in rev1.items() if k not in changed}, {k: v for k, v in rev2.items() if k not in changed}
    )


VALID_NOTES = """# Execution Record: E-test

## Summary

Implemented the plan. {filler}

## Actual Metrics

| metric                | value |
| --------------------- | ----- |
| success_actual        | 1     |
| entropy_actual        | 1.0   |
| impact_actual         | 8.0   |
| cost_actual           | 2.0   |
| learning_value_actual | 2.0   |

## Steps Executed

- Step 1 completed.

## Tests Run

- `uv run pytest` passed.
""".format(filler="Details of the change. " * 10)


def _run_executor_main(  # noqa: PLR0913
    tmp_path,
    plan_row: dict,
    plan_md: str | None,
    intent_row: dict,
    commands: list | None = None,
    plan_branch: str = "I-456/P-123/_",
) -> list[dict]:
    runner = MagicMock()
    runner.get_version.return_value = "1.0.0"

    def side_effect(args, cwd=None, **kwargs):
        res = MagicMock(returncode=0, stdout="", stderr="")
        if commands is not None:
            commands.append(list(args))
        if args[:4] == ["git", "diff", "--cached", "--quiet"]:
            res.returncode = 1
        if _cuts_branch(args):
            ledger = os.path.join(cwd, "holon-knowledge/ledger")
            os.makedirs(ledger, exist_ok=True)
            with open(os.path.join(ledger, "plans.jsonl"), "w") as f:
                f.write(json.dumps(plan_row) + "\n")
            with open(os.path.join(ledger, "intents.jsonl"), "w") as f:
                f.write(json.dumps(intent_row) + "\n")
            if plan_md is not None:
                os.makedirs(os.path.join(cwd, "plans"), exist_ok=True)
                with open(os.path.join(cwd, plan_row["plan_file"]), "w") as f:
                    f.write(plan_md)
        return res

    repo = str(tmp_path / "repo")
    with (
        patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": repo}),
        patch("sandbox_executor.entrypoint.executor.workspace_mounted", return_value=True),
        patch("sandbox_executor.entrypoint.executor.run_cmd", side_effect=side_effect),
        patch("sandbox_executor.entrypoint.executor.get_runner", return_value=runner),
        patch("sandbox_executor.entrypoint.executor.validate_mounted_workspace", new=_validate_stub),
        patch("sys.argv", ["executor.py", plan_branch, "claude-agent", "claude-opus-5-5"]),
    ):
        exit_code = 0
        try:
            executor.main()
        except SystemExit as exc:
            exit_code = exc.code
    runner.build_cmd.assert_not_called()
    ledger = os.path.join(repo, "holon-knowledge/ledger")
    assert read_merged_rows(os.path.join(ledger, "intents.jsonl")) == [intent_row]
    rows = read_merged_rows(os.path.join(ledger, "executions.jsonl"))
    statuses = [r.get("status") for r in rows if r.get("entry_type") == "summary"]
    assert exit_code == (executor.OVER_BUDGET_EXIT_CODE if statuses == ["over_budget"] else 0)
    return rows


class IncrementalNotesHarness:
    def run_executor(self, turns, env=None):
        scripted = list(turns)
        prompts = []
        git_cmds = []

        def fake_run_cmd(args, cwd=None, **kwargs):
            git_cmds.append(list(args))
            if _cuts_branch(args):
                ledger_dir = os.path.join(cwd, "holon-knowledge/ledger")
                os.makedirs(ledger_dir, exist_ok=True)
                with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
                    f.write(json.dumps({"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}) + "\n")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            # why: #95 empty-diff guard treats notes alone as an empty diff, so the agent also changes a source file
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                result.stdout = "plan-tip-sha\n"
            elif "diff" in args and "--name-only" in args:
                result.stdout = "executions/E-1-agent-model.md\0src/app.py\0"
            return result

        def fake_run_turn(cmd, **kwargs):
            prompts.append(cmd[-1])
            self.assertTrue(scripted, "executor ran more agent turns than scripted")
            return scripted.pop(0)(kwargs["watch_path"])

        runner = MagicMock()
        runner.get_version.return_value = "1.0.0"
        runner.build_cmd.side_effect = lambda model, pf, inf, prompt: ["agy", prompt]
        tmp = tempfile.TemporaryDirectory(prefix="sandbox_executor_test_")
        self.addCleanup(tmp.cleanup)
        exit_code = 0
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"HOLON_TARGET_WORKTREE": tmp.name, **(env or {})}),
            patch.object(executor, "run_cmd", side_effect=fake_run_cmd),
            patch.object(executor, "run_turn", side_effect=fake_run_turn),
            patch.object(executor, "get_runner", return_value=runner),
            patch.object(executor, "validate_mounted_workspace", new=_validate_stub),
            patch("sys.argv", ["executor.py", "I-456/P-123/_", "antigravity-agent", "gemini-3.5-flash"]),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            try:
                executor.main()
            except SystemExit as exc:
                exit_code = exc.code
        entries = read_merged_rows(os.path.join(tmp.name, "holon-knowledge/ledger/executions.jsonl"))
        return {
            "exit_code": exit_code,
            "entries": entries,
            "prompts": prompts,
            "git_cmds": git_cmds,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }

    @staticmethod
    def write_notes(content, returncode=0, stop_reason=None, breach=None, stdout="", stderr=""):  # noqa: PLR0913
        def turn(path):
            if content is not None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
            return TurnResult(
                returncode=returncode, stop_reason=stop_reason, breach=breach, stdout=stdout, stderr=stderr
            )

        return turn

    def summary(self, run):
        return next(e for e in run["entries"] if e.get("entry_type") == "summary")


class GitRemoteHarness:
    def _create_bare_remote_with_plan(self, base_dir: str, plan_branch: str) -> tuple[str, str]:
        bare_dir = os.path.join(base_dir, "bare_remote.git")
        subprocess.run(["git", "init", "--bare", "-b", "main", bare_dir], check=True, capture_output=True)
        seed_dir = os.path.join(base_dir, "seed_repo")
        subprocess.run(["git", "init", "-b", "main", seed_dir], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Seed User"], cwd=seed_dir, check=True)
        subprocess.run(["git", "config", "user.email", "seed@example.com"], cwd=seed_dir, check=True)

        src_dir = os.path.join(seed_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "codebase.py"), "w") as f:
            f.write("# Original codebase file\ndef app(): pass\n")

        ledger_dir = os.path.join(seed_dir, "holon-knowledge/ledger")
        os.makedirs(ledger_dir, exist_ok=True)
        with open(os.path.join(ledger_dir, "plans.jsonl"), "w") as f:
            f.write(
                json.dumps(
                    {
                        "plan_id": "P-123",
                        "intent_branch": "I-456/_",
                        "entropy": 2.0,
                        "entropy_budget": 5.0,
                    }
                )
                + "\n"
            )
        with open(os.path.join(ledger_dir, "intents.jsonl"), "w") as f:
            f.write(json.dumps({"branch": "I-456/_", "slug": "intent-456"}) + "\n")

        subprocess.run(["git", "add", "-A"], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial plan commit"], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "branch", "-M", plan_branch], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", bare_dir], cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", plan_branch], cwd=seed_dir, check=True, capture_output=True)

        tip_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=seed_dir, capture_output=True, text=True, check=True)
        plan_tip = tip_res.stdout.strip()
        return bare_dir, plan_tip

    def _clone_workspace(self, base_dir: str, bare_dir: str, plan_branch: str) -> str:
        workspace_dir = os.path.join(base_dir, "workspace")
        subprocess.run(
            ["git", "clone", "--branch", plan_branch, bare_dir, workspace_dir],
            check=True,
            capture_output=True,
        )
        return workspace_dir
