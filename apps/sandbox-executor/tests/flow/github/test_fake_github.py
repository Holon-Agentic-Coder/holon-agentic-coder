"""The ``FakeGitHub`` double stays shaped like ``GitHubAdapter`` and never spawns a process."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from sandbox_executor.flow.github.adapter import GitHubAdapter

from tests.flow.github import fake_github
from tests.flow.github.fake_github import FakeGitHub

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
