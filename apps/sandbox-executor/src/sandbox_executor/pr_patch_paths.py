"""The paths a pull request's patch (``gh pr diff``) changes, read from the patch rather than ``--name-only``.

``gh pr diff --name-only`` lists only a renamed file's destination, which the base does not have, so a rename out of
a folder or glob entry would never overlap it. The patch also names the rename's source (``rename from``) and a
deletion's path (``--- a/``), so both sides of every file count as changed. Pure parsing with no I/O.
"""

from __future__ import annotations

import codecs

_HEADER = "diff --git "
#: Extended-header and file-header prefixes that name a changed path (a copy's source is unchanged, so not listed).
_PATH_PREFIXES = ("rename from ", "rename to ", "copy to ", "--- a/", "+++ b/")


def _unquote(path: str) -> str:
    """A path git printed C-quoted (``"a/caf\\303\\251.py"``) decoded; any other path unchanged."""
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    raw = codecs.escape_decode(path[1:-1].encode("utf-8"))[0]
    return raw.decode("utf-8", errors="surrogateescape")


def _header_path(rest: str) -> str | None:
    """A plain or C-quoted ``a/P b/P`` header's path (a mode or binary change has no other path line), else None."""
    if rest.startswith('"') and rest.endswith('"'):
        sides = rest[1:-1].split('" "')
        if len(sides) == 2 and sides[0].startswith("a/") and sides[1].startswith("b/"):
            old, new = (_unquote(f'"{side}"')[2:] for side in sides)
            return old if old == new else None
        return None
    end = 2 + (len(rest) - 5) // 2  # "a/" + P + " b/" + P
    if rest.startswith("a/") and rest[end:].startswith(" b/") and rest[2:end] == rest[end + 3 :]:
        return rest[2:end]
    return None


def _quoted_prefix_path(line: str) -> str | None:
    """The path of a ``--- "a/..."`` or ``+++ "b/..."`` line git quoted, else None."""
    for prefix, side in (('--- "', "a/"), ('+++ "', "b/")):
        if line.startswith(prefix):
            return _unquote(line[4:]).removeprefix(side)
    return None


def patch_paths(patch: str) -> list[str]:
    """Every path ``patch`` changes, in first-seen order: both sides of a rename and a deleted file included."""
    paths: dict[str, None] = {}
    in_header = False  # path lines are read only between a file's "diff --git" and its first "@@" hunk
    for line in patch.splitlines():
        line = line.removesuffix("\t")  # git pads a path holding a space with a tab on the --- and +++ lines
        path = None
        if line.startswith(_HEADER):
            in_header = True
            path = _header_path(line[len(_HEADER) :])
        elif line.startswith("@@"):
            in_header = False
        elif in_header:
            prefix = next((p for p in _PATH_PREFIXES if line.startswith(p)), None)
            path = _unquote(line[len(prefix) :]) if prefix else _quoted_prefix_path(line)
        if path:
            paths[path] = None
    return list(paths)
