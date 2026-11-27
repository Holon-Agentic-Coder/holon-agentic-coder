import contextlib
import json
import logging
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.github.adapter import MERGEABILITY_FIELDS
from sandbox_executor.flow.github.pr import check_pr_mergeability
from sandbox_executor.flow.stages.stage_support import consult_knowledge
from sandbox_executor.knowledge import consult, ledger_sync
from sandbox_executor.knowledge import index as knowledge_index
from sandbox_executor.knowledge.consult import ConsultEngine

from tests import gh_probe_stub
from tests.flow.helpers import _git

REPO_ROOT = Path(__file__).resolve().parents[4]
INDEX_DB = "holon-knowledge/.index/action_index.db"
MERGEABILITY_READ = ["gh", "pr", "view", "feature", "--json", MERGEABILITY_FIELDS]
FAKE_HOST_GH = "/opt/host/bin/gh"


def _simulate_host_gh(monkeypatch: pytest.MonkeyPatch, on_path: str | None = FAKE_HOST_GH) -> None:
    real_which = shutil.which
    monkeypatch.setattr(gh_probe_stub, "HOST_GH", FAKE_HOST_GH)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: on_path if name == "gh" else real_which(name, *a, **k))


def _record_spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    spawned: list[list[str]] = []
    real_popen = subprocess.Popen

    def recording_popen(args, *rest, **kwargs):
        spawned.append([str(arg) for arg in args] if isinstance(args, (list, tuple)) else [str(args)])
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    return spawned


def test_default_ledgers_are_the_test_hosts_own_files(flow_repos):
    host, _origin = flow_repos
    ledger_dir = host / "holon-knowledge" / "ledger"

    assert [Path(path).name for path in consult.DEFAULT_LEDGERS] == ["executions.jsonl", "intents.jsonl", "plans.jsonl"]
    for path in consult.DEFAULT_LEDGERS:
        assert Path(path).is_absolute() and Path(path).parent == ledger_dir
        assert Path(path).is_file()
        assert not Path(path).is_relative_to(REPO_ROOT)
    assert ConsultEngine(project_db_path=str(host / INDEX_DB)).ledgers == consult.DEFAULT_LEDGERS


def test_consult_indexes_the_test_hosts_rows_and_none_of_the_checkouts(flow_repos):
    host, _origin = flow_repos
    rows = {
        "executions.jsonl": {"execution_id": "E-host-1", "status": "success", "labels": ["hermetic-flow"]},
        "intents.jsonl": {"branch": "I-host-1/_", "slug": "host-intent", "labels": "hermetic-flow, other"},
        "plans.jsonl": {"plan_id": "P-host-1", "summary": "host plan", "labels": ["hermetic-flow"]},
    }
    for name, row in rows.items():
        ledger = host / "holon-knowledge" / "ledger" / name
        ledger.write_text(f"{json.dumps(row)}\nthis line is not JSON\n\n", encoding="utf-8")
    context = FlowContext(repo_dir=str(host), intent_data={"slug": "hermetic", "labels": ["hermetic-flow"]})

    consulted = consult_knowledge(
        context, context.intent_data, match="any", purpose="plan", log=logging.getLogger(__name__)
    )

    seeded = {"E-host-1", "I-host-1/_", "P-host-1"}
    assert set(consulted) == seeded
    assert "Consulted 3 knowledge records for plan:" in context.logs[-1]
    with contextlib.closing(sqlite3.connect(host / INDEX_DB)) as conn:
        indexed = {record_id for (record_id,) in conn.execute("SELECT record_id FROM action_records")}
        synced = {path for (path,) in conn.execute("SELECT ledger_path FROM ledger_sync_state")}
    assert indexed == seeded, "the index holds rows that the test host's ledgers do not"
    assert synced == set(consult.DEFAULT_LEDGERS)


