import json
import subprocess
from pathlib import Path

import pytest

from scripts.measure_deleted_why_comments import (
    UnmatchedReason,
    find_unmatched_reasons_in_diff,
    main,
    scan_self_doc_history,
)


def make_diff(path: str, hunk_header: str, body_lines: list[str]) -> str:
    header = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", hunk_header]
    return "\n".join(header + body_lines) + "\n"


@pytest.mark.parametrize(
    "comment",
    [
        "# retry because the socket closes early",
        "# sort keys so that hashes stay deterministic",
        "# clamp timeout otherwise the worker hangs",
        "# lock must be held before mutating state",
        "# avoid allocating inside the hot loop",
        "# never reuse this nonce across sessions",
        "# serialize writes to prevent a race condition",
        "# flush buffer to ensure durability",
        "# why: upstream API rejects empty arrays",
    ],
)
def test_removed_reason_indicator_without_why_is_flagged(comment):
    diff_text = make_diff(
        "app/worker.py",
        "@@ -10,3 +10,2 @@",
        [" def run():", f"-    {comment}", "     return 1"],
    )
    assert find_unmatched_reasons_in_diff(diff_text) == [
        UnmatchedReason(file_path="app/worker.py", line=11, text=comment)
    ]


def test_removed_reason_paired_with_same_hunk_why_passes():
    diff_text = make_diff(
        "app/worker.py",
        "@@ -4,3 +4,3 @@",
        [
            " def run():",
            "-    # retry because the socket closes early",
            "+    # why: the socket closes early on idle peers",
            "     return 1",
        ],
    )
    assert find_unmatched_reasons_in_diff(diff_text) == []


def test_removed_reason_paired_with_other_hunk_why_passes():
    diff_text = "\n".join(
        [
            "diff --git a/app/worker.py b/app/worker.py",
            "--- a/app/worker.py",
            "+++ b/app/worker.py",
            "@@ -4,3 +4,2 @@",
            " def run():",
            "-    # retry because the socket closes early",
            "     return 1",
            "@@ -20,2 +19,3 @@",
            " def helper():",
            "+    # why: the socket closes early on idle peers",
            "     return 2",
        ]
    )
    assert find_unmatched_reasons_in_diff(diff_text) == []


def test_same_hunk_why_consumes_one_to_one():
    diff_text = make_diff(
        "app/worker.py",
        "@@ -4,4 +4,3 @@",
        [
            " def run():",
            "-    # retry because the socket closes early",
            "-    # never drop pending frames",
            "+    # why: the socket closes early on idle peers",
            "     return 1",
        ],
    )
    assert find_unmatched_reasons_in_diff(diff_text) == [
        UnmatchedReason(file_path="app/worker.py", line=6, text="# never drop pending frames")
    ]


def test_cross_hunk_why_consumes_one_to_one():
    diff_text = "\n".join(
        [
            "diff --git a/app/worker.py b/app/worker.py",
            "--- a/app/worker.py",
            "+++ b/app/worker.py",
            "@@ -4,4 +4,2 @@",
            " def run():",
            "-    # retry because the socket closes early",
            "-    # never drop pending frames",
            "     return 1",
            "@@ -20,2 +18,3 @@",
            " def helper():",
            "+    # why: the socket closes early on idle peers",
            "     return 2",
        ]
    )
    assert find_unmatched_reasons_in_diff(diff_text) == [
        UnmatchedReason(file_path="app/worker.py", line=6, text="# never drop pending frames")
    ]


@pytest.mark.parametrize("comment", ["# increment counter", "# build payload", "# parse response"])
def test_ordinary_removed_comments_are_ignored(comment):
    diff_text = make_diff(
        "app/worker.py",
        "@@ -1,3 +1,2 @@",
        [" x = 1", f"-{comment}", " y = 2"],
    )
    assert find_unmatched_reasons_in_diff(diff_text) == []


@pytest.mark.parametrize(
    ("path", "removed_line"),
    [
        ("app/worker.py", 'msg = "# because this is inside double quotes"'),
        ("app/worker.py", "msg = '# never treat single quotes as comment'"),
        ("app/worker.py", 'msg = "escaped \\" # because still quoted"'),
        ("app/worker.py", "    holding a raw `/` or `#` is never read as the host"),
        ("app/worker.py", "x = 1  # noqa: E501 because long line"),
        ("app/worker.py", "x = 1  # type: ignore[assignment] because stub"),
        ("app/worker.py", "if cond:  # pragma: no cover because fallback"),
        ("app/worker.py", "#!/usr/bin/env python3"),
        ("app/worker.py", "# -*- coding: utf-8 -*-"),
        ("plans/P-1.md", "# must keep this heading"),
        ("executions/E-1.md", "# because markdown is not code"),
        ("holon-knowledge/notes.py", "# because knowledge files are excluded"),
        ("data/config.json", "# must not scan json"),
        ("app/widget.js", "this.#mustKeep = 1"),
    ],
)
def test_string_literals_directives_and_non_code_paths_are_ignored(path, removed_line):
    diff_text = make_diff(path, "@@ -1,2 +1 @@", [f"-{removed_line}", " pass"])
    assert find_unmatched_reasons_in_diff(diff_text) == []


