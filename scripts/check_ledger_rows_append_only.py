from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.check_ledger_stores import LEDGER_DIR, REPO_ROOT, canonical_path, classify

MAX_PROBLEMS_PER_FILE = 20


class UnreadableBase(Exception):
    pass


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False)
    if done.returncode != 0:
        stderr = done.stderr.decode("utf-8", "replace").strip()
        raise UnreadableBase(f"git {' '.join(args)} failed ({done.returncode}): {stderr}")
    return done.stdout


def merge_base(repo: Path, base: str, head: str) -> str:
    # why: rows that landed on the base branch after the branch point are not this pull request's edits.
    return _git(repo, "merge-base", base, head).decode("ascii").strip()


def ledger_paths_at(repo: Path, commit: str) -> list[str]:
    listing = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit, "--", LEDGER_DIR).decode("utf-8")
    return [path for path in listing.split("\0") if _is_ledger_file(path)]


def _is_ledger_file(path: str) -> bool:
    hit = classify(path)
    return hit is not None and hit[0] in ("canonical", "fragment")


def read_blobs(repo: Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    request = "".join(f"{commit}:{path}\n" for path in paths).encode("utf-8")
    output = _git(repo, "cat-file", "--batch", stdin=request) if paths else b""
    blobs: dict[str, bytes] = {}
    offset = 0
    for path in paths:
        header_end = output.index(b"\n", offset)
        header = output[offset:header_end].split()
        if header[-1] == b"missing" or header[1] != b"blob":
            raise UnreadableBase(f"cannot read {path} at {commit}")
        size = int(header[2])
        blobs[path] = output[header_end + 1 : header_end + 1 + size]
        offset = header_end + 2 + size
    return blobs


def rows(data: bytes) -> list[bytes]:
    # why: promotion adds a newline to an unterminated last line before appending, which is not an edit of that row.
    lines = data.split(b"\n")
    return lines[:-1] if lines[-1] == b"" else lines


def compare_rows(path: str, base_rows: list[bytes], head_rows: list[bytes]) -> list[str]:
    head_set = set(head_rows)
    problems: list[str] = []
    for index, row in enumerate(base_rows):
        if index >= len(head_rows):
            problems.append(f"{path}:{index + 1}: deleted")
        elif head_rows[index] != row:
            problems.append(f"{path}:{index + 1}: {'moved' if row in head_set else 'changed'}")
    if len(problems) > MAX_PROBLEMS_PER_FILE:
        hidden = len(problems) - MAX_PROBLEMS_PER_FILE
        problems = [*problems[:MAX_PROBLEMS_PER_FILE], f"{path}: ... and {hidden} more"]
    return problems


def promoted(fragment_rows: list[bytes], head_canonical_rows: list[bytes]) -> bool:
    # why: `holon ledger promote` appends fragment rows verbatim to the canonical ledger and then deletes the fragment.
    canonical = set(head_canonical_rows)
    return all(row in canonical for row in fragment_rows if row.strip())


def _removed_file_problem(path: str, base_data: bytes, head: dict[str, bytes]) -> str | None:
    role, kind = classify(path) or ("", "")
    if role == "fragment" and promoted(rows(base_data), rows(head.get(canonical_path(kind), b""))):
        return None
    return f"{path}: {'fragment removed' if role == 'fragment' else 'deleted'}"


def check_append_only(repo: Path, base: str, head: str) -> tuple[list[str], int]:
    start = merge_base(repo, base, head)
    base_paths = ledger_paths_at(repo, start)
    base_blobs = read_blobs(repo, start, base_paths)
    head_blobs = read_blobs(repo, head, ledger_paths_at(repo, head))
    problems: list[str] = []
    for path in base_paths:
        if path not in head_blobs:
            removed = _removed_file_problem(path, base_blobs[path], head_blobs)
            problems += [removed] if removed else []
            continue
        problems += compare_rows(path, rows(base_blobs[path]), rows(head_blobs[path]))
    return problems, len(base_paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when a ledger row of the base changed on the head.")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        problems, checked = check_append_only(args.repo, args.base, args.head)
    except (UnreadableBase, OSError) as error:
        print(f"cannot read the base ledgers: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"ledger rows append-only: {checked} files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
