"""Platform-sensitivity rules of the macOS relevance gate: which changed file can behave differently on macOS.

A changed file is platform-sensitive when its path or its content says so; every other file is neutral. Paths are
judged first, the new path and, for a rename or a deletion, the old path:

- dependency manifests and lock files: any ``pyproject.toml`` or ``uv.lock`` at any depth;
- every path under ``.github/`` (workflows, actions, this gate's action);
- the gate itself: ``scripts/macos_relevance*.py`` and ``apps/sandbox-executor/tests/test_macos_relevance*.py``;
- shell: ``*.sh``, ``*.bash``, ``Makefile``, the repository launcher ``holon`` and any file whose first line is a
  ``#!`` shebang naming ``sh``, ``bash`` or ``zsh``;
- containers: ``Dockerfile``, ``Dockerfile.*``, ``*.Dockerfile``, ``docker-bake.hcl``;
- launcher and keychain code: ``apps/sandbox-executor/src/sandbox_executor/launcher/``,
  ``apps/sandbox-executor/src/sandbox_executor/entrypoint/``, ``apps/sandbox-executor/entrypoint/`` and any file whose
  name contains ``keychain``, ``launcher`` or ``credential``;
- test-wide configuration: every ``conftest.py``.

Then the content of a Python file (``.py``), its new and its old version, tests included: each line, without its ``#``
comment, is matched against ``CONTENT_PATTERNS`` (processes, platform checks, POSIX-only modules, home and temporary
directories, filesystem operations beyond joining paths and plain reads and writes, keychain and launcher names).
Pure path joining and naming and plain content I/O (``open``, ``read_text``, ``write_text``, ``mkdir``, ``exists``,
``unlink`` and the like) stay neutral. Content that is larger than ``CONTENT_CAP`` bytes, not UTF-8 or unreadable is
sensitive. Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass

from scripts.macos_relevance_git import ChangedFile

CONTENT_CAP = 1024 * 1024
SENSITIVE_FILE_NAMES = frozenset(
    {"pyproject.toml", "uv.lock", "Makefile", "holon", "conftest.py", "Dockerfile", "docker-bake.hcl"}
)
SENSITIVE_SUFFIXES = (".sh", ".bash", ".Dockerfile")
SENSITIVE_NAME_PREFIXES = ("Dockerfile.",)
SENSITIVE_PREFIXES = (
    ".github/",
    "apps/sandbox-executor/src/sandbox_executor/launcher/",
    "apps/sandbox-executor/src/sandbox_executor/entrypoint/",
    "apps/sandbox-executor/entrypoint/",
)
SENSITIVE_NAME_FRAGMENTS = ("keychain", "launcher", "credential")
GATE_PATH_PATTERNS = ("scripts/macos_relevance*.py", "apps/sandbox-executor/tests/test_macos_relevance*.py")
CONTENT_SUFFIX = ".py"
SHEBANG = re.compile(r"^#!.*\b(sh|bash|zsh)\b")
UNREADABLE_CONTENT = "unreadable-content"
BY_PATH, BY_CONTENT = "path", "content"
# ``read_blob(side, path)`` returns the text of ``path`` on ``side`` (``new`` or ``old``), or ``None`` when unreadable.
BlobReader = Callable[[str, str], "str | None"]


def _imports(modules: str) -> str:
    """Return a pattern for ``import``/``from`` of ``modules``, also as a later name of ``import a, b``."""
    return rf"^\s*(import|from)\s+([\w.]+\s*,\s*)*({modules})\b"


_FS_OS_PATH = r"realpath|abspath|samefile|islink|ismount|expanduser|getmtime|getctime|getatime"
_FS_OS = (
    r"chmod|chown|lchown|symlink|link|readlink|rename|replace|scandir|listdir|walk|stat|lstat|access|getcwd|chdir"
    r"|umask|statvfs"
)
_FS_PATHLIB = (
    r"resolve|absolute|samefile|is_symlink|symlink_to|hardlink_to|readlink|chmod|lchmod|stat|lstat|owner|group"
    r"|iterdir|glob|rglob|walk|rename|replace|expanduser|cwd"
)
# (reason, pattern) pairs; the README's "macOS Relevance Gate" section lists the same table.
CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (reason, re.compile(pattern, flags))
    for reason, pattern, flags in (
        ("process", _imports("subprocess"), 0),
        ("process", r"\bsubprocess\.", 0),
        ("process", r"\bos\.(system|popen|fork|exec\w*|spawn\w*|kill|killpg|getpgid|setsid|wait\w*)\(", 0),
        ("process", r"\bpexpect\b", 0),
        ("process", r"\basyncio\.create_subprocess_", 0),
        ("platform-check", r"\bsys\.platform\b", 0),
        ("platform-check", _imports("platform"), 0),
        ("platform-check", r"\bplatform\.", 0),
        ("platform-check", r"\bos\.name\b", 0),
        ("platform-check", r"\bos\.uname\(", 0),
        ("posix-module", _imports("signal|fcntl|termios|pty|tty|resource|pwd|grp|select"), 0),
        ("posix-module", r"\bsignal\.", 0),
        ("home-or-temp", r"^(?=.*(environ|getenv)).*\bHOME\b", 0),
        ("home-or-temp", r"\bexpanduser\(", 0),
        ("home-or-temp", r"\bPath\.home\(", 0),
        ("home-or-temp", r"\bTMPDIR\b", 0),
        ("home-or-temp", _imports("tempfile"), 0),
        ("home-or-temp", r"\bgettempdir\(", 0),
        ("filesystem", rf"\bos\.path\.({_FS_OS_PATH})\(", 0),
        ("filesystem", rf"\bos\.({_FS_OS})\(", 0),
        ("filesystem", r"^\s*from\s+os(\.path)?\s+import\b", 0),
        ("filesystem", _imports("shutil"), 0),
        ("filesystem", r"\bshutil\.", 0),
        ("filesystem", rf"\.({_FS_PATHLIB})\(", 0),
        ("keychain-or-launcher", r"keychain", re.IGNORECASE),
        ("keychain-or-launcher", r"\bsecurity\s+find-generic-password\b", 0),
        ("keychain-or-launcher", r"\bsandbox_executor\.launcher\b", 0),
        ("keychain-or-launcher", r"^\s*from\s+\.+launcher\b", 0),
    )
)


@dataclass(frozen=True)
class Verdict:
    """The judgement of one changed file: sensitive or not, and the basis, side and reason of a sensitive verdict."""

    change: ChangedFile
    sensitive: bool
    basis: str = ""
    side: str = ""
    reason: str = ""


def path_reason(path: str) -> str | None:
    """Return why ``path`` is platform-sensitive by its path alone, or ``None``."""
    name = posixpath.basename(path)
    checks = (
        (name in SENSITIVE_FILE_NAMES, f"file-name {name}"),
        (path.startswith(SENSITIVE_PREFIXES), "sensitive-directory"),
        (any(fnmatch.fnmatchcase(path, pattern) for pattern in GATE_PATH_PATTERNS), "relevance-gate"),
        (name.endswith(SENSITIVE_SUFFIXES) or name.startswith(SENSITIVE_NAME_PREFIXES), "shell-or-container"),
        (any(fragment in name.lower() for fragment in SENSITIVE_NAME_FRAGMENTS), "keychain-launcher-or-credential"),
    )
    return next((reason for matched, reason in checks if matched), None if path else "empty-path")


def shebang_reason(first_line: str) -> str | None:
    """Return ``shell-shebang`` when ``first_line`` is a ``#!`` line naming ``sh``, ``bash`` or ``zsh``."""
    return "shell-shebang" if SHEBANG.match(first_line) else None


