#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Iterable, Sequence
from pathlib import PurePath
from typing import TextIO

EMPTY_SHARD_EXIT_CODE = 2


def normalised_test_path(path: str) -> str:
    posix_path = PurePath(path.replace("\\", "/")).as_posix()
    return posix_path.removeprefix("./")


def shard_of(path: str, shards: int) -> int:
    if shards < 1:
        raise ValueError(f"shards must be at least 1, got {shards}")
    # why: the built-in hash() is salted per process, so it cannot give every CI runner the same split.
    digest = hashlib.sha256(normalised_test_path(path).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shards + 1


def collected_test_files(lines: Iterable[str]) -> list[str]:
    test_files = set()
    for line in lines:
        test_file, separator, _ = line.strip().partition("::")
        if separator and test_file.endswith(".py"):
            test_files.add(normalised_test_path(test_file))
    return sorted(test_files)


def files_in_shard(files: Sequence[str], shard: int, shards: int) -> list[str]:
    if not 1 <= shard <= shards:
        raise ValueError(f"shard must be between 1 and {shards}, got {shard}")
    return [path for path in files if shard_of(path, shards) == shard]


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print the test files of one unit-suite shard.")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    arguments = parse_arguments(argv)
    test_files = collected_test_files(stdin)
    if not test_files:
        print("The pytest collection holds no test file.", file=sys.stderr)
        return EMPTY_SHARD_EXIT_CODE
    try:
        shard_files = files_in_shard(test_files, arguments.shard, arguments.shards)
    except ValueError as error:
        print(error, file=sys.stderr)
        return EMPTY_SHARD_EXIT_CODE
    if not shard_files:
        print(f"Shard {arguments.shard} of {arguments.shards} holds no test file.", file=sys.stderr)
        return EMPTY_SHARD_EXIT_CODE
    stdout.writelines(f"{path}\n" for path in shard_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
