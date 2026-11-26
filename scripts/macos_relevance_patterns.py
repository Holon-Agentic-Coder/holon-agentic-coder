"""Content patterns of the macOS relevance gate: the lines of a Python file that can behave differently on macOS.

``CONTENT_PATTERNS`` holds ``(reason, pattern)`` pairs that ``scripts.macos_relevance_rules`` matches against each
line of a changed Python file, without its ``#`` comment. Standard library only and Python 3.9 syntax.
"""

from __future__ import annotations

import re


def _imports(modules: str) -> str:
    """Return a pattern for ``import``/``from`` of ``modules``, also as a later name of ``import a, b``."""
    return rf"^\s*(import|from)\s+([\w.]+\s*,\s*)*({modules})\b"


_FS_OS_PATH = (
    r"realpath|abspath|samefile|islink|ismount|expanduser|getmtime|getctime|getatime|exists|lexists|isfile|isdir"
    r"|getsize"
)
_FS_OS = (
    r"chmod|chown|lchown|symlink|link|readlink|rename|replace|scandir|listdir|walk|stat|lstat|access|getcwd|chdir"
    r"|umask|statvfs|makedirs|mkdir|remove|unlink|rmdir|removedirs"
)
# ``Path.group`` and ``Path.replace`` are left out: ``.group(`` is mostly ``re.Match.group`` and ``.replace(`` mostly
# ``str.replace`` or ``datetime.replace``; ``Path.replace(target)`` is matched by its single positional argument.
_FS_PATHLIB = (
    r"resolve|absolute|samefile|is_symlink|symlink_to|hardlink_to|readlink|chmod|lchmod|stat|lstat|owner"
    r"|iterdir|glob|rglob|walk|rename|expanduser|cwd|exists|is_file|is_dir|mkdir|unlink|rmdir|touch"
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
        ("process", _imports("multiprocessing"), 0),
        ("process", r"\bmultiprocessing\.", 0),
        ("process", r"\bProcessPoolExecutor\b", 0),
        ("platform-check", r"\bsys\.platform\b", 0),
        ("platform-check", _imports("platform"), 0),
        ("platform-check", r"\bplatform\.", 0),
        ("platform-check", r"\bos\.name\b", 0),
        ("platform-check", r"\bos\.uname\(", 0),
        ("posix-module", _imports("signal|fcntl|termios|pty|tty|resource|pwd|grp|select"), 0),
        ("posix-module", r"\bsignal\.", 0),
        ("posix-module", r"\bos\.sched_\w+\(", 0),
        ("posix-module", r"\bAF_UNIX\b", 0),
        ("home-or-temp", r"^(?=.*(environ|getenv|setenv|putenv|delenv)).*\bHOME\b", 0),
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
        ("filesystem", r"\.replace\(\s*([^,=()\s]|\([^()]*\))([^,=()]|\([^()]*\))*\)", 0),
        ("keychain-or-launcher", r"keychain", re.IGNORECASE),
        ("keychain-or-launcher", r"\bfind-generic-password\b", 0),
        ("keychain-or-launcher", r"\bsandbox_executor\.launcher\b", 0),
        ("keychain-or-launcher", r"^\s*from\s+\.+launcher\b", 0),
    )
)
