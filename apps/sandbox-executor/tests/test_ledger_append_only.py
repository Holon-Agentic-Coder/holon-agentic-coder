"""Pure-append guarantee for the three ledgers: no write reorders, deduplicates or re-serialises existing rows."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sandbox_executor import flow
from sandbox_executor import ledger as ledger_module
from sandbox_executor.entrypoint import executor as executor_module
from sandbox_executor.entrypoint.executor import sync_and_reconcile_pre_push
from sandbox_executor.flow import (
    StageStatus,
    reconcile_jsonl_ledger_conflict,
    reconcile_ledger_file,
    run_intent_stage,
)
from sandbox_executor.ledger import (
    LedgerLock,
    LedgerRewriteError,
    assert_ledger_prefix,
    atomic_ledger_append,
    suppress_ledger_failures,
    try_append_action_record,
    try_append_ledger_line,
)

from tests.test_flow import FakeRoleAgents, _dispatch_context, _git

LEDGER_NAMES = ("intents", "plans", "executions")

#: Rows that the old reconciliation reordered: out-of-order timestamps, a repeated branch key and a description that
#: mentions a diff3 marker inside a JSON string, which the old substring scan read as a conflict.
INTENT_ROWS: list[dict[str, Any]] = [
    {"slug": "late", "branch": "I-late", "created_at": "2026-10-28T12:00:00Z"},
    {"slug": "early", "branch": "I-early", "created_at": "2026-10-01T09:00:00Z"},
    {"slug": "late", "branch": "I-late", "created_at": "2026-10-29T08:00:00Z", "status": "again"},
    {"slug": "guard", "branch": "I-guard", "description": "mentions ||||||| inside text", "created_at": "2026-10-02"},
]


def _seed_ledgers(repo: Path) -> dict[str, bytes]:
    """Write the three ledgers under ``repo`` with compact separators and return their bytes."""
    ledger_dir = repo / "holon-knowledge" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "intents": INTENT_ROWS,
        "plans": [{"plan_id": "P-2", "created_at": "2026-10-05"}, {"plan_id": "P-1", "created_at": "2026-10-01"}],
        "executions": [{"execution_id": "E-2", "created_at": "2026-10-05"}, {"execution_id": "E-1"}],
    }
    for name, ledger_rows in rows.items():
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in ledger_rows)
        (ledger_dir / f"{name}.jsonl").write_text(text, encoding="utf-8")
    return _snapshot(repo)


def _snapshot(repo: Path) -> dict[str, bytes]:
    """Return the current bytes of each ledger under ``repo``."""
    return {n: (repo / "holon-knowledge" / "ledger" / f"{n}.jsonl").read_bytes() for n in LEDGER_NAMES}


class FakeGit:
    """Records every ``run_git`` call and answers with a clean porcelain status."""

    def __init__(self, porcelain: str = "") -> None:
        self.calls: list[list[str]] = []
        self.porcelain = porcelain

    def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        stdout = self.porcelain if args[:1] == ["status"] else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def ledger_adds(self) -> list[list[str]]:
        """Return each ``git add`` call that names a ledger path."""
        return [c for c in self.calls if c[:1] == ["add"] and any("holon-knowledge/ledger" in a for a in c)]


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    """Replace git and the mergeability probe at their ``flow`` bindings."""
    fake = FakeGit()
    monkeypatch.setattr(flow, "run_git", fake)
    monkeypatch.setattr(flow, "check_pr_mergeability", lambda *_a, **_k: {"mergeable": "MERGEABLE"})
    return fake


def test_stage_precheck_leaves_ledgers_byte_identical(tmp_path: Path, fake_git: FakeGit) -> None:
    """Reproduction: the Stage 1 pre-check must not rewrite or stage a ledger that only mentions a marker."""
    before = _seed_ledgers(tmp_path)
    result = flow.check_and_resolve_stage_conflicts(str(tmp_path), stage_name="intent", branch=None)
    assert _snapshot(tmp_path) == before
    assert fake_git.ledger_adds() == []
    assert result["conflicting_files"] == []


#: A minimal action row for the guarded helpers.
GUARD_ACTION: dict[str, Any] = {
    "action_id": "ACT-GUARD-1",
    "execution_id": "E-guard",
    "action": "Guarded append",
    "category": "code",
    "target": "I-1/P-2/E-3/_",
    "outcome": "success",
    "created_at": "2026-10-29T10:00:00+00:00",
}


def _report_rewrite(path: str, before: bytes) -> None:
    """Stand in for the prefix guard detecting a rewrite."""
    raise LedgerRewriteError(f"Ledger {path} was rewritten, not appended")


class TestPrefixGuard:
    """``assert_ledger_prefix`` and the guarded ``atomic_ledger_append`` fail closed on any non-append write."""

    def test_pure_append_passes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "plans.jsonl"
        ledger.write_bytes(b'{"plan_id":"P-1"}\n')
        before = ledger.read_bytes()
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"plan_id":"P-2"}\n')
        assert ledger.read_bytes() == before + b'{"plan_id":"P-2"}\n'
        assert_ledger_prefix(str(ledger), before)

    def test_missing_file_counts_as_empty(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger" / "executions.jsonl"
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"execution_id":"E-1"}\n')
        assert_ledger_prefix(str(ledger), b"")

    @pytest.mark.parametrize("rewritten", [b'{"plan_id":"P-1"', b'{"plan_id":"P-2"}\n{"plan_id":"P-1"}\n'])
    def test_truncated_or_reordered_file_raises(self, tmp_path: Path, rewritten: bytes) -> None:
        ledger = tmp_path / "plans.jsonl"
        ledger.write_bytes(b'{"plan_id":"P-1"}\n{"plan_id":"P-2"}\n')
        before = ledger.read_bytes()
        ledger.write_bytes(rewritten)
        with pytest.raises(LedgerRewriteError, match="first divergence at byte"):
            assert_ledger_prefix(str(ledger), before)

    def test_rewrite_inside_append_context_raises(self, tmp_path: Path) -> None:
        ledger = tmp_path / "intents.jsonl"
        ledger.write_bytes(b'{"slug":"b"}\n{"slug":"a"}\n')
        with pytest.raises(LedgerRewriteError), atomic_ledger_append(str(ledger)) as f:
            ledger.write_bytes(b'{"slug":"a"}\n')
            f.write('{"slug":"c"}\n')

    def test_try_append_action_record_propagates_rewrite_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ledger_module, "assert_ledger_prefix", _report_rewrite)
        reported: list[str] = []
        with pytest.raises(LedgerRewriteError):
            try_append_action_record(
                str(tmp_path / "executions.jsonl"), dict(GUARD_ACTION), label="guard", log_fn=reported.append
            )
        assert reported == []

    def test_try_append_ledger_line_propagates_rewrite_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ledger_module, "assert_ledger_prefix", _report_rewrite)
        reported: list[str] = []
        with pytest.raises(LedgerRewriteError):
            try_append_ledger_line(
                str(tmp_path / "executions.jsonl"), {"execution_id": "E-1"}, label="guard", log_fn=reported.append
            )
        assert reported == []

    def test_resolution_that_moves_the_prefix_never_touches_the_file(self, tmp_path: Path) -> None:
        ledger = tmp_path / "executions.jsonl"
        ledger.write_text(_conflicted(), encoding="utf-8")
        before = ledger.read_bytes()
        with pytest.raises(LedgerRewriteError), LedgerLock(str(ledger)) as locked:
            flow._write_resolved_ledger(locked, str(ledger), f"{OURS_ROW}\n", f"{THEIRS_ROWS[0]}\n{OURS_ROW}\n")
        assert ledger.read_bytes() == before

    def test_suppress_ledger_failures_hides_other_errors_but_not_a_rewrite(self) -> None:
        with suppress_ledger_failures():
            raise OSError("disk full")
        with pytest.raises(LedgerRewriteError), suppress_ledger_failures():
            raise LedgerRewriteError("rewritten")

    def test_reconcile_ledger_file_propagates_rewrite_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ledger = tmp_path / "executions.jsonl"
        ledger.write_text(_conflicted(), encoding="utf-8")
        monkeypatch.setattr(flow, "assert_ledger_prefix", _report_rewrite)
        with pytest.raises(LedgerRewriteError):
            reconcile_ledger_file(str(ledger))

    def test_reconcile_missing_ledger_file_is_false_and_creates_nothing(self, tmp_path: Path) -> None:
        assert reconcile_ledger_file(str(tmp_path / "executions.jsonl")) is False
        assert list(tmp_path.iterdir()) == []

    def test_unknown_prefix_side_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="prefix_side"):
            reconcile_jsonl_ledger_conflict(_conflicted(), prefix_side="main")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stage 1 against a local bare origin whose ledgers hold the rows that used to be reordered
# ---------------------------------------------------------------------------


class GuardedIntentAgents(FakeRoleAgents):
    """Intent-creator stand-in that follows the real entrypoint: stage pre-check, then a guarded ledger append.

    ``exit_code`` models a container that fails after its clone, before it pushes anything.
    """

    def __init__(self, root: Path, origin: Path, exit_code: int = 0) -> None:
        super().__init__(root, origin)
        self.exit_code = exit_code

    def _intent(self, work: Path, _args: list[str], kwargs: dict[str, Any]) -> int:
        if self.exit_code != 0:
            return self.exit_code
        with open(kwargs["intent_file"], encoding="utf-8") as f:
            payload = json.load(f)
        intent_branch = f"{payload['branch']}/_"
        _git(work, "checkout", "-q", "-B", intent_branch, "origin/main")
        flow.check_and_resolve_stage_conflicts(str(work), stage_name="intent")
        with atomic_ledger_append(str(work / "holon-knowledge/ledger/intents.jsonl")) as lf:
            lf.write(json.dumps({**payload, "status": "proposed"}) + "\n")
        self._push(work, intent_branch, f"intent: created {intent_branch}")
        return 0


@pytest.fixture
def seeded_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, bytes]]:
    """Bare origin whose main holds the seeded ledgers, a host clone the flow runs in, and main's ledger bytes.

    Git runs for real against the local origin; only the mergeability probe is stubbed, so ``gh`` is never asked.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(flow, "check_pr_mergeability", lambda *_a, **_k: {"mergeable": "MERGEABLE"})
    origin, seed, host = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "host"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True, capture_output=True)
    main_bytes = _seed_ledgers(seed)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", str(origin), "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(host)], check=True, capture_output=True)
    return host, origin, main_bytes


