"""Tests for the platform-sensitivity rules of the macOS relevance gate: every path rule and every content pattern.

Each rule has a case that fires and a neutral neighbour that must not; a false "sensitive" is tolerated by design, a
false "neutral" is not. Git is not involved: ``judge_change`` reads contents through a dictionary.
"""

from __future__ import annotations

import pytest

from scripts.macos_relevance_git import ChangedFile
from scripts.macos_relevance_rules import (
    BY_CONTENT,
    BY_PATH,
    CONTENT_PATTERNS,
    UNREADABLE_CONTENT,
    Verdict,
    blob_reason,
    content_reason,
    judge_change,
    path_reason,
    shebang_reason,
    strip_comment,
)


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "apps/sandbox-executor/pyproject.toml",
        "uv.lock",
        "apps/sandbox-executor/uv.lock",
        ".github/workflows/test-unit.yml",
        ".github/workflows/README.md",
        ".github/actions/macos-relevance-gate/action.yml",
        "scripts/macos_relevance.py",
        "scripts/macos_relevance_rules.py",
        "scripts/ci_bookkeeping_gate.py",
        "scripts/ci_bookkeeping_api.py",
        "scripts/ci_bookkeeping_chain.py",
        "scripts/ci_timing_gate.py",
        "apps/sandbox-executor/tests/test_macos_relevance_git.py",
        "apps/sandbox-executor/build_all_images.sh",
        "tools/setup.bash",
        "tools/profile.zsh",
        "holon",
        "Makefile",
        "apps/sandbox-executor/Dockerfile",
        "docker/Dockerfile.sidecar",
        "docker/agent.Dockerfile",
        "docker-bake.hcl",
        "apps/sandbox-executor/src/sandbox_executor/launcher/run.py",
        "apps/sandbox-executor/src/sandbox_executor/launcher/README.md",
        "apps/sandbox-executor/src/sandbox_executor/entrypoint/planner.py",
        "apps/sandbox-executor/entrypoint/run",
        "apps/sandbox-executor/src/sandbox_executor/claude_keychain.py",
        "docs/Keychain.md",
        "apps/x/credential_store.py",
        "apps/x/launcher_args.json",
        "apps/sandbox-executor/tests/conftest.py",
        "",
    ],
)
def test_sensitive_paths(path: str) -> None:
    assert path_reason(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/safety.md",
        "plans/I-x/P-y.md",
        "holon-knowledge/ledger/plans.jsonl",
        "holon-config/hygiene/module_budgets.json",
        "apps/sandbox-executor/src/sandbox_executor/flow/stage5.py",
        "apps/sandbox-executor/tests/test_flow.py",
        "scripts/ci_durations.py",
        "scripts/ci_timing_noise.py",
        "github/workflows/x.yml",
        "docs/pyproject.toml.md",
        "apps/sandbox-executor/src/sandbox_executor/entrypoints.py",
        "LICENSE",
        "scripts/macos_relevance.md",
    ],
)
def test_neutral_paths(path: str) -> None:
    assert path_reason(path) is None


@pytest.mark.parametrize(
    "line", ["#!/bin/sh", "#!/bin/bash -e", "#!/usr/bin/env bash", "#!/usr/bin/env zsh", "#! /bin/sh"]
)
def test_shell_shebangs_are_sensitive(line: str) -> None:
    assert shebang_reason(line) == "shell-shebang"


@pytest.mark.parametrize("line", ["#!/usr/bin/env python3", "# bash comment", "echo sh", ""])
def test_other_first_lines_are_not_shell_shebangs(line: str) -> None:
    assert shebang_reason(line) is None


