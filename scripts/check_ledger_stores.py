from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

LEDGER_DIR = "holon-knowledge/ledger"
LEDGER_KINDS = ("executions", "intents", "plans")
PENDING_DIR = f"{LEDGER_DIR}/pending"
EXCLUDED_DIRS = ("holon-knowledge/kb/failure_modes/", "holon-knowledge/wisdom/")
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class LedgerStores:
    kinds: frozenset[str]
    foreign: tuple[str, ...]


def canonical_path(kind: str) -> str:
    return f"{LEDGER_DIR}/{kind}.jsonl"


def fragment_dir(kind: str) -> str:
    return f"{PENDING_DIR}/{kind}"


def list_repository_files(repo: Path) -> list[str]:
    # why: untracked-not-ignored files count, so a new store fails before its commit; ignored trees never do.
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout
    return [path for path in listing.decode("utf-8").split("\0") if path]


def classify(path: str) -> tuple[str, str] | None:
    for kind in LEDGER_KINDS:
        if path == canonical_path(kind):
            return ("canonical", kind)
    if path.startswith(f"{PENDING_DIR}/"):
        kind, _, name = path[len(PENDING_DIR) + 1 :].partition("/")
        if kind in LEDGER_KINDS and name.endswith(".jsonl") and "/" not in name:
            return ("fragment", kind)
        return ("foreign", path)
    if path.endswith(".jsonl") and not path.startswith(EXCLUDED_DIRS):
        return ("foreign", path)
    return None


def find_ledger_stores(paths: Iterable[str]) -> LedgerStores:
    kinds: set[str] = set()
    foreign: list[str] = []
    for path in paths:
        hit = classify(path)
        if hit is None:
            continue
        role, value = hit
        if role == "canonical":
            kinds.add(value)
        elif role == "foreign":
            foreign.append(value)
    return LedgerStores(frozenset(kinds), tuple(sorted(foreign)))


def store_problems(stores: LedgerStores) -> list[str]:
    problems = [f"fourth ledger-like store: {path}" for path in stores.foreign]
    problems += [f"missing ledger: {canonical_path(kind)}" for kind in LEDGER_KINDS if kind not in stores.kinds]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail unless the repository holds exactly the three ledgers.")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        paths = list_repository_files(args.repo)
    except (subprocess.CalledProcessError, OSError) as error:
        print(f"cannot list the files of {args.repo}: {error}", file=sys.stderr)
        return 2
    stores = find_ledger_stores(paths)
    print(f"ledger kinds: {', '.join(sorted(stores.kinds)) or 'none'}")
    problems = store_problems(stores)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
