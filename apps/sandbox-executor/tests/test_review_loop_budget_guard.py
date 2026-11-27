import json
import os
import subprocess
from unittest.mock import patch

import pytest
from sandbox_executor.review_report import FindingDisposition

from tests.test_review_loop import _isolate_git_global_config, _real_git, _seed_local_remote

BUDGET_CONFIG = "holon-config/hygiene/module_budgets.json"
BUDGET_FINDING = FindingDisposition(
    id="FIND-R1-002", severity="important", path="fix.py", line=1, summary="too long", disposition="applied"
)


def _seed_with_budget_config(tmp_path, monkeypatch):
    _isolate_git_global_config(monkeypatch, tmp_path)
    _remote, seed = _seed_local_remote(tmp_path)
    (seed / BUDGET_CONFIG).parent.mkdir(parents=True)
    (seed / BUDGET_CONFIG).write_text('{"default_max_lines": 200}\n', encoding="utf-8")
    _real_git(seed, "add", "-A")
    _real_git(seed, "commit", "-qm", "budget config")
    return seed, _real_git(seed, "rev-parse", "HEAD")


def _commit_with_budget_edit(seed, head, intent_data, other_change=True):
    from sandbox_executor.entrypoint.review_loop import CommitOptions, _commit_resolution

    (seed / BUDGET_CONFIG).write_text('{"default_max_lines": 999}\n', encoding="utf-8")
    if other_change:
        (seed / "fix.py").write_text("fixed code\n", encoding="utf-8")
    with patch("sandbox_executor.entrypoint.review_loop._verify_before_push", return_value=(True, True, None)):
        return _commit_resolution(
            str(seed), "main", 1, (BUDGET_FINDING,), CommitOptions(pre_resolver_head=head, intent_data=intent_data)
        )


def test_resolver_commit_restores_an_unauthorised_budget_config_change(tmp_path, monkeypatch, capsys):
    seed, head = _seed_with_budget_config(tmp_path, monkeypatch)
    _updated, new_head, _lint, _tests, err = _commit_with_budget_edit(seed, head, {"branch": "I-1-x"})

    assert err is None and new_head != head
    assert _real_git(seed, "diff", "--name-only", head, new_head) == "fix.py"
    assert (seed / BUDGET_CONFIG).read_text(encoding="utf-8") == '{"default_max_lines": 200}\n'
    assert "module budget configuration is frozen" in capsys.readouterr().err


def test_resolver_commit_with_only_a_budget_config_change_reports_the_refusal(tmp_path, monkeypatch):
    seed, head = _seed_with_budget_config(tmp_path, monkeypatch)
    _updated, new_head, _lint, _tests, err = _commit_with_budget_edit(seed, head, {}, other_change=False)

    assert new_head == head
    assert err is not None and "split the module" in err
    assert _real_git(seed, "status", "--porcelain") == ""


def test_resolver_commit_keeps_an_authorised_budget_config_change(tmp_path, monkeypatch):
    seed, head = _seed_with_budget_config(tmp_path, monkeypatch)
    intent = {"module_budget_change": "Maintainer, 2026-11-20: raise the default."}
    _updated, new_head, _lint, _tests, err = _commit_with_budget_edit(seed, head, intent)

    assert err is None
    assert set(_real_git(seed, "diff", "--name-only", head, new_head).splitlines()) == {BUDGET_CONFIG, "fix.py"}


@pytest.mark.parametrize(("intent", "expected"), [({}, None), ({"module_budget_change": "Maintainer: yes"}, "1")])
def test_the_resolver_lint_sees_the_budget_flag_only_when_authorised(tmp_path, monkeypatch, intent, expected):
    from sandbox_executor.entrypoint import review_loop

    monkeypatch.setenv("HOLON_ALLOW_MODULE_BUDGET_CHANGE", "1")
    ledger = tmp_path / "holon-knowledge" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "intents.jsonl").write_text(json.dumps({"branch": "I-1-x", **intent}) + "\n", encoding="utf-8")
    review_loop.with_module_budget_flag(os.environ, review_loop._intent_row(str(tmp_path), "I-1-x/P-2/E-3/_"))
    seen: dict[str, str | None] = {}

    def fake_run(args, cwd=None, env=None, check=False):
        seen["flag"] = (env or {}).get("HOLON_ALLOW_MODULE_BUDGET_CHANGE")
        return subprocess.CompletedProcess(args, 1, "", "lint failed")

    monkeypatch.setattr(review_loop, "check_lockfile", lambda repo_dir: None)
    monkeypatch.setattr(review_loop, "run_cmd", fake_run)
    review_loop._verify_before_push(str(tmp_path), 1)
    assert seen["flag"] == expected


def test_the_intent_row_matches_the_branch_only_on_a_component_boundary(tmp_path):
    from sandbox_executor.entrypoint.review_loop import _intent_row

    ledger = tmp_path / "holon-knowledge" / "ledger"
    ledger.mkdir(parents=True)
    rows = [{"branch": "I-1-x", "module_budget_change": "yes"}, {"branch": "I-1-xy"}]
    (ledger / "intents.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert _intent_row(str(tmp_path), "I-1-xy/P-2/E-3/_") == {"branch": "I-1-xy"}
    assert _intent_row(str(tmp_path), "I-1-x/P-2/E-3/_")["module_budget_change"] == "yes"
    assert _intent_row(str(tmp_path), "I-1-x") == rows[0]
    assert _intent_row(str(tmp_path), "other/I-1-x") == {}
