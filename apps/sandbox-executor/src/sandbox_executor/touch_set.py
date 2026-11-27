from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.git import is_metadata_path

_HEADING_RE = re.compile(r"^##\s+touch\s+set\s*$", re.IGNORECASE)
_NEXT_SECTION_RE = re.compile(r"^#{1,2}\s")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(?P<body>.*)$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SYMBOLS_RE = re.compile(r"symbols\s*:\s*(?P<names>.*)$", re.IGNORECASE)
_BRACE_RENAME_RE = re.compile(r"\{[^{}]* => ([^{}]*)\}")
_PLACEHOLDER_WORDS = frozenset({"none", "n/a", "na", "nil", "no", "not"})
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
_PLACEHOLDER_PUNCTUATION = ".,;:"
_GLOB_CHARS = ("*", "?", "[")
_GLOB_TOKEN_RE = re.compile(r"\*\*/|\*\*|\*|\?|\[[^\]/]+\]|[^*?\[]+|\[")
_GLOB_TOKENS = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}


@dataclass(frozen=True)
class TouchSetEntry:
    path: str
    symbols: tuple[str, ...] = ()

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {"path": self.path}
        if self.symbols:
            row["symbols"] = list(self.symbols)
        return row


@dataclass(frozen=True)
class TouchSet:
    entries: tuple[TouchSetEntry, ...] = ()

    def to_row(self) -> list[dict[str, Any]]:
        return [entry.to_row() for entry in self.entries]

    @classmethod
    def from_row(cls, value: Any) -> TouchSet | None:
        if not isinstance(value, list):
            return None
        entries: list[TouchSetEntry] = []
        for item in value:
            entry = _entry_from_row_item(item)
            if entry is None:
                return None
            entries.append(entry)
        return cls(tuple(entries))


def _entry_from_row_item(item: Any) -> TouchSetEntry | None:
    if isinstance(item, str):
        return TouchSetEntry(normalise_touch_path(item))
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        return None
    symbols = item.get("symbols") or []
    if not isinstance(symbols, list) or not all(isinstance(name, str) for name in symbols):
        return None
    return TouchSetEntry(normalise_touch_path(item["path"]), tuple(symbols))


def normalise_touch_path(path: str) -> str:
    clean = _BRACE_RENAME_RE.sub(r"\1", path.strip().strip("`").strip().replace("\\", "/")).replace("//", "/")
    if " => " in clean:
        clean = clean.split(" => ")[-1].strip().rstrip("}").strip()
    if clean.startswith("./"):
        clean = clean[2:]
    return clean.lstrip("/")


def _fenced_lines(lines: list[str]) -> set[int]:
    fenced: set[int] = set()
    fence: str | None = None
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
        elif fence is not None and match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            fenced.add(index)
            fence = None
            continue
        if fence is not None:
            fenced.add(index)
    return fenced


def _section_items(plan_markdown: str) -> list[str] | None:
    lines = plan_markdown.splitlines()
    fenced = _fenced_lines(lines)
    start = next(
        (index for index, line in enumerate(lines) if index not in fenced and _HEADING_RE.match(line.strip())), None
    )
    if start is None:
        return None
    items: list[str] = []
    in_top_item = False
    for index, line in enumerate(lines[start + 1 :], start + 1):
        if index in fenced:
            in_top_item = False
            continue
        if _NEXT_SECTION_RE.match(line):
            break
        match = _ITEM_RE.match(line)
        if match:
            items.append(match.group("body").strip())
        in_top_item = bool(match) or (in_top_item and line.startswith("  ") and not _ITEM_RE.match(line.strip()))
        if in_top_item and not match:
            items[-1] = f"{items[-1]} {line.strip()}"
    return items


def _entry_from_item(body: str) -> TouchSetEntry | None:
    backticked = _BACKTICK_RE.search(body)
    if backticked:
        raw, rest = backticked.group(1), body[backticked.end() :]
    else:
        raw, _, rest = body.partition(" ")
    path = normalise_touch_path(raw)
    if not path:
        return None
    symbols_match = _SYMBOLS_RE.search(rest.replace("\\_", "_"))
    symbols: tuple[str, ...] = ()
    if symbols_match:
        names = (name.strip().strip("`") for name in symbols_match.group("names").split(","))
        symbols = tuple(name for name in names if name)
    return TouchSetEntry(path, symbols)


