from __future__ import annotations

import ast
import io
import tokenize

_LAYOUT = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}

_Position = tuple[int, int]


def _tokens(text: str) -> list[tokenize.TokenInfo] | None:
    try:
        return list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return None


def _text(token: tokenize.TokenInfo) -> str:
    # why: comments are compared too, so a real comment edit is never mistaken for a mechanical one.
    return " ".join(token.string.split()) if token.type == tokenize.COMMENT else token.string


def python_lines(text: str) -> list[str] | None:
    tokens = _tokens(text)
    if tokens is None:
        return None
    indents, lines, current = [""], [], []
    for token in tokens:
        if token.type == tokenize.INDENT:
            indents.append(token.string)
        elif token.type == tokenize.DEDENT:
            indents.pop()
        elif token.type in (tokenize.NL, tokenize.NEWLINE):
            if current:
                lines.append(indents[-1] + " ".join(current))
            current = []
        elif token.type != tokenize.ENDMARKER:
            current.append(_text(token))
    return [*lines, indents[-1] + " ".join(current)] if current else lines


def _position(lines: list[str], line: int, byte_col: int) -> _Position:
    return line, len(lines[line - 1].encode("utf-8")[:byte_col].decode("utf-8"))


def _span(lines: list[str], first: ast.expr, last: ast.expr) -> tuple[_Position, _Position]:
    end = _position(lines, last.end_lineno or 0, last.end_col_offset or 0)
    return _position(lines, first.lineno, first.col_offset), end


def unchanged_outside(base_text: str, ours_text: str, entries: list[tuple[ast.expr | None, ast.expr]]) -> bool:
    base, ours = _tokens(base_text), _tokens(ours_text)
    # why: a ``**`` splat entry has no first node, so it cannot be cut out cleanly.
    if base is None or ours is None or any(first is None for first, _ in entries):
        return False
    lines = ours_text.splitlines(keepends=True)
    spans = [_span(lines, first, last) for first, last in entries if first is not None]

    def kept(tokens: list[tokenize.TokenInfo], cut: list[tuple[_Position, _Position]]) -> list[tuple[int, str]]:
        return [
            (token.type, _text(token))
            for token in tokens
            if token.type not in _LAYOUT
            and token.string != ","
            and not any(start <= token.start and token.end <= end for start, end in cut)
        ]

    return kept(base, []) == kept(ours, spans)
