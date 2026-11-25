from __future__ import annotations

import codecs

_HEADER = "diff --git "
_PATH_PREFIXES = ("rename from ", "rename to ", "copy to ", "--- a/", "+++ b/")
_HUNK_HEADER_PREFIX = "@@"
_QUOTED_FILE_HEADER_PREFIXES = (('--- "', "a/"), ('+++ "', "b/"))
_UNQUOTED_HEADER_PREFIX_OVERHEAD = len("a/") + len(" b/")
_TRAILING_SPACE_PAD_TAB = "\t"


def _unquote(path: str) -> str:
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    raw = codecs.escape_decode(path[1:-1].encode("utf-8"))[0]
    return raw.decode("utf-8", errors="surrogateescape")


def _header_path(rest: str) -> str | None:
    if rest.startswith('"') and rest.endswith('"'):
        sides = rest[1:-1].split('" "')
        if len(sides) == 2 and sides[0].startswith("a/") and sides[1].startswith("b/"):
            old, new = (_unquote(f'"{side}"')[2:] for side in sides)
            return old if old == new else None
        return None
    path_end = len("a/") + (len(rest) - _UNQUOTED_HEADER_PREFIX_OVERHEAD) // 2
    if rest.startswith("a/") and rest[path_end:].startswith(" b/") and rest[2:path_end] == rest[path_end + 3 :]:
        return rest[2:path_end]
    return None


def _quoted_prefix_path(line: str) -> str | None:
    for prefix, side in _QUOTED_FILE_HEADER_PREFIXES:
        if line.startswith(prefix):
            return _unquote(line[4:]).removeprefix(side)
    return None


def patch_paths(patch: str) -> list[str]:
    paths: dict[str, None] = {}
    inside_file_header = False
    for line in patch.splitlines():
        line = line.removesuffix(_TRAILING_SPACE_PAD_TAB)
        path = None
        if line.startswith(_HEADER):
            inside_file_header = True
            path = _header_path(line[len(_HEADER) :])
        elif line.startswith(_HUNK_HEADER_PREFIX):
            inside_file_header = False
        elif inside_file_header:
            prefix = next((p for p in _PATH_PREFIXES if line.startswith(p)), None)
            path = _unquote(line[len(prefix) :]) if prefix else _quoted_prefix_path(line)
        if path:
            paths[path] = None
    return list(paths)