def _assert_host_untouched(host: Path, main_bytes: dict[str, bytes]) -> None:
    """The operator's checkout keeps main's ledger bytes and has nothing staged."""
    assert _snapshot(host) == main_bytes
    assert _git(host, "diff", "--cached", "--name-only") == ""
    assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""


def _origin_ledger(host: Path, ref: str, name: str) -> bytes:
    """Return the bytes of a ledger at ``ref`` as git stores them."""
    blob = f"{ref}:holon-knowledge/ledger/{name}.jsonl"
    return subprocess.run(["git", "show", blob], cwd=host, check=True, capture_output=True).stdout


class TestIntentStageLedgers:
    """Successful, failed and dry-run Stage 1 runs keep every ledger a pure append of main's bytes."""

    def test_successful_stage_appends_exactly_one_intent_row(self, seeded_repos, tmp_path: Path) -> None:
        host, origin, main_bytes = seeded_repos
        ctx = _dispatch_context(host, GuardedIntentAgents(tmp_path, origin))

        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        _git(host, "fetch", "-q", "origin")
        for name in LEDGER_NAMES:
            branch_bytes = _origin_ledger(host, f"origin/{ctx.intent_branch}", name)
            assert branch_bytes.startswith(main_bytes[name]), name
        new_intents = _origin_ledger(host, f"origin/{ctx.intent_branch}", "intents")[len(main_bytes["intents"]) :]
        assert new_intents.count(b"\n") == 1
        assert json.loads(new_intents)["slug"] == "dispatch-test"
        _assert_host_untouched(host, main_bytes)

    def test_failed_stage_leaves_ledgers_identical_and_unstaged(self, seeded_repos, tmp_path: Path) -> None:
        host, origin, main_bytes = seeded_repos
        ctx = _dispatch_context(host, GuardedIntentAgents(tmp_path, origin, exit_code=3))

        result = run_intent_stage(ctx)

        assert result.status == StageStatus.FAILED
        assert "exited with code 3" in (result.error or "")
        _assert_host_untouched(host, main_bytes)

    def test_dry_run_leaves_ledgers_identical_and_unstaged(self, seeded_repos, tmp_path: Path) -> None:
        host, origin, main_bytes = seeded_repos
        agents = GuardedIntentAgents(tmp_path, origin)
        ctx = _dispatch_context(host, agents, dry_run=True)

        assert run_intent_stage(ctx).status == StageStatus.SUCCESS

        assert agents.calls == []
        _assert_host_untouched(host, main_bytes)