SENSITIVE_LINES = [
    ("import subprocess", "process"),
    ("from subprocess import run", "process"),
    ("import os, subprocess", "process"),
    ("result = subprocess.run(args)", "process"),
    ("os.system('ls')", "process"),
    ("os.execvp(cmd, args)", "process"),
    ("os.kill(pid, 9)", "process"),
    ("os.waitpid(pid, 0)", "process"),
    ("child = pexpect.spawn(cmd)", "process"),
    ("await asyncio.create_subprocess_exec(*args)", "process"),
    ("import multiprocessing", "process"),
    ("from multiprocessing import Pool", "process"),
    ("ctx = multiprocessing.get_context('spawn')", "process"),
    ("with ProcessPoolExecutor() as pool:", "process"),
    ("if sys.platform == 'darwin':", "platform-check"),
    ("import platform", "platform-check"),
    ("name = platform.system()", "platform-check"),
    ("if os.name == 'posix':", "platform-check"),
    ("os.uname()", "platform-check"),
    ("import signal", "posix-module"),
    ("import os, signal, sys", "posix-module"),
    ("import fcntl", "posix-module"),
    ("import termios", "posix-module"),
    ("import pty", "posix-module"),
    ("from resource import getrlimit", "posix-module"),
    ("import pwd", "posix-module"),
    ("signal.signal(signal.SIGTERM, handler)", "posix-module"),
    ("cpus = len(os.sched_getaffinity(0))", "posix-module"),
    ("sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)", "posix-module"),
    ('home = os.environ["HOME"]', "home-or-temp"),
    ('home = os.getenv("HOME")', "home-or-temp"),
    ('monkeypatch.setenv("HOME", str(tmp_path))', "home-or-temp"),
    ('monkeypatch.delenv("HOME")  # setenv', "home-or-temp"),
    ("path = os.path.expanduser('~')", "home-or-temp"),
    ("home = Path.home()", "home-or-temp"),
    ('env["TMPDIR"] = tmp', "home-or-temp"),
    ("import tempfile", "home-or-temp"),
    ("from tempfile import mkdtemp", "home-or-temp"),
    ("root = tempfile.gettempdir()", "home-or-temp"),
    ("real = os.path.realpath(path)", "filesystem"),
    ("os.path.islink(path)", "filesystem"),
    ("os.path.getmtime(path)", "filesystem"),
    ("if os.path.exists(path):", "filesystem"),
    ("os.path.isdir(path)", "filesystem"),
    ("size = os.path.getsize(path)", "filesystem"),
    ("os.makedirs(root, exist_ok=True)", "filesystem"),
    ("os.remove(path)", "filesystem"),
    ("os.unlink(path)", "filesystem"),
    ("os.chmod(path, 0o755)", "filesystem"),
    ("os.symlink(src, dst)", "filesystem"),
    ("os.replace(tmp, path)", "filesystem"),
    ("names = os.listdir(root)", "filesystem"),
    ("for root, dirs, files in os.walk(top):", "filesystem"),
    ("os.getcwd()", "filesystem"),
    ("from os import getcwd", "filesystem"),
    ("from os.path import realpath", "filesystem"),
    ("import shutil", "filesystem"),
    ("shutil.rmtree(path)", "filesystem"),
    ("root = Path(path).resolve()", "filesystem"),
    ("link.symlink_to(target)", "filesystem"),
    ("mode = path.stat().st_mode", "filesystem"),
    ("for child in root.iterdir():", "filesystem"),
    ("files = sorted(root.rglob('*.py'))", "filesystem"),
    ("tmp.rename(target)", "filesystem"),
    ("tmp.replace(target)", "filesystem"),
    ("Path(tmp).replace(root / name)", "filesystem"),
    ("tmp.replace(target_for(name))", "filesystem"),
    ("here = Path.cwd()", "filesystem"),
    ("path.parent.mkdir(parents=True, exist_ok=True)", "filesystem"),
    ("if path.exists() and path.is_file():", "filesystem"),
    ("root.is_dir()", "filesystem"),
    ("path.unlink(missing_ok=True)", "filesystem"),
    ("root.rmdir()", "filesystem"),
    ("marker.touch()", "filesystem"),
    ("from sandbox_executor.claude_keychain import read", "keychain-or-launcher"),
    ("KEYCHAIN_SERVICE = 'x'", "keychain-or-launcher"),
    ("cmd = ['security', 'find-generic-password', '-s', name]", "keychain-or-launcher"),
    ("run('security find-generic-password -s x')", "keychain-or-launcher"),
    ("from sandbox_executor.launcher import main", "keychain-or-launcher"),
    ("from .launcher import main", "keychain-or-launcher"),
]


@pytest.mark.parametrize(("line", "reason"), SENSITIVE_LINES)
def test_sensitive_content_lines(line: str, reason: str) -> None:
    found = content_reason(f"x = 1\n{line}\n")

    assert found is not None
    assert found.startswith(f"{reason} `")


@pytest.mark.parametrize(
    "line",
    [
        "path = os.path.join(root, name)",
        "base = os.path.basename(path)",
        "parent = os.path.dirname(path)",
        "stem, ext = os.path.splitext(name)",
        "target = root / 'plans' / name",
        "text = Path(path).read_text(encoding='utf-8')",
        "path.write_text(text, encoding='utf-8')",
        "data = path.read_bytes()",
        "path.write_bytes(data)",
        "from concurrent.futures import ThreadPoolExecutor",
        "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        "with open(path, encoding='utf-8') as handle:",
        "name, suffix = path.name, path.suffix",
        "pure = PurePosixPath(path)",
        "from pathlib import Path",
        "import os",
        "value = os.environ.get('HOLON_MODE', '')",
        "# if sys.platform == 'darwin':",
        "x = 1  # subprocess.run is not called here",
        "import json, re",
        "signals = load_signals()",
        "from platformdirs import user_cache_dir",
        "home_dir = settings.home",
        "text = text.replace('a', 'b')",
        "stamp = stamp.replace(tzinfo=UTC)",
        "steps = dataclasses.replace(stage5_steps(), calibrate=fake)",
        "text = text.replace(old(), new)",
        "number = match.group(1)",
        "# os.environ['HOME'] is not read here",
    ],
)
def test_neutral_content_lines(line: str) -> None:
    assert content_reason(f"x = 1\n{line}\n") is None