def _is_placeholder(body: str) -> bool:
    stripped_words = body.strip(_PLACEHOLDER_WRAPPERS).split()
    if not stripped_words:
        return False
    first_word = stripped_words[0].strip(_PLACEHOLDER_WRAPPERS + _PLACEHOLDER_PUNCTUATION).lower()
    return first_word in _PLACEHOLDER_WORDS


def parse_touch_set_section(plan_markdown: str) -> TouchSet | None:
    items = _section_items(plan_markdown)
    if items is None:
        return None
    entries: list[TouchSetEntry] = []
    seen: set[str] = set()
    for body in items:
        entry = None if _is_placeholder(body) else _entry_from_item(body)
        if entry is None or entry.path in seen or is_metadata_path(entry.path):
            continue
        seen.add(entry.path)
        entries.append(entry)
    return TouchSet(tuple(entries))


def _class_body(body: str) -> str:
    return re.sub(r"([\\\[\]^&~|])", r"\\\1", body)


def _compile_char_class(chars: str, *, negated: bool) -> str:
    prefix = "^" if negated else ""
    candidate_class = f"[{prefix}{_class_body(chars)}]"
    try:
        re.compile(candidate_class)
    except re.error:
        return f"[{prefix}{re.escape(chars)}]"
    return candidate_class


def _glob_literal_or_class(token: str) -> str:
    is_bracket_class = len(token) > 2 and token.startswith("[") and token.endswith("]")
    if not is_bracket_class:
        return re.escape(token)
    body = token[1:-1]
    negated = body.startswith("!")
    class_chars = body[1:] if negated else body
    if not class_chars:
        return re.escape(token)
    return _compile_char_class(class_chars, negated=negated)


@functools.lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    for token in _GLOB_TOKEN_RE.findall(pattern):
        parts.append(_GLOB_TOKENS.get(token) or _glob_literal_or_class(token))
    return re.compile("".join(parts) + r"\Z", re.DOTALL)


def entry_matches(entry: TouchSetEntry, path: str) -> bool:
    normalised_path = normalise_touch_path(path)
    # why: literal paths with brackets (e.g. app/[id]/page.tsx) must match themselves before glob expansion
    if normalised_path == entry.path:
        return True
    is_directory_entry = entry.path.endswith("/")
    has_glob_syntax = any(char in entry.path for char in _GLOB_CHARS)
    if has_glob_syntax:
        match_pattern = f"{entry.path}**" if is_directory_entry else entry.path
        return _glob_regex(match_pattern).match(normalised_path) is not None
    if is_directory_entry:
        return normalised_path.startswith(entry.path)
    return False


def is_literal_entry(entry: TouchSetEntry) -> bool:
    return not entry.path.endswith("/") and "*" not in entry.path and "?" not in entry.path


@dataclass(frozen=True)
class TouchSetScore:
    precision: float | None
    recall: float | None
    unpredicted_paths: tuple[str, ...] = ()
    unused_entries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "unpredicted_paths": list(self.unpredicted_paths),
            "unused_entries": list(self.unused_entries),
        }


def code_paths(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(p for p in map(normalise_touch_path, paths) if p and not is_metadata_path(p)))


def score_touch_set(touch_set: TouchSet, changed_paths: list[str]) -> TouchSetScore:
    paths = code_paths(changed_paths)
    used = [entry for entry in touch_set.entries if any(entry_matches(entry, path) for path in paths)]
    unpredicted = [path for path in paths if not any(entry_matches(entry, path) for entry in touch_set.entries)]
    entries = touch_set.entries
    return TouchSetScore(
        precision=len(used) / len(entries) if entries else None,
        recall=(len(paths) - len(unpredicted)) / len(paths) if paths else None,
        unpredicted_paths=tuple(unpredicted),
        unused_entries=tuple(entry.path for entry in entries if entry not in used),
    )


def out_of_set_files(touch_set: TouchSet, changed_paths: list[str]) -> list[str]:
    return [p for p in code_paths(changed_paths) if not any(entry_matches(entry, p) for entry in touch_set.entries)]
