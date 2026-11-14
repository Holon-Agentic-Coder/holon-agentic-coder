"""Parse, normalise, match and score the planner's touch set (the ``## Touch Set`` plan section).

The planner records the parsed set on its ``plans.jsonl`` row (``touch_set``), and Stage 5 calibration scores it
against the execution diff (precision and recall). Admission and enforcement will read the same rows later. Format:
one backticked repository-relative POSIX path or glob per top-level bullet, a trailing ``/`` for a whole directory,
``*`` within one path segment and ``**`` across directories, and an optional ``symbols: a, b`` suffix that is stored
but never scored. Metadata paths (``git.is_metadata_path``) are dropped.
Pure logic with no I/O, kept outside the flow package so the planner entrypoint and calibration import it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sandbox_executor.git import is_metadata_path

_HEADING_RE = re.compile(r"^##\s+touch\s+set\s*$", re.IGNORECASE)
_NEXT_SECTION_RE = re.compile(r"^#{1,2}\s")
_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(?P<body>.*)$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_SYMBOLS_RE = re.compile(r"symbols\s*:\s*(?P<names>.*)$", re.IGNORECASE)
_BRACE_RENAME_RE = re.compile(r"\{[^{}]* => ([^{}]*)\}")
_PLACEHOLDER_WORDS = frozenset({"none", "n/a", "na", "nil", "no", "not"})
# Wrapping quotes, brackets and code or emphasis markers stripped before the placeholder test, so _none_ is skipped.
_PLACEHOLDER_WRAPPERS = "()[]{}<>\"'`*_~\u2018\u2019\u201c\u201d"
_GLOB_CHARS = ("*", "?", "[")
_GLOB_TOKEN_RE = re.compile(r"\*\*/|\*\*|\*|\?|\[[^\]/]+\]|[^*?\[]+|\[")
_GLOB_TOKENS = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}


@dataclass(frozen=True)
class TouchSetEntry:
    """One declared path, directory (trailing ``/``) or glob, with optional symbols for large modules."""

    path: str
    symbols: tuple[str, ...] = ()

    def to_row(self) -> dict[str, Any]:
        """Ledger shape: ``path`` plus ``symbols`` only when some were declared."""
        row: dict[str, Any] = {"path": self.path}
        if self.symbols:
            row["symbols"] = list(self.symbols)
        return row


@dataclass(frozen=True)
class TouchSet:
    """The ordered, de-duplicated entries a plan declares it will touch."""

    entries: tuple[TouchSetEntry, ...] = ()

    def to_row(self) -> list[dict[str, Any]]:
        """Ledger shape of the ``touch_set`` field on a ``plans.jsonl`` row."""
        return [entry.to_row() for entry in self.entries]

    @classmethod
    def from_row(cls, value: Any) -> TouchSet | None:
        """Rebuild from a ledger value; ``None`` for a missing field or an unknown shape (never raises)."""
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
    """Strip whitespace and backticks, drop a leading ``./`` or ``/``, keep a rename's target (``a/{b => c}/d`` too)."""
    clean = _BRACE_RENAME_RE.sub(r"\1", path.strip().strip("`").strip().replace("\\", "/")).replace("//", "/")
    if " => " in clean:
        clean = clean.split(" => ")[-1].strip().rstrip("}").strip()
    if clean.startswith("./"):
        clean = clean[2:]
    return clean.lstrip("/")


def _section_items(plan_markdown: str) -> list[str] | None:
    """Bodies of the top-level list items under ``## Touch Set``, or ``None`` when the heading is absent."""
    lines = plan_markdown.splitlines()
    start = next((index for index, line in enumerate(lines) if _HEADING_RE.match(line.strip())), None)
    if start is None:
        return None
    items: list[str] = []
    in_top_item = False
    for line in lines[start + 1 :]:
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
    """Parse one item body into an entry; ``None`` for an empty body."""
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
    words = body.strip(_PLACEHOLDER_WRAPPERS).split()
    return bool(words) and words[0].strip(_PLACEHOLDER_WRAPPERS + ".,;:").lower() in _PLACEHOLDER_WORDS


def parse_touch_set_section(plan_markdown: str) -> TouchSet | None:
    """Parse the ``## Touch Set`` section; ``None`` when absent. Placeholder items such as ``none`` are skipped."""
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
    """Escape only the characters special inside a regex class, so ranges such as ``a-c`` keep working."""
    return re.sub(r"([\\\[\]^&~|])", r"\\\1", body)


def _glob_literal_or_class(token: str) -> str:
    if len(token) > 2 and token.startswith("[") and token.endswith("]"):
        body = token[1:-1]
        negate, chars = (True, body[1:]) if body.startswith("!") else (False, body)
        if not chars:  # an empty class such as [!] matches its characters literally
            return re.escape(token)
        regex_class = f"[{'^' if negate else ''}{_class_body(chars)}]"
        try:
            re.compile(regex_class)
        except re.error:  # a reversed range such as [c-a]: match its characters literally instead
            return f"[{'^' if negate else ''}{re.escape(chars)}]"
        return regex_class
    return re.escape(token)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob where ``*`` and ``?`` stay within one segment and ``**/`` spans zero or more directories."""
    parts: list[str] = []
    for token in _GLOB_TOKEN_RE.findall(pattern):
        parts.append(_GLOB_TOKENS.get(token) or _glob_literal_or_class(token))
    return re.compile("".join(parts) + r"\Z", re.DOTALL)


def entry_matches(entry: TouchSetEntry, path: str) -> bool:
    """Whether a changed repository path falls under a declared entry (directory prefix, glob or exact path)."""
    clean = normalise_touch_path(path)
    if clean == entry.path:  # a literal path such as app/[id]/page.tsx must match itself
        return True
    is_dir = entry.path.endswith("/")
    if any(char in entry.path for char in _GLOB_CHARS):
        # A directory glob such as apps/*/tests/ covers everything below each matching directory.
        return _glob_regex(f"{entry.path}**" if is_dir else entry.path).match(clean) is not None
    if is_dir:
        return clean.startswith(entry.path)
    return clean == entry.path


@dataclass(frozen=True)
class TouchSetScore:
    """Touch-set accuracy against a diff; precision or recall is ``None`` when its denominator is zero."""

    precision: float | None
    recall: float | None
    unpredicted_paths: tuple[str, ...] = ()
    unused_entries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Calibration report shape."""
        return {
            "precision": self.precision,
            "recall": self.recall,
            "unpredicted_paths": list(self.unpredicted_paths),
            "unused_entries": list(self.unused_entries),
        }


def score_touch_set(touch_set: TouchSet, changed_paths: list[str]) -> TouchSetScore:
    """Precision: declared entries matching some changed file. Recall: changed non-metadata files covered."""
    paths = list(dict.fromkeys(p for p in map(normalise_touch_path, changed_paths) if not is_metadata_path(p)))
    used = [entry for entry in touch_set.entries if any(entry_matches(entry, path) for path in paths)]
    unpredicted = [path for path in paths if not any(entry_matches(entry, path) for entry in touch_set.entries)]
    entries = touch_set.entries
    return TouchSetScore(
        precision=len(used) / len(entries) if entries else None,
        recall=(len(paths) - len(unpredicted)) / len(paths) if paths else None,
        unpredicted_paths=tuple(unpredicted),
        unused_entries=tuple(entry.path for entry in entries if entry not in used),
    )
