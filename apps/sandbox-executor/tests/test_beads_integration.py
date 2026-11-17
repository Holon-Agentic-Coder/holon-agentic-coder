"""Hermetic guards for the committed beads (bd) issue-tracker pilot surface.

Pins everything the pilot commits that is testable without network: the shape of the tracked
``.beads/config.yaml`` (``hac`` issue prefix, dolt local-only, no ``node_id``), the ``.gitignore``
entry for the embedded Dolt database, the pinned ``BD_VERSION`` install in the ``holon-base``
Dockerfile stage with its in-build offline smoke check, and the non-collision of ``hac-`` issue ids
with every identifier pattern the repository hygiene gate rejects. A final create/claim/close smoke
test runs only when a ``bd`` binary is on the PATH, fully offline in a temporary repository.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_repository_hygiene import find_hygiene_violations

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / ".beads" / "config.yaml"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DOCKERFILE_PATH = Path(__file__).resolve().parent.parent / "Dockerfile"
BAKE_PATH = Path(__file__).resolve().parent.parent / "docker-bake.hcl"

BD_TIMEOUT_SECONDS = 60


def _config_lines() -> list[str]:
    """The non-comment, non-blank lines of the tracked beads config, indentation preserved."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    return [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


class TestCommittedConfigShape:
    """The tracked ``.beads/config.yaml`` matches the decisions the intent fixed."""

    def test_config_exists(self) -> None:
        assert CONFIG_PATH.is_file(), f"missing tracked tracker config at {CONFIG_PATH}"

    def test_issue_prefix_is_hac(self) -> None:
        assert "issue_prefix: hac" in _config_lines(), "issue_prefix must be exactly 'hac' (rendered ids are hac-...)"

    def test_dolt_is_local_only(self) -> None:
        lines = _config_lines()
        assert "dolt:" in lines, "config must carry a dolt section"
        assert "  local-only: true" in lines, "dolt.local-only must be true so sandboxes never attempt a remote"
        assert lines.index("  local-only: true") > lines.index("dolt:")

    def test_node_id_is_never_committed(self) -> None:
        assert not any(line.lstrip().startswith(("node_id", "node-id")) for line in _config_lines()), (
            "node_id is per-clone machine state and must never live in the tracked config"
        )


def test_gitignore_excludes_embedded_dolt_database() -> None:
    entries = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    assert ".beads/embeddeddolt/" in entries, "the embedded Dolt database directory must be gitignored"


class TestDockerfilePin:
    """Every image built from holon-base ships the pinned, build-time-smoke-checked bd binary."""

    def test_bd_version_arg_is_a_concrete_pin(self) -> None:
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        match = re.search(r"^ARG BD_VERSION=(\S+)$", content, flags=re.MULTILINE)
        assert match, "Dockerfile must declare a default ARG BD_VERSION"
        assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", match.group(1)), (
            f"BD_VERSION default must be a concrete release, not {match.group(1)!r}"
        )

    def _holon_base_stage(self) -> str:
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        stage = content[content.index("AS holon-base") :]
        next_stage = stage.find("\nFROM ")
        return stage[:next_stage] if next_stage != -1 else stage

    def test_bd_is_installed_in_holon_base_with_checksum_verification(self) -> None:
        stage = self._holon_base_stage()
        assert "ARG BD_VERSION" in stage, "the stage must redeclare ARG BD_VERSION to consume the global pin"
        assert 'beads_${BD_VERSION}_linux_${ARCH}.tar.gz"' in stage, "install must download the pinned linux artefact"
        assert "sha256sum -c" in stage, "install must verify the artefact against the release checksums or fail"
        assert "/usr/local/bin/bd" in stage, "bd must land on the system PATH of every image"

    def test_build_runs_offline_version_smoke_check(self) -> None:
        assert re.search(r"&& \\\n\s+bd version$", self._holon_base_stage(), flags=re.MULTILINE), (
            "the install layer must end with an in-build 'bd version' smoke check"
        )

    def test_bake_file_exposes_bd_version(self) -> None:
        bake = BAKE_PATH.read_text(encoding="utf-8")
        assert 'variable "BD_VERSION"' in bake, "docker-bake.hcl must surface BD_VERSION as an overridable variable"
        assert "BD_VERSION = BD_VERSION" in bake, "the common target must forward a non-empty BD_VERSION override"


class TestHygieneNonCollision:
    """``hac-`` issue ids and the tracked tracker files are invisible to the hygiene gate."""

    @pytest.mark.parametrize(
        "line",
        [
            "hac-1",
            "hac-0042",
            "hac-1a2b",
            "closing hac-1a2b after review",
            "blocked on hac-0042 and hac-7",
        ],
    )
    def test_hac_ids_match_no_forbidden_pattern(self, line: str) -> None:
        assert find_hygiene_violations("docs/example.md", line) == []

    @pytest.mark.parametrize("path", [".beads/config.yaml", "docs/issue_tracking.md"])
    def test_tracked_tracker_surface_scans_clean(self, path: str) -> None:
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert find_hygiene_violations(path, text) == []


def _run_bd(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bd", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=BD_TIMEOUT_SECONDS,
    )


@pytest.mark.skipif(shutil.which("bd") is None, reason="no bd binary on PATH; the pinned install lives in the images")
def test_offline_create_claim_close_roundtrip(tmp_path: Path) -> None:
    """With a bd binary present, one issue can be created, claimed and closed fully offline."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "HOME": str(home), "BD_DOLT_LOCAL_ONLY": "true"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=BD_TIMEOUT_SECONDS)

    init = _run_bd(
        ["init", "--prefix", "hac", "--non-interactive", "--skip-hooks", "--skip-agents", "--quiet"], repo, env
    )
    assert init.returncode == 0, f"bd init failed offline: {init.stdout}\n{init.stderr}"

    created = _run_bd(["create", "offline lifecycle smoke issue"], repo, env)
    assert created.returncode == 0, f"bd create failed offline: {created.stdout}\n{created.stderr}"
    id_match = re.search(r"\bhac-[0-9a-zA-Z.-]+\b", created.stdout + created.stderr)
    assert id_match, f"bd create reported no hac- issue id: {created.stdout}\n{created.stderr}"
    issue_id = id_match.group(0)

    claimed = _run_bd(["update", issue_id, "--claim"], repo, env)
    assert claimed.returncode == 0, f"bd update --claim failed offline: {claimed.stdout}\n{claimed.stderr}"

    closed = _run_bd(["close", issue_id, "-r", "offline smoke complete"], repo, env)
    assert closed.returncode == 0, f"bd close failed offline: {closed.stdout}\n{closed.stderr}"
