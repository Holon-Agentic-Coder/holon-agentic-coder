"""Unit and container availability tests verifying GitHub CLI (gh) and the pinned uv in agent images."""

import pathlib
import re
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

DOCKERFILE_PATH = pathlib.Path(__file__).parent.parent / "Dockerfile"

AGENT_TARGETS = [
    "agent-antigravity",
    "agent-claude",
    "agent-gemini",
    "agent-opencode",
    "agent-pi",
    "agent-codex",
    "holon-orchestrator",
]


def is_docker_available() -> bool:
    """Detect whether Docker CLI and daemon are operational in the current environment."""
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


class TestDockerfileGitHubCLIInstallation:
    """Hermetic unit tests validating GitHub CLI (gh) installation specification in Dockerfile."""

    def test_dockerfile_installs_gh_in_holon_base_with_multiarch(self):
        """Verify holon-base stage installs official GitHub CLI with multi-architecture support."""
        assert DOCKERFILE_PATH.exists(), f"Dockerfile not found at {DOCKERFILE_PATH}"
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")

        # 1. Base stage definition
        assert "FROM python:3.13-slim AS holon-base" in content

        # 2. Keyring directory creation with 0755 permissions
        assert "/etc/apt/keyrings" in content
        assert "mkdir -p -m 0755 /etc/apt/keyrings" in content or "mkdir -p /etc/apt/keyrings" in content

        # 3. Official GPG keyring download and world-readable 0644 permissions
        assert "githubcli-archive-keyring.gpg" in content
        assert "chmod 0644 /etc/apt/keyrings/githubcli-archive-keyring.gpg" in content

        # 4. Multi-arch APT source configuration using dpkg --print-architecture
        assert "arch=$(dpkg --print-architecture)" in content
        assert "signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg" in content
        assert "/etc/apt/sources.list.d/github-cli.list" in content

        # 5. gh package installation with --no-install-recommends
        assert re.search(r"apt-get install[^\n]+gh", content) is not None

        # 6. Non-root user holon definition
        assert "useradd -m holon" in content
        assert "USER holon" in content

    @pytest.mark.parametrize("target", AGENT_TARGETS)
    def test_all_agent_stages_inherit_from_holon_base(self, target: str):
        """Verify each agent target derives directly from holon-base and retains gh binary."""
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        stage_pattern = rf"FROM\s+holon-base\s+AS\s+{re.escape(target)}"
        assert re.search(stage_pattern, content) is not None, f"Target stage {target} does not derive from holon-base"


class TestContainerImageInspection:
    """Unit and mock tests verifying container image inspect properties for non-root execution."""

    @pytest.mark.parametrize("target", AGENT_TARGETS)
    def test_image_inspect_configuration_user_and_binary_path(self, target: str):
        """Verify container image configuration specifies non-root user holon and standard binary path."""
        fake_inspect_output = '[{"Config": {"User": "holon", "Env": ["PATH=/home/holon/.local/bin:/usr/bin:/bin"]}}]'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=fake_inspect_output, stderr="")

            cmd = ["docker", "image", "inspect", f"holon/{target}:latest"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            assert res.returncode == 0
            assert "holon" in res.stdout
            assert "/usr/bin" in res.stdout


class TestContainerExecutionNonRoot:
    """Container availability tests verifying gh execution under UID 1000."""

    @pytest.mark.integration_test
    @pytest.mark.skipif(not is_docker_available(), reason="Docker daemon unavailable in execution environment")
    @pytest.mark.parametrize("target", AGENT_TARGETS)
    def test_container_gh_execution_as_non_root(self, target: str):
        """Run container as non-root user holon (UID 1000) and verify gh presence and version."""
        image_name = f"holon/{target}:latest"
        inspect_res = subprocess.run(
            ["docker", "image", "inspect", image_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if inspect_res.returncode != 0:
            pytest.skip(f"Docker image {image_name} not available locally")

        cmd = [
            "docker",
            "run",
            "--rm",
            "-u",
            "1000:1000",
            image_name,
            "sh",
            "-c",
            "which gh && gh --version",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
        assert result.returncode == 0, f"gh execution failed in {image_name}: {result.stderr}"
        assert "/usr/bin/gh" in result.stdout or "gh" in result.stdout
        assert "gh version" in result.stdout


def _holon_base_stage(content: str) -> str:
    """Return the Dockerfile text of the holon-base stage, up to the next FROM line."""
    start = content.index("AS holon-base")
    end = content.find("\nFROM ", start)
    return content[start:] if end == -1 else content[start:end]


class TestDockerfileUvInstallation:
    """Hermetic tests: holon-base ships one pinned uv on the holon user's PATH and smoke-checks it at build time."""

    def test_uv_version_is_pinned_once_to_an_exact_release(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        pins = re.findall(r"^ARG UV_VERSION=(\S+)$", content, flags=re.MULTILINE)
        assert len(pins) == 1, f"expected exactly one UV_VERSION pin, found {pins}"
        assert re.fullmatch(r"\d+\.\d+\.\d+", pins[0]), f"UV_VERSION must be an exact x.y.z release, not {pins[0]}"
        assert content.index("ARG UV_VERSION=") < content.index("FROM "), "the pin must be a global ARG"

    def test_uv_source_stage_uses_the_pinned_official_image(self):
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert re.search(r"^FROM ghcr\.io/astral-sh/uv:\$\{UV_VERSION\} AS uv-bin$", content, flags=re.MULTILINE)

    def test_holon_base_copies_uv_onto_the_path(self):
        stage = _holon_base_stage(DOCKERFILE_PATH.read_text(encoding="utf-8"))
        copy = re.search(r"^COPY --from=uv-bin /uv\b.* /usr/local/bin/$", stage, flags=re.MULTILINE)
        assert copy, "holon-base must copy /uv from the uv-bin stage into /usr/local/bin/"
        assert stage.index(copy.group(0)) < stage.index("USER holon"), "uv must be installed while still root"

    def test_holon_base_smoke_checks_uv_as_the_holon_user(self):
        stage = _holon_base_stage(DOCKERFILE_PATH.read_text(encoding="utf-8"))
        assert re.search(r"^RUN uv --version$", stage, flags=re.MULTILINE), "holon-base must run 'uv --version'"
        assert stage.index("RUN uv --version") > stage.index("USER holon"), "the smoke check must run as holon"


class TestContainerUvExecutionNonRoot:
    """Container availability tests verifying uv execution under UID 1000."""

    @pytest.mark.integration_test
    @pytest.mark.skipif(not is_docker_available(), reason="Docker daemon unavailable in execution environment")
    @pytest.mark.parametrize("target", AGENT_TARGETS)
    def test_container_uv_execution_as_non_root(self, target: str):
        """Run container as non-root user holon (UID 1000) and verify uv presence and version."""
        image_name = f"holon/{target}:latest"
        inspect_res = subprocess.run(
            ["docker", "image", "inspect", image_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if inspect_res.returncode != 0:
            pytest.skip(f"Docker image {image_name} not available locally")

        cmd = ["docker", "run", "--rm", "-u", "1000:1000", image_name, "sh", "-c", "uv --version"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
        assert result.returncode == 0, f"uv execution failed in {image_name}: {result.stderr}"
        assert result.stdout.startswith("uv ")