# ---------------------------------------------------------------------------
# Conflict resolution is a pure append
# ---------------------------------------------------------------------------

OURS_ROW = '{"execution_id":"E-ours","created_at":"2026-10-29T10:00:00Z"}'
THEIRS_ROWS = ['{"execution_id":"E-z","created_at":"2026-10-29T11:00:00Z"}', '{"execution_id":"E-a"}']
BASE_ROW = '{"execution_id":"E-base","created_at":"2026-10-28T00:00:00Z"}'


def _conflicted(diff3: bool = False) -> str:
    """Return ledger content with a real line-anchored conflict between ours and theirs appends."""
    base_section = "||||||| merged common ancestors\n" if diff3 else ""
    theirs = "".join(f"{row}\n" for row in THEIRS_ROWS)
    return f"{BASE_ROW}\n<<<<<<< HEAD\n{OURS_ROW}\n{base_section}=======\n{theirs}>>>>>>> origin/main\n"


class TestAppendOnlyResolution:
    """``reconcile_jsonl_ledger_conflict`` and ``reconcile_ledger_file`` never sort, dedupe by key or re-serialise."""

    def test_content_without_marker_lines_is_unchanged(self) -> None:
        content = "".join(json.dumps(r) + "\n" for r in INTENT_ROWS)
        assert reconcile_jsonl_ledger_conflict(content) == content

    @pytest.mark.parametrize("diff3", [False, True])
    def test_ours_prefix_then_missing_theirs_lines_in_order(self, diff3: bool) -> None:
        resolved = reconcile_jsonl_ledger_conflict(_conflicted(diff3))
        assert resolved == "\n".join([BASE_ROW, OURS_ROW, *THEIRS_ROWS]) + "\n"

    def test_theirs_prefix_keeps_target_bytes_first(self) -> None:
        resolved = reconcile_jsonl_ledger_conflict(_conflicted(), prefix_side="theirs")
        assert resolved == "\n".join([BASE_ROW, *THEIRS_ROWS, OURS_ROW]) + "\n"

    def test_lines_already_present_are_not_repeated(self) -> None:
        content = f"<<<<<<< HEAD\n{OURS_ROW}\n=======\n{OURS_ROW}\n{THEIRS_ROWS[1]}\n>>>>>>> main\n"
        assert reconcile_jsonl_ledger_conflict(content) == f"{OURS_ROW}\n{THEIRS_ROWS[1]}\n"

    def test_file_without_marker_lines_is_not_rewritten(self, tmp_path: Path) -> None:
        before = _seed_ledgers(tmp_path)
        ledger = tmp_path / "holon-knowledge" / "ledger" / "intents.jsonl"
        mtime = ledger.stat().st_mtime_ns
        assert reconcile_ledger_file(str(ledger)) is True
        for name in LEDGER_NAMES:
            assert reconcile_ledger_file(str(tmp_path / "holon-knowledge" / "ledger" / f"{name}.jsonl")) is True
        assert _snapshot(tmp_path) == before
        assert ledger.stat().st_mtime_ns == mtime

    def test_conflicted_file_resolves_with_ours_bytes_as_prefix(self, tmp_path: Path) -> None:
        ledger = tmp_path / "executions.jsonl"
        ledger.write_text(_conflicted(diff3=True), encoding="utf-8")
        assert reconcile_ledger_file(str(ledger)) is True
        assert ledger.read_text(encoding="utf-8") == "\n".join([BASE_ROW, OURS_ROW, *THEIRS_ROWS]) + "\n"
        assert [p.name for p in tmp_path.iterdir()] == ["executions.jsonl"]

    def test_resolution_rewrites_in_place_so_a_waiting_appender_lands_after_it(self, tmp_path: Path) -> None:
        ledger = tmp_path / "executions.jsonl"
        ledger.write_text(_conflicted(), encoding="utf-8")
        inode = ledger.stat().st_ino
        assert reconcile_ledger_file(str(ledger)) is True
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"execution_id":"E-next"}\n')
        assert ledger.stat().st_ino == inode
        expected = [BASE_ROW, OURS_ROW, *THEIRS_ROWS, '{"execution_id":"E-next"}']
        assert ledger.read_text(encoding="utf-8") == "\n".join(expected) + "\n"

    def test_stage_precheck_resolves_marker_conflict_without_staging(self, tmp_path: Path, fake_git: FakeGit) -> None:
        ledger = tmp_path / "holon-knowledge" / "ledger" / "executions.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text(_conflicted(), encoding="utf-8")
        fake_git.porcelain = "UU holon-knowledge/ledger/executions.jsonl\n"

        result = flow.check_and_resolve_stage_conflicts(str(tmp_path), stage_name="execute")

        assert result["resolved_ledgers"] == ["holon-knowledge/ledger/executions.jsonl"]
        assert ledger.read_text(encoding="utf-8").startswith(f"{BASE_ROW}\n{OURS_ROW}\n")
        assert fake_git.ledger_adds() == []


