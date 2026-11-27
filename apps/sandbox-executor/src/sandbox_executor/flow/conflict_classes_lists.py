from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

from sandbox_executor.flow.conflict_classes_tokens import unchanged_outside

_Literal = ast.List | ast.Dict


@dataclass(frozen=True)
class _ListSide:
    tree: ast.Module
    data: bytes


def _parse(text: str) -> _ListSide | None:
    try:
        return _ListSide(ast.parse(text), text.encode("utf-8"))
    except (SyntaxError, ValueError):
        return None


def _literal(node: ast.stmt) -> _Literal | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        value: ast.expr | None = node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        value = node.value
    else:
        return None
    return value if isinstance(value, ast.List | ast.Dict) else None


def _entries(literal: _Literal) -> list[str]:
    if isinstance(literal, ast.List):
        return [ast.dump(element) for element in literal.elts]
    pairs = zip(literal.keys, literal.values, strict=True)
    return [f"{ast.dump(key) if key else '**'}:{ast.dump(value)}" for key, value in pairs]


def _shell(node: ast.stmt) -> str:
    emptied = copy.deepcopy(node)
    literal = _literal(emptied)
    if isinstance(literal, ast.List):
        literal.elts = []
    elif isinstance(literal, ast.Dict):
        literal.keys, literal.values = [], []
    return ast.dump(emptied)


def _differing_index(base: ast.Module, ours: ast.Module, theirs: ast.Module) -> int | None:
    if not len(base.body) == len(ours.body) == len(theirs.body):
        return None
    differing = [
        index
        for index, nodes in enumerate(zip(base.body, ours.body, theirs.body, strict=True))
        if len({ast.dump(node) for node in nodes}) > 1
    ]
    if len(differing) != 1:
        return None
    nodes = (base.body[differing[0]], ours.body[differing[0]], theirs.body[differing[0]])
    if any(_literal(node) is None for node in nodes) or len({_shell(node) for node in nodes}) > 1:
        return None
    return differing[0]


def _offset(data: bytes, line: int, col: int) -> int:
    start = 0
    for _ in range(line - 1):
        start = data.index(b"\n", start) + 1
    return start + col


def _segment(side: _ListSide, first: ast.expr, last: ast.expr) -> bytes:
    begin = _offset(side.data, first.lineno, first.col_offset)
    return side.data[begin : _offset(side.data, last.end_lineno or 0, last.end_col_offset or 0)]


def _entry_segments(side: _ListSide, literal: _Literal, indexes: list[int]) -> list[bytes]:
    if isinstance(literal, ast.List):
        return [_segment(side, literal.elts[i], literal.elts[i]) for i in indexes]
    segments = []
    for i in indexes:
        key, value = literal.keys[i], literal.values[i]
        segments.append(_segment(side, key, value) if key else b"**" + _segment(side, value, value))
    return segments


def _appended_nodes(literal: _Literal, prefix: int) -> list[tuple[ast.expr | None, ast.expr]]:
    if isinstance(literal, ast.List):
        return [(element, element) for element in literal.elts[prefix:]]
    return list(zip(literal.keys[prefix:], literal.values[prefix:], strict=True))


def _line_end(data: bytes, pos: int) -> int:
    newline = data.find(b"\n", pos)
    return len(data) if newline < 0 else newline


def _insert(side: _ListSide, literal: _Literal, segments: list[bytes]) -> bytes | None:
    last = (literal.elts or [None])[-1] if isinstance(literal, ast.List) else (literal.values or [None])[-1]
    if last is None:
        return None
    end = _offset(side.data, last.end_lineno or 0, last.end_col_offset or 0)
    if literal.lineno == literal.end_lineno:
        return side.data[:end] + b"".join(b", " + segment for segment in segments) + side.data[end:]
    first = last if isinstance(literal, ast.List) else (literal.keys[-1] or last)
    line_start = _offset(side.data, first.lineno, 0)
    start_line = side.data[line_start : _line_end(side.data, line_start)]
    indent = start_line[: len(start_line) - len(start_line.lstrip())]
    rest = side.data[end:]
    comma = len(rest) - len(rest.lstrip(b" \t")) if rest.lstrip(b" \t").startswith(b",") else -1
    insert_at = end + comma + 1 if comma >= 0 else end
    line_rest = side.data[insert_at : _line_end(side.data, insert_at)]
    # why: the last entry's trailing comment must stay on that entry's line.
    if line_rest.strip().startswith(b"#"):
        insert_at += len(line_rest)
    prefix = side.data[:end] + (b"" if comma >= 0 else b",") + side.data[end:insert_at]
    lines = b"".join(b"\n" + indent + segment + b"," for segment in segments)
    return prefix + lines + side.data[insert_at:]


def _branch_only(start: int, ours_entries: list[str], theirs_entries: list[str]) -> list[int]:
    seen = set(theirs_entries)
    picked: list[int] = []
    for index in range(start, len(ours_entries)):
        if ours_entries[index] not in seen:
            seen.add(ours_entries[index])
            picked.append(index)
    return picked


def _overlapping_keys(ours: _Literal, theirs: _Literal, branch_only: list[int]) -> bool:
    if not isinstance(ours, ast.Dict) or not isinstance(theirs, ast.Dict):
        return False
    # why: a splat or a reused key would let the flow branch's value silently override the target's.
    target_keys = {ast.dump(key) for key in theirs.keys if key is not None}
    appended = [ours.keys[i] for i in branch_only]
    return any(key is None or ast.dump(key) in target_keys for key in appended)


def _verified(result: bytes, theirs: _ListSide, index: int, expected: list[str]) -> str | None:
    parsed = _parse(result.decode("utf-8"))
    if parsed is None or len(parsed.tree.body) != len(theirs.tree.body):
        return None
    for position, (got, want) in enumerate(zip(parsed.tree.body, theirs.tree.body, strict=True)):
        same = _shell(got) == _shell(want) if position == index else ast.dump(got) == ast.dump(want)
        if not same:
            return None
    literal = _literal(parsed.tree.body[index])
    return result.decode("utf-8") if literal is not None and _entries(literal) == expected else None


def appended_list_union(base_text: str, ours_text: str, theirs_text: str) -> str | None:
    base, ours, theirs = _parse(base_text), _parse(ours_text), _parse(theirs_text)
    if base is None or ours is None or theirs is None:
        return None
    index = _differing_index(base.tree, ours.tree, theirs.tree)
    if index is None:
        return None
    literals = [_literal(side.tree.body[index]) for side in (base, ours, theirs)]
    base_lit, ours_lit, theirs_lit = literals
    if base_lit is None or ours_lit is None or theirs_lit is None:
        return None
    base_entries, ours_entries, theirs_entries = (_entries(lit) for lit in (base_lit, ours_lit, theirs_lit))
    prefix = len(base_entries)
    branch_only = _branch_only(prefix, ours_entries, theirs_entries)
    appended_only = ours_entries[:prefix] == base_entries and theirs_entries[:prefix] == base_entries
    # why: the flow branch's comments outside the appended entries must survive the union unchanged.
    unchanged = unchanged_outside(base_text, ours_text, _appended_nodes(ours_lit, prefix))
    if not appended_only or not unchanged or _overlapping_keys(ours_lit, theirs_lit, branch_only):
        return None
    if not branch_only:
        return theirs_text
    result = _insert(theirs, theirs_lit, _entry_segments(ours, ours_lit, branch_only))
    expected = theirs_entries + [ours_entries[i] for i in branch_only]
    return None if result is None else _verified(result, theirs, index, expected)