def test_flow_index_differs_from_productions_only_in_not_waiting_for_the_disk(
    tmp_path, _knowledge_index_without_disk_wait
):
    production_connect = _knowledge_index_without_disk_wait
    pragmas = ("journal_mode", "foreign_keys", "busy_timeout", "synchronous")

    def settings(connect, name: str) -> dict[str, object]:
        with contextlib.closing(connect(str(tmp_path / name))) as conn:
            return {pragma: conn.execute(f"PRAGMA {pragma}").fetchone()[0] for pragma in pragmas}

    production = settings(production_connect, "production.db")
    in_a_flow_test = settings(ledger_sync.connect_wal, "flow-test.db")

    assert knowledge_index.connect_wal is ledger_sync.connect_wal
    assert production == {"journal_mode": "wal", "foreign_keys": 1, "busy_timeout": 5000, "synchronous": 1}
    assert in_a_flow_test == {**production, "synchronous": 0}


def test_host_origin_is_the_tests_own_origin(flow_repos, _flow_repos_template):
    host, origin = flow_repos

    assert _git(host, "remote", "get-url", "origin") == str(origin)
    assert not origin.is_relative_to(_flow_repos_template)
    assert not list(host.glob(".git/hooks/*.sample")) and not list(origin.glob("hooks/*.sample"))


def test_mergeability_probe_starts_no_gh_when_the_host_has_one(flow_repos, monkeypatch):
    host, _origin = flow_repos
    _git(host, "checkout", "-q", "-b", "feature")
    (host / "src" / "feature.py").write_text("print('feature')\n", encoding="utf-8")
    _git(host, "add", "-A")
    _git(host, "commit", "-q", "-m", "feature")
    _simulate_host_gh(monkeypatch)
    spawned = _record_spawns(monkeypatch)

    result = check_pr_mergeability(str(host), "feature")

    assert result["source"] == "git_local"
    assert (result["mergeable"], result["merge_state_status"], result["is_dirty"]) == ("MERGEABLE", "CLEAN", False)
    assert spawned, "the local git fallback did not run"
    assert all(argv[0] == "git" for argv in spawned), spawned


def test_stub_answers_the_mergeability_read_with_the_failure_of_the_real_gh(monkeypatch):
    _simulate_host_gh(monkeypatch)

    answer = gh_probe_stub.stubbed_mergeability_read(MERGEABILITY_READ)

    assert answer is not None
    assert (answer.returncode, answer.stdout, answer.stderr) == (1, "", gh_probe_stub.STUB_STDERR)
    assert answer.args == MERGEABILITY_READ
    assert gh_probe_stub.stubbed_mergeability_read(tuple(MERGEABILITY_READ)) is not None


@pytest.mark.parametrize(
    "cmd",
    [
        ["gh", "pr", "view", "feature", "--json", "number,title"],
        ["gh", "pr", "view", "feature", "--json", MERGEABILITY_FIELDS, "--repo", "owner/name"],
        ["gh", "pr", "checks", "feature", "--json", MERGEABILITY_FIELDS],
        ["gh", "pr", "merge", "feature", "--merge", "--delete-branch"],
        [FAKE_HOST_GH, "pr", "view", "feature", "--json", MERGEABILITY_FIELDS],
        ["git", "pr", "view", "feature", "--json", MERGEABILITY_FIELDS],
        f"gh pr view feature --json {MERGEABILITY_FIELDS}",
        [],
    ],
)
def test_stub_steps_aside_for_any_other_command(monkeypatch, cmd):
    _simulate_host_gh(monkeypatch)

    assert gh_probe_stub.stubbed_mergeability_read(cmd) is None


@pytest.mark.parametrize("on_path", ["/tmp/fake-bin/gh", None])
def test_stub_steps_aside_when_path_resolves_another_gh(monkeypatch, on_path):
    _simulate_host_gh(monkeypatch, on_path=on_path)

    assert gh_probe_stub.stubbed_mergeability_read(MERGEABILITY_READ) is None


def test_stub_steps_aside_on_a_host_without_gh(monkeypatch):
    monkeypatch.setattr(gh_probe_stub, "HOST_GH", None)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)

    assert gh_probe_stub.stubbed_mergeability_read(MERGEABILITY_READ) is None
