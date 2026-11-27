#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SELF_DOC_LABEL_TOKENS = ("self-doc", "self_doc", "self-explaining", "self_explaining")
SELF_DOC_TITLE_PATTERN = re.compile(r"\bself[-_ ]?(?:doc|explaining)\b", re.IGNORECASE)
SELF_DOC_TEST_PATTERN = re.compile(r"^test_.*self_(?:doc|explaining).*\.py$")


@dataclass(frozen=True)
class FileComparison:
    path: str
    equivalent: bool
    detail: str


@dataclass(frozen=True)
class ChangedFileEntry:
    status: str
    old_path: str
    new_path: str
    before_text: str | None
    after_text: str | None


@dataclass(frozen=True)
class TriggerDecision:
    should_run: bool
    reason: str


def is_docstring_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


class DocstringStripper(ast.NodeTransformer):
    def _strip_leading_docstring(
        self,
        node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        substitute_pass: bool,
    ) -> ast.AST:
        self.generic_visit(node)
        if node.body and is_docstring_statement(node.body[0]):
            node.body = node.body[1:]
            # why: removing the sole docstring from a class or function leaves an invalid empty body
            if substitute_pass and not node.body:
                node.body = [ast.Pass()]
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        return self._strip_leading_docstring(node, substitute_pass=False)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._strip_leading_docstring(node, substitute_pass=True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._strip_leading_docstring(node, substitute_pass=True)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._strip_leading_docstring(node, substitute_pass=True)


def normalise_python_source(source: str) -> str:
    tree = ast.parse(source)
    normalised = DocstringStripper().visit(tree)
    return ast.dump(normalised, include_attributes=False)


def compare_python_sources(path: str, before: str, after: str) -> FileComparison:
    try:
        before_dump = normalise_python_source(before)
    except SyntaxError as error:
        return FileComparison(path=path, equivalent=False, detail=f"syntax error in before source: {error}")
    try:
        after_dump = normalise_python_source(after)
    except SyntaxError as error:
        return FileComparison(path=path, equivalent=False, detail=f"syntax error in after source: {error}")
    if before_dump != after_dump:
        return FileComparison(path=path, equivalent=False, detail="AST changed beyond docstrings and comments")
    return FileComparison(path=path, equivalent=True, detail="equivalent")


def parse_labels_json(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    labels: list[str] = []
    for item in payload:
        if isinstance(item, str):
            labels.append(item.strip().lower())
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            labels.append(item["name"].strip().lower())
    return tuple(label for label in labels if label)


def matches_self_doc_label(labels: Iterable[str]) -> bool:
    for label in labels:
        normalised = label.strip().lower()
        if any(token in normalised for token in SELF_DOC_LABEL_TOKENS):
            return True
    return False


def matches_self_doc_title(title: str) -> bool:
    return bool(SELF_DOC_TITLE_PATTERN.search(title))


def is_self_doc_test_path(path: str) -> bool:
    return bool(SELF_DOC_TEST_PATTERN.match(Path(path).name))


def is_modified_python_entry(entry: ChangedFileEntry) -> bool:
    return entry.status[:1] in {"M", "R"} and entry.new_path.endswith(".py")


def is_comment_or_docstring_only_diff(entries: Sequence[ChangedFileEntry]) -> bool:
    if not entries:
        return False
    for entry in entries:
        if is_self_doc_test_path(entry.new_path):
            continue
        if not is_modified_python_entry(entry):
            return False
        if entry.before_text is None or entry.after_text is None:
            return False
        if not compare_python_sources(entry.new_path, entry.before_text, entry.after_text).equivalent:
            return False
    return True


def should_run_self_doc_gate(
    labels: Iterable[str],
    title: str,
    entries: Sequence[ChangedFileEntry],
) -> TriggerDecision:
    if matches_self_doc_label(labels):
        return TriggerDecision(should_run=True, reason="label")
    if matches_self_doc_title(title):
        return TriggerDecision(should_run=True, reason="title")
    if is_comment_or_docstring_only_diff(entries):
        return TriggerDecision(should_run=True, reason="diff-only")
    return TriggerDecision(should_run=False, reason="not-self-doc")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def read_blob_text(root: Path, revision: str, path: str) -> str:
    result = run_git(root, "show", f"{revision}:{path}")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git show {revision}:{path} failed: {message}")
    return result.stdout.decode("utf-8")


def parse_name_status_z(payload: bytes) -> list[tuple[str, str, str]]:
    tokens = [token.decode("utf-8") for token in payload.split(b"\x00") if token]
    records: list[tuple[str, str, str]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        if status[:1] in {"R", "C"} and index + 2 < len(tokens):
            records.append((status, tokens[index + 1], tokens[index + 2]))
            index += 3
        elif index + 1 < len(tokens):
            path = tokens[index + 1]
            records.append((status, path, path))
            index += 2
        else:
            break
    return records


def diff_entries(root: Path, base: str, head: str) -> list[ChangedFileEntry]:
    result = run_git(root, "-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", base, head)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git diff {base}..{head} failed: {message}")
    entries: list[ChangedFileEntry] = []
    for status, old_path, new_path in parse_name_status_z(result.stdout):
        before_text: str | None = None
        after_text: str | None = None
        if status[:1] in {"M", "R"} and new_path.endswith(".py"):
            before_text = read_blob_text(root, base, old_path)
            after_text = read_blob_text(root, head, new_path)
        entries.append(ChangedFileEntry(status, old_path, new_path, before_text, after_text))
    return entries


def check_changed_python_files(entries: Sequence[ChangedFileEntry]) -> list[FileComparison]:
    comparisons: list[FileComparison] = []
    for entry in entries:
        if not is_modified_python_entry(entry) or is_self_doc_test_path(entry.new_path):
            continue
        if entry.before_text is None or entry.after_text is None:
            comparisons.append(FileComparison(entry.new_path, False, "missing before or after source"))
            continue
        comparisons.append(compare_python_sources(entry.new_path, entry.before_text, entry.after_text))
    return comparisons


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify self-doc Python edits preserve normalised ASTs.")
    parser.add_argument("--base", help="Base git revision.")
    parser.add_argument("--head", help="Head git revision.")
    parser.add_argument("--range", dest="revision_range", help="Git revision range formatted as BASE..HEAD.")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="Repository root.")
    parser.add_argument("--auto-trigger", action="store_true", help="Run only when PR metadata or diff is self-doc.")
    parser.add_argument("--pr-title", default=os.environ.get("PR_TITLE", ""), help="Pull request title.")
    parser.add_argument(
        "--pr-labels-json",
        default=os.environ.get("PR_LABELS", ""),
        help="Pull request labels as JSON.",
    )
    parser.add_argument("--pr-label", action="append", default=[], help="Additional pull request label.")
    return parser.parse_args(argv)


def resolve_revisions(arguments: argparse.Namespace) -> tuple[str, str]:
    if arguments.revision_range:
        if arguments.base or arguments.head:
            raise ValueError("--range cannot be combined with --base or --head")
        parts = arguments.revision_range.split("..", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("--range must be formatted as BASE..HEAD")
        return parts[0], parts[1]
    if not arguments.base or not arguments.head:
        raise ValueError("provide either --range BASE..HEAD or both --base and --head")
    return arguments.base, arguments.head


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        base, head = resolve_revisions(arguments)
        entries = diff_entries(arguments.root, base, head)
    except (RuntimeError, ValueError) as error:
        print(f"self-doc behavior check error: {error}", file=sys.stderr)
        return 2
    if arguments.auto_trigger:
        labels = (*parse_labels_json(arguments.pr_labels_json), *arguments.pr_label)
        decision = should_run_self_doc_gate(labels, arguments.pr_title, entries)
        if not decision.should_run:
            print(f"Self-doc behavior check skipped ({decision.reason}).")
            return 0
    comparisons = check_changed_python_files(entries)
    failures = [comparison for comparison in comparisons if not comparison.equivalent]
    if failures:
        for failure in failures:
            print(f"{failure.path}: {failure.detail}", file=sys.stderr)
        summary = f"Self-doc behavior check failed ({len(failures)}/{len(comparisons)} file(s) changed AST)."
        print(summary, file=sys.stderr)
        return 1
    print(f"Self-doc behavior check passed ({len(comparisons)} Python file(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
