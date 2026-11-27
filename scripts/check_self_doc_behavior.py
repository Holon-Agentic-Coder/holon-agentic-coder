#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

_UNREADABLE_EXIT_CODE = 2
_SELF_DOC_LABEL_TOKENS = ("self-doc", "self_doc", "self-explaining")
_SELF_DOC_TITLE_RE = re.compile(r"self[-_ ](?:doc|explaining)", re.IGNORECASE)
_SELF_DOC_TEST_FILE_RE = re.compile(r"^test_self_doc_.*\.py$")


class DiffUnreadable(Exception):
    pass


@dataclass(frozen=True)
class ComparisonResult:
    equivalent: bool
    reason: str = ""


@dataclass(frozen=True)
class ChangedEntry:
    status: str
    old_path: str
    new_path: str


def is_docstring_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


_DocstringHost = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


class _DocstringStripper(ast.NodeTransformer):
    def _strip_leading_docstring(self, node: _DocstringHost) -> ast.AST:
        self.generic_visit(node)
        if node.body and is_docstring_statement(node.body[0]):
            node.body = node.body[1:]
        if not isinstance(node, ast.Module) and not node.body:
            node.body = [ast.Pass()]
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        return self._strip_leading_docstring(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._strip_leading_docstring(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._strip_leading_docstring(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._strip_leading_docstring(node)


def strip_docstrings(tree: ast.AST) -> ast.AST:
    return _DocstringStripper().visit(tree)


def normalised_ast_dump(source: str) -> str:
    tree = strip_docstrings(ast.parse(source))
    return ast.dump(tree, include_attributes=False)


def compare_python_sources(before: str, after: str) -> ComparisonResult:
    try:
        before_dump = normalised_ast_dump(before)
    except SyntaxError as exc:
        return ComparisonResult(False, f"before syntax error: {exc.msg} (line {exc.lineno})")
    try:
        after_dump = normalised_ast_dump(after)
    except SyntaxError as exc:
        return ComparisonResult(False, f"after syntax error: {exc.msg} (line {exc.lineno})")
    if before_dump == after_dump:
        return ComparisonResult(True)
    return ComparisonResult(False, "AST changed outside docstrings and comments")


def has_self_doc_label(labels: Sequence[str]) -> bool:
    lowered = [label.lower() for label in labels]
    return any(token in label for label in lowered for token in _SELF_DOC_LABEL_TOKENS)


def has_self_doc_title(title: str) -> bool:
    return bool(_SELF_DOC_TITLE_RE.search(title))


def is_self_doc_test_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return name == "self_doc_scanner.py" or bool(_SELF_DOC_TEST_FILE_RE.match(name))


def _is_modified_python_entry(entry: ChangedEntry) -> bool:
    is_mod_or_rename = entry.status == "M" or entry.status.startswith("R")
    return is_mod_or_rename and entry.old_path.endswith(".py") and entry.new_path.endswith(".py")


def is_diff_only_self_doc(changed_entries: Sequence[ChangedEntry], read_blob: Callable[[str, str], str]) -> bool:
    if not changed_entries:
        return False
    saw_relevant = False
    for entry in changed_entries:
        if is_self_doc_test_path(entry.new_path) or is_self_doc_test_path(entry.old_path):
            saw_relevant = True
            continue
        if not _is_modified_python_entry(entry):
            return False
        comparison = compare_python_sources(read_blob("base", entry.old_path), read_blob("head", entry.new_path))
        if not comparison.equivalent:
            return False
        saw_relevant = True
    return saw_relevant


def detect_trigger_reason(
    labels: Sequence[str],
    title: str,
    changed_entries: Sequence[ChangedEntry],
    read_blob: Callable[[str, str], str],
) -> str | None:
    if has_self_doc_label(labels):
        return "label"
    if has_self_doc_title(title):
        return "title"
    if is_diff_only_self_doc(changed_entries, read_blob):
        return "diff-only"
    return None


def _run_git(args: Sequence[str], repo: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, errors="replace", check=False)
    except OSError as exc:
        raise DiffUnreadable(str(exc)) from exc
    if result.returncode != 0:
        raise DiffUnreadable(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _git_merge_base(base: str, head: str, repo: str) -> str:
    if not base.strip() or not head.strip():
        raise DiffUnreadable("base or head revision is empty")
    return _run_git(["merge-base", base, head], repo).strip()


def _parse_name_status_z(output: str) -> list[ChangedEntry]:
    tokens = [token for token in output.split("\0") if token]
    entries: list[ChangedEntry] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                raise DiffUnreadable(f"truncated git name-status output for {status}")
            entries.append(ChangedEntry(status=status, old_path=tokens[index], new_path=tokens[index + 1]))
            index += 2
        else:
            if index >= len(tokens):
                raise DiffUnreadable(f"truncated git name-status output for {status}")
            path = tokens[index]
            entries.append(ChangedEntry(status=status, old_path=path, new_path=path))
            index += 1
    return entries


def git_changed_entries(base: str, head: str, repo: str) -> list[ChangedEntry]:
    effective_base = _git_merge_base(base, head, repo)
    output = _run_git(["diff", "--name-status", "-z", effective_base, head], repo)
    return _parse_name_status_z(output)


def git_read_blob(rev: str, path: str, repo: str) -> str:
    return _run_git(["show", f"{rev}:{path}"], repo)


def _check_entries(entries: Sequence[ChangedEntry], effective_base: str, head: str, repo: str) -> list[str]:
    failures: list[str] = []
    for entry in entries:
        if not _is_modified_python_entry(entry):
            continue
        before = git_read_blob(effective_base, entry.old_path, repo)
        after = git_read_blob(head, entry.new_path, repo)
        comparison = compare_python_sources(before, after)
        if not comparison.equivalent:
            failures.append(f"{entry.new_path}: {comparison.reason}")
    return failures


def check_revision_range(base: str, head: str, repo: str) -> list[str]:
    effective_base = _git_merge_base(base, head, repo)
    entries = _parse_name_status_z(_run_git(["diff", "--name-status", "-z", effective_base, head], repo))
    return _check_entries(entries, effective_base, head, repo)


def _parse_labels_json(raw: str) -> list[str]:
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiffUnreadable(f"invalid PR labels JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise DiffUnreadable("PR labels JSON must be a list")
    labels: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            labels.append(item["name"])
    return labels


def _resolve_pr_metadata(args: argparse.Namespace) -> tuple[str, list[str]]:
    title = args.pr_title if args.pr_title is not None else os.environ.get(args.pr_title_env, "")
    raw_labels = args.pr_labels_json if args.pr_labels_json is not None else os.environ.get(args.pr_labels_env, "")
    return title, _parse_labels_json(raw_labels)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify self-doc Python changes preserve normalised ASTs.")
    parser.add_argument("--base", required=True, help="Base revision of the pull request")
    parser.add_argument("--head", required=True, help="Head revision of the pull request")
    parser.add_argument("--repo", default=".", help="Repository to inspect")
    parser.add_argument("--pr-title", default=None, help="Pull request title")
    parser.add_argument("--pr-title-env", default="", help="Environment variable holding the pull request title")
    parser.add_argument("--pr-labels-json", default=None, help="JSON array of pull request labels")
    parser.add_argument("--pr-labels-env", default="", help="Environment variable holding pull request labels JSON")
    parser.add_argument(
        "--only-when-triggered",
        action="store_true",
        help="Skip with exit code 0 unless PR metadata or diff triggers the self-doc gate",
    )
    return parser.parse_args(argv)


def _run_gate(args: argparse.Namespace) -> int:
    effective_base = _git_merge_base(args.base, args.head, args.repo)
    entries = _parse_name_status_z(_run_git(["diff", "--name-status", "-z", effective_base, args.head], args.repo))
    if args.only_when_triggered:
        title, labels = _resolve_pr_metadata(args)

        def _read_blob(side: str, path: str) -> str:
            return git_read_blob(effective_base if side == "base" else args.head, path, args.repo)

        reason = detect_trigger_reason(labels, title, entries, _read_blob)
        if reason is None:
            print("Self-doc AST behaviour check skipped (not triggered).")
            return 0
        print(f"Self-doc AST behaviour check triggered by: {reason}")
    failures = _check_entries(entries, effective_base, args.head, args.repo)
    for failure in failures:
        print(failure)
    if failures:
        print(f"{len(failures)} file(s) failed self-doc AST behaviour check.")
        return 1
    print("Self-doc AST behaviour check passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run_gate(args)
    except DiffUnreadable as exc:
        print(f"cannot read the pull request diff: {exc}")
        return _UNREADABLE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