class TestPrePushMerge:
    """Merging the advanced target branch keeps its ledger bytes as the prefix of the branch's ledger."""

    @staticmethod
    def _advance_main(origin: Path, tmp_path: Path, rel_path: str, text: str) -> bytes:
        """Append ``text`` to ``rel_path`` on origin's main from a peer clone and return the file's new bytes."""
        peer = tmp_path / "peer"
        subprocess.run(["git", "clone", "-q", str(origin), str(peer)], check=True, capture_output=True)
        (peer / rel_path).parent.mkdir(parents=True, exist_ok=True)
        with open(peer / rel_path, "a", encoding="utf-8") as f:
            f.write(text)
        _git(peer, "add", "-A")
        _git(peer, "commit", "-q", "-m", "peer")
        _git(peer, "push", "-q", "origin", "main")
        return (peer / rel_path).read_bytes()

    def _branch_with_intent_row(self, host: Path) -> Path:
        _git(host, "checkout", "-q", "-b", "feature")
        ledger = host / "holon-knowledge" / "ledger" / "intents.jsonl"
        with atomic_ledger_append(str(ledger)) as f:
            f.write('{"slug":"branch-row","created_at":"2026-09-01"}\n')
        _git(host, "commit", "-qam", "branch row")
        return ledger

    def test_ledger_conflict_keeps_main_bytes_as_prefix(self, seeded_repos, tmp_path: Path) -> None:
        host, origin, _ = seeded_repos
        ledger = self._branch_with_intent_row(host)
        main_row = '{"slug":"main-row","created_at":"2026-10-30"}\n'
        main_intents = self._advance_main(origin, tmp_path, "holon-knowledge/ledger/intents.jsonl", main_row)

        ok, msg = sync_and_reconcile_pre_push(str(host), target_branch="main")

        assert ok, msg
        assert ledger.read_bytes() == main_intents + b'{"slug":"branch-row","created_at":"2026-09-01"}\n'
        assert _git(host, "status", "--porcelain", "--untracked-files=no") == ""

    def test_clean_merge_leaves_ledgers_as_git_merged_them(self, seeded_repos, tmp_path: Path) -> None:
        host, origin, main_bytes = seeded_repos
        ledger = self._branch_with_intent_row(host)
        branch_bytes = ledger.read_bytes()
        self._advance_main(origin, tmp_path, "src/other.py", "x = 1\n")

        ok, msg = sync_and_reconcile_pre_push(str(host), target_branch="main")

        assert ok, msg
        assert ledger.read_bytes() == branch_bytes
        assert branch_bytes.startswith(main_bytes["intents"])
        for name in ("plans", "executions"):
            assert (host / "holon-knowledge" / "ledger" / f"{name}.jsonl").read_bytes() == main_bytes[name]

    def test_rewrite_during_ledger_conflict_aborts_the_merge_and_propagates(
        self, seeded_repos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        host, origin, _ = seeded_repos
        self._branch_with_intent_row(host)
        self._advance_main(origin, tmp_path, "holon-knowledge/ledger/intents.jsonl", '{"slug":"main-row"}\n')
        head = _git(host, "rev-parse", "HEAD")
        monkeypatch.setattr(flow, "assert_ledger_prefix", _report_rewrite)

        with pytest.raises(LedgerRewriteError):
            sync_and_reconcile_pre_push(str(host), target_branch="main")

        assert _git(host, "rev-parse", "HEAD") == head
        assert not (host / ".git" / "MERGE_HEAD").exists()


REPO_LEDGER_DIR = Path(__file__).resolve().parents[3] / "holon-knowledge" / "ledger"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(not (REPO_LEDGER_DIR / "intents.jsonl").exists(), reason="repository ledgers are absent")
def test_repository_ledgers_survive_precheck_and_reconcile_byte_for_byte(tmp_path: Path, fake_git: FakeGit) -> None:
    """Main's real ledgers (one row mentions ``|||||||``) pass through unchanged; the originals are only read."""
    originals = {n: _digest(REPO_LEDGER_DIR / f"{n}.jsonl") for n in LEDGER_NAMES}
    copy_dir = tmp_path / "holon-knowledge" / "ledger"
    copy_dir.mkdir(parents=True)
    for name in LEDGER_NAMES:
        shutil.copyfile(REPO_LEDGER_DIR / f"{name}.jsonl", copy_dir / f"{name}.jsonl")
    before = _snapshot(tmp_path)

    flow.check_and_resolve_stage_conflicts(str(tmp_path), stage_name="intent", branch=None)
    for name in LEDGER_NAMES:
        assert reconcile_ledger_file(str(copy_dir / f"{name}.jsonl")) is True
    assert _snapshot(tmp_path) == before
    assert fake_git.ledger_adds() == []

    with atomic_ledger_append(str(copy_dir / "intents.jsonl")) as f:
        f.write('{"slug":"appended"}\n')
    assert (copy_dir / "intents.jsonl").read_bytes() == before["intents"] + b'{"slug":"appended"}\n'
    assert {n: _digest(REPO_LEDGER_DIR / f"{n}.jsonl") for n in LEDGER_NAMES} == originals


# ---------------------------------------------------------------------------
# Callers let a detected rewrite stop the stage instead of logging and carrying on
# ---------------------------------------------------------------------------


def _run_executor_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, plan: dict[str, Any] | None = None, status: str = "tip-sha\n"
) -> list[list[str]]:
    """Run the executor entrypoint with faked git and agent until it raises; return every command it ran."""
    from tests.test_executor import _turn_via_run_cmd

    commands: list[list[str]] = []
    plan_row = plan or {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 2.0}

    def fake_run_cmd(args: list[str], cwd: str | None = None, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(list(args))
        if "clone" in args and cwd:
            plans = Path(cwd) / "holon-knowledge" / "ledger" / "plans.jsonl"
            plans.parent.mkdir(parents=True, exist_ok=True)
            plans.write_text(json.dumps(plan_row) + "\n")
        if "diff" in args and "--name-only" in args:
            return subprocess.CompletedProcess(args, 0, stdout="src/app.py\0", stderr="")
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, stdout=status, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="tip-sha\n", stderr="")

    runner = SimpleNamespace(
        validate=lambda *_a, **_k: None,
        get_version=lambda: "1.0.0",
        build_cmd=lambda *_a, **_k: ["agent", "prompt"],
    )
    monkeypatch.setattr(executor_module, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(executor_module, "run_turn", _turn_via_run_cmd)
    monkeypatch.setattr(executor_module, "get_runner", lambda *_a, **_k: runner)
    monkeypatch.setattr(executor_module, "get_repo_url", lambda *_a, **_k: "/fake/repo")
    monkeypatch.setenv("HOLON_REPO_DIR", str(tmp_path))
    monkeypatch.setenv("HOLON_SKIP_PUSH", "1")
    monkeypatch.setattr("sys.argv", ["executor.py", "I-456/P-123/_", "agent", "model"])
    with pytest.raises(LedgerRewriteError):
        executor_module.main()
    return commands


@pytest.mark.parametrize("action_logging", ["0", "1"], ids=["summary-row", "action-record"])
def test_executor_rewrite_stops_before_commit_and_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action_logging: str
) -> None:
    """The summary-row write and the best-effort action-record writes both fail closed."""
    monkeypatch.setenv("HOLON_ACTION_LOGGING", action_logging)
    monkeypatch.setattr(ledger_module, "assert_ledger_prefix", _report_rewrite)
    commands = _run_executor_main(tmp_path, monkeypatch)
    assert not any(c[:2] in (["git", "commit"], ["git", "push"]) for c in commands)


