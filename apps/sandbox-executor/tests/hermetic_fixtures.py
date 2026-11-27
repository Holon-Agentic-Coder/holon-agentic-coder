# why: a bind-mounted host temp dir keeps the host owner on Linux CI, so git refuses it as dubious
# why: ownership and the image's uid 1000 cannot write it; 0o777 is CWE-732 and --user breaks the
# why: entrypoint. A named volume has one owner inside, stays off the host and is reclaimed by
# why: docker volume rm regardless of inner ownership.
import os
import shutil
import subprocess
import uuid
from collections.abc import Sequence

DEFAULT_FIXTURE_IMAGE = "holon/orchestrator"
IMAGE_UID = "1000:1000"
SANDBOX_WORKSPACE_MOUNT = "/home/holon/.holon-sandbox/workspace"


# why: role containers never reach a git remote, so the seeded volume stands in for the host
# why: worktree at the sandbox workspace mount and there is no URL.
def remote_path(volume: str) -> str:
    return SANDBOX_WORKSPACE_MOUNT


def _seed_script(workspace: str) -> str:
    return f"""\
set -eu
git init -b main -q {workspace}
cd {workspace}
git config user.email test@holon.com
git config user.name "Holon Fixture"
mkdir -p holon-knowledge/ledger
: > holon-knowledge/ledger/intents.jsonl
git add -A
git commit -qm "fixture seed"
# A freshly created volume is root-owned and the seed must run as root to populate it, so hand
# ownership to the image's unprivileged user; otherwise the role container cannot commit into it.
chown -R {IMAGE_UID} {workspace}
"""


class RemoteFixtureError(RuntimeError): ...


# why: callers reclaim the volume via addCleanup(remove_remote_volume) so a failing assertion
# why: cannot leak it.
def create_seeded_remote(image: str = DEFAULT_FIXTURE_IMAGE) -> str:
    volume = f"holon-it-{uuid.uuid4().hex[:16]}"
    remote = remote_path(volume)
    create = subprocess.run(["docker", "volume", "create", volume], capture_output=True, text=True, check=False)
    if create.returncode != 0:
        raise RemoteFixtureError(f"docker volume create failed: {create.stderr}")

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "root",
                "-v",
                f"{volume}:{remote}",
                "--entrypoint",
                "bash",
                image,
                "-c",
                _seed_script(remote),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.SubprocessError as exc:
        remove_remote_volume(volume)
        raise RemoteFixtureError(f"seeding docker run failed: {exc}") from exc

    if result.returncode != 0:
        remove_remote_volume(volume)
        raise RemoteFixtureError(
            f"seeding remote fixture failed with code {result.returncode}.\n"
            f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        )
    return volume


def remove_remote_volume(volume: str) -> None:
    subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, text=True, check=False)


def remote_volume_args(volume: str) -> list[str]:
    return ["-v", f"{volume}:{remote_path(volume)}"]


# why: the repository never exists on the host, so inspection runs in a container as the image
# why: user that owns the volume contents, which needs no ownership exception.
def remote_show_file(
    ref: str, path: str, volume: str, image: str = DEFAULT_FIXTURE_IMAGE
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{volume}:{remote_path(volume)}",
            "--entrypoint",
            "git",
            image,
            "-C",
            remote_path(volume),
            "show",
            f"{ref}:{path}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# why: one shared child-suite command; two hand-built command lines once drifted from the runner
# why: and marker selection of .github/workflows/test-unit.yml.
# why: uv is the only sanctioned runner (.agents/rules.md); unittest ignores pytest markers and
# why: re-runs integration tests that need images the unit job never builds.
UV = "uv"

# why: stress tests re-run the whole suite, so a child selecting them would spawn another child.
UNIT_MARKER_EXPRESSION = "not integration_test and not stress"

# why: pyproject.toml at the repository root supplies the pytest configuration, so children must
# why: run from here.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

TESTS_DIR = os.path.relpath(os.path.dirname(os.path.abspath(__file__)), REPO_ROOT)

CHILD_TIMEOUT_SECONDS = 1800


CHILD_SUITE_SENTINEL = "HOLON_CHILD_SUITE_ACTIVE"


def pytest_suite_command(*extra_args: str, targets: Sequence[str] = (TESTS_DIR,)) -> list[str]:
    # why: no:cacheprovider keeps a child with a throwaway HOME from writing cache state, and
    # why: importlib import mode avoids basename collisions if a sibling branch adds a conftest.py.
    return [
        UV,
        "run",
        "--no-sync",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "-n",
        "auto",
        "--dist",
        "worksteal",
        "-q",
        "--tb=short",
        "-m",
        UNIT_MARKER_EXPRESSION,
        *targets,
        *extra_args,
    ]


def simulated_container_env(sim_home: str, sentinel_var: str) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = sim_home
    env["USER"] = "holon"
    env["USERNAME"] = "holon"
    env["HOLON_ROLE"] = "executor"
    env["HOLON_IN_SANDBOX"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # why: the workspace-deletion incident happened because nothing pinned the workspace, so an
    # why: unpatched get_workspace_dir() must resolve into sim_home.
    env.pop("HOLON_TARGET_WORKTREE", None)
    env.pop("HOLON_REPO_DIR", None)
    env[sentinel_var] = "1"
    env[CHILD_SUITE_SENTINEL] = "1"
    # why: with a rewritten HOME a child was seen downloading CPython and building a fresh .venv
    # why: that lacked the holon console script; pin the environment and never download.
    env["UV_PROJECT_ENVIRONMENT"] = project_environment()
    env["UV_PYTHON_DOWNLOADS"] = "never"
    return env


def project_environment() -> str:
    active = os.environ.get("VIRTUAL_ENV")
    if active and os.path.isdir(active):
        return os.path.abspath(active)
    return os.path.join(REPO_ROOT, ".venv")


# why: callers skip rather than fall back; silently switching to unittest made the original guard
# why: measure the wrong artifact.
def uv_available() -> bool:
    return bool(shutil.which(UV))


# why: limited to the index; broader walks would include the append-only ledgers other guards
# why: already protect, and a pre-existing developer index only counts when a test changes it.
KNOWLEDGE_INDEX_SUBDIR = os.path.join("holon-knowledge", ".index")

CHECKOUT_KNOWLEDGE_INDEX_DIR = os.path.join(REPO_ROOT, KNOWLEDGE_INDEX_SUBDIR)


# why: os.walk swallows scandir errors by default, so an unreadable index would yield empty
# why: snapshots on both sides and the guard would fail open.
def _raise_walk_error(error: OSError) -> None:
    raise error


def knowledge_index_snapshot(root: str) -> dict[str, tuple[int, int]]:
    # why: SQLite WAL side-files are included because WAL mode creates them on first open.
    index_dir = os.path.join(root, KNOWLEDGE_INDEX_SUBDIR)
    snapshot: dict[str, tuple[int, int]] = {}
    if not os.path.isdir(index_dir):
        return snapshot
    for dirpath, _dirnames, filenames in os.walk(index_dir, onerror=_raise_walk_error):
        for name in filenames:
            full = os.path.join(dirpath, name)
            st = os.stat(full)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            snapshot[rel] = (st.st_size, st.st_mtime_ns)
    return snapshot


def describe_index_changes(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    changes: list[str] = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            changes.append(f"created {path}")
        elif path not in after:
            changes.append(f"deleted {path}")
        elif before[path] != after[path]:
            changes.append(f"modified {path}")
    return changes
