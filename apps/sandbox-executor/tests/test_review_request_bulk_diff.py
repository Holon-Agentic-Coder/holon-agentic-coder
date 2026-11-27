from __future__ import annotations

from pathlib import Path

import pytest
from sandbox_executor.flow import FlowContext
from sandbox_executor.flow.review.delta_review_git import DeltaRepo
from sandbox_executor.flow.review.delta_review_mode import SCOPED, ReviewMode
from sandbox_executor.flow.review.delta_review_request import build_delta_request
from sandbox_executor.git import EMPTY_DIFF_EXCLUDES, run_git
from sandbox_executor.review_request import (
    INLINED_DIFF_MAX_BYTES,
    SUMMARY_SAMPLE_SIZE,
    bound_review_diff,
    build_scoped_diff,
)
from sandbox_executor.review_target import ReviewTarget

from tests.flow.helpers import _git

APP = "src/app.py"
HEADING = "# Review diff summary (not inlined)"
RULE = "judge a bulk deletion by its deleted-file count and sample"


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _commit_all_and_return_head(repo: Path, message: str = "change") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _bulk_files(directory: str, count: int) -> dict[str, str]:
    return {f"{directory}/file_{n:04d}.txt": f"bulk-content-{directory}-{n}\n" for n in range(count)}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _write(work, {APP: "".join(f"line {n}\n" for n in range(10))})
    _commit_all_and_return_head(work, "base")
    return work


def _modify_app(repo: Path) -> None:
    _write(repo, {APP: "".join(f"line {n}\n" for n in range(10)).replace("line 3\n", "line 3 changed\n")})


def _delete(repo: Path, paths: list[str]) -> None:
    for rel in paths:
        (repo / rel).unlink()


def _inlined_part(diff_text: str) -> str:
    return diff_text.split(HEADING, 1)[0]


def _raw_scoped_diff(repo: Path, base: str, head: str) -> str:
    excludes = [f":(exclude){prefix}" for prefix in EMPTY_DIFF_EXCLUDES]
    return run_git(["diff", f"{base}...{head}", "--", ".", *excludes], cwd=str(repo), check=True).stdout


def test_bulk_deletion_is_summarised_and_the_modified_file_inlined(repo):
    bulk = _bulk_files("vendor/bulk", 2000)
    _write(repo, bulk)
    base = _commit_all_and_return_head(repo, "vendor")
    _delete(repo, list(bulk))
    _modify_app(repo)
    head = _commit_all_and_return_head(repo, "drop vendor")

    diff_text, _scoped = build_scoped_diff(str(repo), base, head)

    assert "-line 3\n+line 3 changed\n" in diff_text and "diff --git a/src/app.py b/src/app.py" in diff_text
    assert "bulk-content-" not in diff_text
    assert RULE in diff_text
    [row] = [line for line in diff_text.splitlines() if "vendor/bulk/:" in line]
    assert "2000 deleted files" in row
    samples = row.split("sample: ", 1)[1].split(", ")
    assert len(samples) == SUMMARY_SAMPLE_SIZE and all(path.startswith("vendor/bulk/") for path in samples)
    assert len(diff_text.encode("utf-8")) < 10_000


def test_small_deletions_new_and_modified_files_are_inlined(repo):
    small = _bulk_files("lib/small", 24)
    _write(repo, small)
    base = _commit_all_and_return_head(repo, "small")
    _delete(repo, list(small))
    _modify_app(repo)
    _write(repo, {"src/new.py": "new-file-marker\n"})
    head = _commit_all_and_return_head(repo, "drop small")

    diff_text, _scoped = build_scoped_diff(str(repo), base, head)

    assert all(f"-{content}" in diff_text for content in small.values())
    assert "+new-file-marker\n" in diff_text and "+line 3 changed\n" in diff_text
    assert HEADING not in diff_text
    assert diff_text == _raw_scoped_diff(repo, base, head)


def test_generated_files_are_summarised_even_when_small(repo):
    generated = ["uv.lock", "web/package-lock.json", "web/app.min.js", "web/app.js.map"]
    _write(repo, {path: f"generated-marker-{n}\n" for n, path in enumerate(generated)})
    _modify_app(repo)
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit_all_and_return_head(repo, "generated")

    diff_text, _scoped = build_scoped_diff(str(repo), base, head)

    assert "generated-marker" not in diff_text and "+line 3 changed\n" in diff_text
    assert RULE in diff_text
    rows = [line for line in diff_text.splitlines() if "generated files" in line]
    assert any("./: 1 generated files; sample: uv.lock" in row for row in rows)
    web_row = next(row for row in rows if "web/:" in row)
    assert "3 generated files" in web_row and "web/package-lock.json" in web_row