def test_every_content_reason_has_a_sensitive_case() -> None:
    assert {reason for reason, _ in CONTENT_PATTERNS} == {reason for _, reason in SENSITIVE_LINES}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("x = 1  # comment", "x = 1  "),
        ("x = '#not a comment'", "x = '#not a comment'"),
        ('x = "a\\"#b"  # c', 'x = "a\\"#b"  '),
        ("# whole line", ""),
    ],
)
def test_strip_comment(line: str, expected: str) -> None:
    assert strip_comment(line) == expected


def test_a_hash_inside_a_string_keeps_the_code_after_it() -> None:
    assert content_reason("label = '#'; subprocess.run(cmd)\n") is not None


def test_unreadable_content_is_sensitive() -> None:
    assert blob_reason("a.py", None) == UNREADABLE_CONTENT


def test_a_shebang_counts_for_a_file_without_extension_but_patterns_only_for_python() -> None:
    assert blob_reason("tools/run", "#!/bin/bash\necho hi\n") == "shell-shebang"
    assert blob_reason("docs/notes.txt", "import subprocess\n") is None
    assert blob_reason("tools/run.py", "import subprocess\n") == "process `import subprocess`"


class Reader:
    """A ``read_blob`` that serves texts from a dictionary and records every read."""

    def __init__(self, blobs: dict[tuple[str, str], str | None]) -> None:
        self.blobs = blobs
        self.calls: list[tuple[str, str]] = []

    def __call__(self, side: str, path: str) -> str | None:
        self.calls.append((side, path))
        return self.blobs[(side, path)]


def test_neutral_python_change_is_neutral() -> None:
    change = ChangedFile("M", "pkg/a.py")
    read = Reader({("new", "pkg/a.py"): "x = 1\n", ("old", "pkg/a.py"): "x = 0\n"})

    assert judge_change(change, read) == Verdict(change, False)


def test_adding_a_subprocess_use_is_sensitive_on_the_new_side() -> None:
    change = ChangedFile("M", "pkg/a.py")
    read = Reader({("new", "pkg/a.py"): "import subprocess\n", ("old", "pkg/a.py"): "x = 0\n"})

    assert judge_change(change, read) == Verdict(change, True, BY_CONTENT, "new", "process `import subprocess`")


def test_removing_a_platform_check_is_sensitive_on_the_old_side() -> None:
    change = ChangedFile("M", "pkg/a.py")
    read = Reader({("new", "pkg/a.py"): "x = 1\n", ("old", "pkg/a.py"): "if sys.platform == 'darwin':\n"})

    assert judge_change(change, read) == Verdict(change, True, BY_CONTENT, "old", "platform-check `sys.platform`")


def test_a_deleted_sensitive_file_is_judged_by_its_old_path_without_reading() -> None:
    change = ChangedFile("D", "tools/build.sh")
    read = Reader({})

    assert judge_change(change, read) == Verdict(change, True, BY_PATH, "old", "shell-or-container")
    assert read.calls == []


def test_a_renamed_sensitive_file_with_a_neutral_new_name_is_judged_by_its_old_path() -> None:
    change = ChangedFile("R", "docs/build.txt", "tools/build.sh")

    assert judge_change(change, Reader({})) == Verdict(change, True, BY_PATH, "old", "shell-or-container")


def test_an_added_file_reads_only_its_new_side() -> None:
    change = ChangedFile("A", "pkg/new.py")
    read = Reader({("new", "pkg/new.py"): "x = 1\n"})

    assert judge_change(change, read) == Verdict(change, False)
    assert read.calls == [("new", "pkg/new.py")]


def test_an_unreadable_side_is_sensitive() -> None:
    change = ChangedFile("M", "pkg/a.py")
    read = Reader({("new", "pkg/a.py"): None, ("old", "pkg/a.py"): "x = 0\n"})

    assert judge_change(change, read) == Verdict(change, True, BY_CONTENT, "new", UNREADABLE_CONTENT)


def test_non_python_content_is_not_read() -> None:
    change = ChangedFile("M", "docs/a.md")
    read = Reader({})

    assert judge_change(change, read) == Verdict(change, False)
    assert read.calls == []
