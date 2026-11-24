"""The ``FakeGitHub`` double stays shaped like ``GitHubAdapter`` and never spawns a process."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest
from sandbox_executor.flow.github.adapter import GitHubAdapter

from tests.flow.github import fake_github
from tests.flow.github.fake_github import FakeGitHub, ci_checks_payload

ADAPTER_METHODS = sorted(
    name
    for name, member in inspect.getmembers(GitHubAdapter, inspect.isfunction)
    if not name.startswith("_") and name != "available"
)


@pytest.mark.parametrize("name", ADAPTER_METHODS)
def test_fake_has_every_adapter_method_with_the_same_parameters(name):
    expected = list(inspect.signature(getattr(GitHubAdapter, name)).parameters)
    assert list(inspect.signature(getattr(FakeGitHub, name)).parameters) == expected


def test_fake_has_the_adapter_availability_attribute():
    assert isinstance(GitHubAdapter.available, property)
    assert FakeGitHub().available is True and FakeGitHub(available=False).available is False


def test_fake_imports_no_subprocess_at_runtime():
    tree = ast.parse(Path(inspect.getfile(fake_github)).read_text(encoding="utf-8"))
    runtime_imports = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert "subprocess" not in runtime_imports and "shutil" not in runtime_imports


def test_fake_carries_a_proxy_env_defaulting_to_empty():
    assert FakeGitHub().proxy_env == {}
    assert FakeGitHub(proxy_env={"HTTP_PROXY": "p"}).proxy_env == {"HTTP_PROXY": "p"}


def test_checks_sequence_answers_in_order_and_repeats_the_last_payload():
    github = FakeGitHub(checks=[], checks_sequence=[None, ci_checks_payload(pending=1), ci_checks_payload(passed=1)])
    answers = [github.ci_checks("7", 5) for _ in range(4)]
    assert answers[0].returncode == 1
    assert [check["bucket"] for check in json.loads(answers[1].stdout)] == ["pending"]
    assert [json.loads(answer.stdout)[0]["bucket"] for answer in answers[2:]] == ["pass", "pass"]
    assert github.methods() == ["ci_checks"] * 4


def test_ci_checks_payload_orders_buckets_and_names_checks():
    payload = ci_checks_payload(passed=1, failed=1, pending=1, names=["lint"])
    assert [(c["name"], c["bucket"], c["state"]) for c in payload] == [
        ("lint", "pass", "SUCCESS"),
        ("check-2", "fail", "FAILURE"),
        ("check-3", "pending", "PENDING"),
    ]
    assert payload[1]["link"] == "https://github.com/o/r/actions/runs/101/job/1"


def test_rerun_failed_answers_from_rerun_ok_and_records_the_call():
    github = FakeGitHub()
    assert github.rerun_failed("9", 10) is True
    assert FakeGitHub(rerun_ok=False).rerun_failed("9") is False
    assert FakeGitHub(available=False).rerun_failed("9") is False
    assert github.calls == [("rerun_failed", ("9", 10))]


def test_pr_head_sha_honours_its_timeout_and_target():
    github = FakeGitHub(head_sha="abc")
    assert github.pr_head_sha("feat", 10) == "abc"
    assert github.pr_head_sha("feat", 0) is None
    assert github.pr_head_sha("", 10) is None
    assert github.calls[0] == ("pr_head_sha", ("feat", 10))