def strip_comment(line: str) -> str:
    """Return ``line`` without a trailing ``#`` comment; a ``#`` inside a quoted string is kept (approximately)."""
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#":
            return line[:index]
    return line


def content_reason(text: str) -> str | None:
    """Return ``<reason>: <matched text>`` of the first content pattern found in ``text`` outside comments."""
    for line in text.splitlines():
        code = strip_comment(line)
        for reason, pattern in CONTENT_PATTERNS:
            found = pattern.search(code)
            if found:
                return f"{reason} `{found.group(0).strip()}`"
    return None


def blob_reason(path: str, text: str | None) -> str | None:
    """Judge one side's content: unreadable is sensitive, a shebang counts for every file, patterns for ``.py``."""
    if text is None:
        return UNREADABLE_CONTENT
    reason = shebang_reason(text.partition("\n")[0])
    if reason is None and path.endswith(CONTENT_SUFFIX):
        reason = content_reason(text)
    return reason


def is_content_judged(path: str) -> bool:
    """Return true for a Python file or a file without an extension (which may carry a shell shebang)."""
    name = posixpath.basename(path)
    return name.endswith(CONTENT_SUFFIX) or "." not in name


def judge_change(change: ChangedFile, read_blob: BlobReader) -> Verdict:
    """Judge ``change``: the paths of both sides first, then the content of both sides; the first reason decides."""
    sides = change.sides()
    for side, path in sides:
        reason = path_reason(path)
        if reason is not None:
            return Verdict(change, True, BY_PATH, side, reason)
    for side, path in sides:
        if is_content_judged(path):
            reason = blob_reason(path, read_blob(side, path))
            if reason is not None:
                return Verdict(change, True, BY_CONTENT, side, reason)
    return Verdict(change, False)
