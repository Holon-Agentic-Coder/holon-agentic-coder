"""In-process answer for the one ``gh`` read the mergeability probe issues from a test's temporary repository.

The Stage conflict probe calls ``check_pr_mergeability``, which starts the host's real ``gh pr view`` whenever ``gh``
is installed. In a temporary repository (a local-path ``origin``) that process can only fail, and the local git
fallback decides. :func:`stubbed_mergeability_read` returns that failure without starting the process.

It steps aside (returns None) for every other command, on a host without ``gh``, and when a test put its own ``gh``
first on ``PATH``. It sits behind ``subprocess.run`` (see ``tests/flow/conftest.py``), so a test that patches
``subprocess.run`` never reaches it.
"""

import shutil
import subprocess
from typing import Any

from sandbox_executor.flow.github.adapter import MERGEABILITY_FIELDS

#: The host's own ``gh``, resolved once at import: the only binary the stand-in answers for.
HOST_GH = shutil.which("gh")
#: What the stand-in reports on standard error, so a log line names it instead of the real ``gh``.
STUB_STDERR = "gh_probe_stub: a test repository has no GitHub remote (stand-in for the host gh, no process started)"


def is_mergeability_read(cmd: Any) -> bool:
    """True when ``cmd`` is exactly ``gh pr view <branch> --json <the adapter's mergeability fields>``."""
    if not isinstance(cmd, (list, tuple)) or len(cmd) != 6:
        return False
    return list(cmd[:3]) == ["gh", "pr", "view"] and list(cmd[4:]) == ["--json", MERGEABILITY_FIELDS]


def stubbed_mergeability_read(cmd: Any) -> subprocess.CompletedProcess[str] | None:
    """Return the host ``gh``'s failed result for the mergeability read, or None when the stand-in must not answer."""
    if HOST_GH is None or not is_mergeability_read(cmd) or shutil.which("gh") != HOST_GH:
        return None
    return subprocess.CompletedProcess(list(cmd), 1, stdout="", stderr=STUB_STDERR)