def test_single_line_hunk_and_no_newline_marker_preserve_line_numbers():
    diff_text = "\n".join(
        [
            "diff --git a/app/worker.py b/app/worker.py",
            "--- a/app/worker.py",
            "+++ b/app/worker.py",
            "@@ -12 +12 @@",
            "-value = 1  # must stay positive",
            "\\ No newline at end of file",
            "+value = 1",
            "\\ No newline at end of file",
        ]
    )
    assert find_unmatched_reasons_in_diff(diff_text) == [
        UnmatchedReason(file_path="app/worker.py", line=12, text="# must stay positive")
    ]


def git_commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def build_sample_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "src" / "worker.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "def open_session():",
                "    # increment counter",
                "    count = 1",
                "    # avoid reusing stale handles",
                "    handle = open_handle()",
                "    return count, handle",
                "",
                "",
                "def keep_alive(handle):",
                "    ping(handle)",
                "    return True",
                "",
                "",
                "def close_session(handle):",
                "    # must flush before exit",
                "    flush(handle)",
                "    close(handle)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    other = tmp_path / "src" / "flags.py"
    other.write_text("# never parse empty args\nARGS = []\n", encoding="utf-8")
    git_commit(tmp_path, "chore: initial commit")

    target.write_text(
        "\n".join(
            [
                "def open_session():",
                "    count = 1",
                "    handle = open_handle()",
                "    return count, handle",
                "",
                "",
                "def keep_alive(handle):",
                "    ping(handle)",
                "    return True",
                "",
                "",
                "def close_session(handle):",
                "    # why: downstream readers expect flushed state before exit",
                "    flush(handle)",
                "    close(handle)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    self_doc_message = "\n".join(
        [
            "feat(self): clean worker comments (#101)",
            "",
            "* intent: created I-100-self-doc-sample/_",
        ]
    )
    git_commit(tmp_path, self_doc_message)

    other.write_text("ARGS = []\n", encoding="utf-8")
    non_self_doc_message = "\n".join(
        [
            "feat(cli): update flags (#102)",
            "",
            "* intent: created I-101-cli-flags/_",
        ]
    )
    git_commit(tmp_path, non_self_doc_message)
    return tmp_path


def test_scan_self_doc_history_and_cli_report_unmatched_reasons(tmp_path, capsys):
    repo = build_sample_repo(tmp_path)
    reports = scan_self_doc_history(repo, ref="main")
    assert len(reports) == 1
    assert reports[0].slice_info.pr_number == 101
    assert reports[0].slice_info.intent_id == "I-100-self-doc-sample"
    assert reports[0].unmatched == [
        UnmatchedReason(file_path="src/worker.py", line=4, text="# avoid reusing stale handles")
    ]

    assert main(["--repo", str(repo), "--ref", "main", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ref"] == "main"
    assert payload["slices_scanned"] == 1
    assert payload["unmatched_count"] == 1
    assert payload["slices"][0]["unmatched"] == [
        {"file": "src/worker.py", "line": 4, "text": "# avoid reusing stale handles"}
    ]

    assert main(["--repo", str(repo), "--ref", "main", "--format", "text"]) == 0
    text_output = capsys.readouterr().out
    assert "PR #101" in text_output
    assert "src/worker.py:4: # avoid reusing stale handles" in text_output
    assert "Scanned 1 self-doc slice(s); 1 unmatched removed reason(s)." in text_output


def test_scan_self_doc_history_handles_merge_commits(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "src" / "worker.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# avoid reusing stale handles\nVALUE = 1\n", encoding="utf-8")
    git_commit(tmp_path, "chore: initial commit")

    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=tmp_path, check=True, capture_output=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    git_commit(tmp_path, "refactor: drop comment")

    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True, capture_output=True)
    merge_message = "\n".join(
        [
            "Merge pull request #202 from org/self-doc (#202)",
            "",
            "* intent: created I-200-self-doc-merge/_",
        ]
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "merge",
            "--no-ff",
            "-q",
            "-m",
            merge_message,
            "feature",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    reports = scan_self_doc_history(tmp_path, ref="main")
    assert len(reports) == 1
    assert reports[0].slice_info.pr_number == 202
    assert reports[0].unmatched == [
        UnmatchedReason(file_path="src/worker.py", line=1, text="# avoid reusing stale handles")
    ]
