from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

SHELL_TOKEN = re.compile(
    r"\"(?:\\.|[^\"\\])*\"?|'[^']*'?|\\."
    r"|\$?\(\((?:[^()]|\([^()]*\))*\)\)"
    r"|(?<!<)<<(?!<)-?\s*(?P<quote>['\"]?)(?P<heredoc>[A-Za-z_][\w.-]*)(?P=quote)"
    r"|(?:^|(?<=\s))#.*"
)
YAML_TOKEN = re.compile(r"(?:^|(?<=[\s\[{,:]))(?:\"(?:\\.|[^\"\\])*\"?|'(?:''|[^'])*'?)|(?:^|(?<=\s))#.*")
YAML_BLOCK_SCALAR_HEADER = re.compile(r"(?:^|[\s:])(?:(?P<key>[\w.-]+):\s+)?[|>][+-]?[1-9]?[+-]?\s*$")
YAML_SHELL_KEYS = frozenset({"run"})
TOML_TOKEN = re.compile(
    r"\"\"\"(?:\\.|[^\\])*?(?:\"\"\"|\Z)|'''.*?(?:'''|\Z)|\"(?:\\.|[^\"\\\n])*\"?|'[^'\n]*'?|#[^\n]*",
    re.DOTALL,
)

NumberedLine = tuple[int, str]


def numbered_lines(text: str) -> list[NumberedLine]:
    return list(enumerate(text.splitlines(), start=1))


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip())


def shell_comments(lines: Iterable[NumberedLine], *, heredocs: bool = True) -> Iterator[NumberedLine]:
    pending_heredocs: list[str] = []
    for number, line in lines:
        if pending_heredocs:
            if line.strip() == pending_heredocs[0]:
                pending_heredocs.pop(0)
            continue
        for match in SHELL_TOKEN.finditer(line):
            if match.group("heredoc") and heredocs:
                pending_heredocs.append(match.group("heredoc"))
            elif match.group().startswith("#"):
                yield number, match.group()


def yaml_comments(lines: list[NumberedLine]) -> Iterator[NumberedLine]:
    index = 0
    while index < len(lines):
        number, line = lines[index]
        index += 1
        comments = [match for match in YAML_TOKEN.finditer(line) if match.group().startswith("#")]
        yield from ((number, match.group()) for match in comments)
        header = YAML_BLOCK_SCALAR_HEADER.search(line[: comments[0].start()] if comments else line)
        if header is None:
            continue
        block_start = index
        while index < len(lines) and (not lines[index][1].strip() or indentation(lines[index][1]) > indentation(line)):
            index += 1
        if header.group("key") in YAML_SHELL_KEYS:
            yield from shell_comments(lines[block_start:index])


def toml_comments(text: str) -> Iterator[NumberedLine]:
    for match in TOML_TOKEN.finditer(text):
        if match.group().startswith("#"):
            yield text.count("\n", 0, match.start()) + 1, match.group()