def _big_file(name: str, size: int) -> str:
    lines = [f"{name}-first\n", *(f"{name}-{n:08d}-" + "x" * 80 + "\n" for n in range(size // 100)), f"{name}-last\n"]
    return "".join(lines)


def test_byte_cap_leaves_whole_sections_out_and_lists_them_by_directory(repo):
    big = {f"src/big/f{n}.py": _big_file(f"big{n}", 120_000) for n in range(5)}
    _write(repo, {**big, "zz/small.py": "small-marker\n"})
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit_all_and_return_head(repo, "big")

    diff_text, _scoped = build_scoped_diff(str(repo), base, head)

    inlined = _inlined_part(diff_text)
    assert len(inlined.encode("utf-8")) <= INLINED_DIFF_MAX_BYTES
    shown = [n for n in range(5) if f"+big{n}-first" in inlined]
    assert shown == [0, 1, 2]
    assert all(f"+big{n}-last" in inlined for n in shown)
    assert all(f"big{n}-" not in inlined for n in (3, 4))
    later_section_that_fits = "+small-marker"
    assert later_section_that_fits in inlined
    assert "src/big/: 2 files not shown" in diff_text
    assert "capped at 400000 bytes" in diff_text


def _synthetic_section(path: str, body_bytes: int) -> str:
    header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n"
    return header + "+" + "y" * (body_bytes - len(header) - 2) + "\n"


def test_a_section_that_exactly_reaches_the_cap_is_inlined():
    exact = _synthetic_section("a.py", INLINED_DIFF_MAX_BYTES)
    assert len(exact.encode("utf-8")) == INLINED_DIFF_MAX_BYTES
    assert bound_review_diff(exact) is exact

    over = exact + _synthetic_section("d/b.py", 100)
    bounded = bound_review_diff(over)
    assert bounded.startswith(exact) and "d/: 1 files not shown" in bounded and "+++ b/d/b.py" not in bounded


def test_unbounded_diff_is_byte_identical(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _modify_app(repo)
    _write(repo, {"docs/name with space.md": "spaced\n"})
    head = _commit_all_and_return_head(repo, "small change")

    diff_text, _scoped = build_scoped_diff(str(repo), base, head)
    raw = _raw_scoped_diff(repo, base, head)

    assert diff_text == raw
    assert bound_review_diff(raw) is raw
    assert bound_review_diff("") == ""


def _delta_request(repo: Path, base: str, head: str, paths: tuple[str, ...]):
    context = FlowContext(repo_dir=str(repo))
    decision = ReviewMode(SCOPED, "test delta", head, base, paths)
    target = ReviewTarget(raw="main", head_branch="main", head_sha=head)
    return build_delta_request(context, target, decision, 2, ("Title", "body"))


def test_delta_request_returns_a_small_diff_unchanged(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _modify_app(repo)
    head = _commit_all_and_return_head(repo, "fix")

    request = _delta_request(repo, base, head, (APP,))

    raw = run_git(["diff", "--no-renames", base, head], cwd=str(repo), check=True).stdout
    assert request.diff_text == raw == DeltaRepo(str(repo)).diff(base, head, (APP,))


def test_delta_request_summarises_a_bulk_deletion(repo):
    bulk = _bulk_files("vendor/bulk", 30)
    _write(repo, bulk)
    base = _commit_all_and_return_head(repo, "vendor")
    _delete(repo, list(bulk))
    _modify_app(repo)
    head = _commit_all_and_return_head(repo, "drop vendor")

    request = _delta_request(repo, base, head, (APP, "vendor/bulk"))

    raw = DeltaRepo(str(repo)).diff(base, head, (APP, "vendor/bulk"))
    assert request.diff_text == bound_review_diff(raw) != raw
    assert "bulk-content-" not in request.diff_text and "+line 3 changed\n" in request.diff_text
    assert "vendor/bulk/: 30 deleted files" in request.diff_text