def _rewrite_only(event_type: str, target: str | None = None) -> Any:
    """Wrap the action-record writer so only the named record reports a rewrite and every other write is real."""
    real = executor_module.append_action_record

    def fake(path: str, record: Any, **kwargs: Any) -> Any:
        if record.event_type == event_type and target in (None, record.target):
            raise LedgerRewriteError(f"Ledger {path} was rewritten, not appended")
        return real(path, record, **kwargs)

    return fake


def _unrepairable_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor_module, "_probe_git_repo", lambda _d: (False, "broken"))
    monkeypatch.setattr(executor_module, "_repair_git_repo", lambda *_a, **_k: False)


def _budget_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    classify = executor_module.classify_execution_outcome
    monkeypatch.setattr(
        executor_module, "classify_execution_outcome", lambda o: (*classify(o)[:2], object(), "over budget")
    )
    monkeypatch.setattr(executor_module, "record_budget_failure", _raise_rewrite)


def _over_budget_plan(_monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return {"plan": {"plan_id": "P-123", "intent_branch": "I-456/_", "entropy": 40.0, "entropy_budget": 5.0}}


def _markdown_changed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(executor_module, "converge_prettier", lambda *_a, **_k: None)
    return {"status": " M notes.md\n"}


_EXECUTOR_SITES: dict[str, tuple[str | None, str | None, Any]] = {
    "plan-decomposition": ("plan_decomposition", None, _over_budget_plan),
    "budget-failure": (None, None, _budget_breach),
    "agent-command": ("command_execution", None, None),
    "git-recovery": ("git_mutation", ".git", _unrepairable_repo),
    "prettier-action": ("doc_update", None, _markdown_changed),
    "commit-action": ("git_mutation", "HEAD", None),
}


@pytest.mark.parametrize("site", list(_EXECUTOR_SITES))
def test_each_executor_write_site_fails_closed_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """A rewrite reported by one write alone still stops the run, so no site may swallow it."""
    event_type, target, arrange = _EXECUTOR_SITES[site]
    monkeypatch.setenv("HOLON_ACTION_LOGGING", "1")
    if event_type:
        monkeypatch.setattr(executor_module, "append_action_record", _rewrite_only(event_type, target))
    extra = (arrange(monkeypatch) if arrange else None) or {}
    commands = _run_executor_main(tmp_path, monkeypatch, **extra)
    assert not any(c[:2] in (["git", "commit"], ["git", "push"]) for c in commands)


def _raise_rewrite(*_args: Any, **_kwargs: Any) -> Any:
    """Stand in for a writer whose prefix guard detected a rewrite."""
    raise LedgerRewriteError("rewritten")


def _flow_context(tmp_path: Path, **kwargs: Any) -> flow.FlowContext:
    return flow.FlowContext(
        repo_dir=str(tmp_path),
        execution_branch="I-100-intent/P-200-plan/E-300-exec/_",
        plan_branch="I-100-intent/P-200-plan/_",
        **kwargs,
    )


def test_superseding_row_rewrite_propagates_before_push(
    tmp_path: Path, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger_module, "assert_ledger_prefix", _report_rewrite)
    with pytest.raises(LedgerRewriteError):
        flow._append_superseding_execution_row(_flow_context(tmp_path), "I-1/P-2/E-3/_", {"execution_id": "E-3"})
    assert not any(c[:1] in (["commit"], ["push"]) for c in fake_git.calls)


def test_calibration_rewrite_propagates_instead_of_writing_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback_calls: list[Any] = []
    monkeypatch.setattr(flow, "_checkout_execution_branch", lambda _ctx: None)
    monkeypatch.setattr(flow, "run_calibrate", _raise_rewrite)
    monkeypatch.setattr(flow, "record_calibration_action", lambda *a, **_k: fallback_calls.append(a))
    with pytest.raises(LedgerRewriteError):
        flow.run_calibrate_stage(_flow_context(tmp_path))
    assert fallback_calls == []
    assert not (tmp_path / "plans").exists()


def test_stage5_reconciliation_rewrite_propagates_before_push(
    tmp_path: Path, fake_git: FakeGit, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_ledgers(tmp_path)
    report = SimpleNamespace(calibrated_branch="I-100-intent/P-200-plan/E-300-exec/calibrated", to_dict=dict)
    monkeypatch.setattr(flow, "_checkout_execution_branch", lambda _ctx: None)
    monkeypatch.setattr(flow, "run_calibrate", lambda **_k: report)
    monkeypatch.setattr(flow, "reconcile_records", _raise_rewrite)
    with pytest.raises(LedgerRewriteError):
        flow.run_calibrate_stage(_flow_context(tmp_path))
    assert not any(c[:1] in (["commit"], ["push"]) for c in fake_git.calls)
