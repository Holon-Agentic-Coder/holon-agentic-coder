"""Import layering guard: the flow engine never imports the CLI, and the launcher imports neither of them.

Every import statement is collected from the AST, function-local ones included, and relative imports are resolved
against the importing module's package, so a deferred `from sandbox_executor.cli import x` or `from ...cli import y`
inside a function body cannot hide an edge. `sandbox_executor.flow.review.cli` is a flow module, not the CLI package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sandbox_executor.cli

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PACKAGE_DIR = SRC_DIR / "sandbox_executor"
CLI = "sandbox_executor.cli"
FLOW = "sandbox_executor.flow"

#: Names that left `cli/__init__.py` for `sandbox_executor.launcher`, or that `cli` imported only for that code; each
#: stays importable from `sandbox_executor.cli`.
MOVED_CLI_NAMES = (
    "AGENT_IMAGE_MAPPING",
    "BLOCKED_METADATA_HOSTS",
    "CLAUDE_CREDENTIAL_ENV_NAMES",
    "COMMAND_ECHO_ENV_NAMES",
    "CONTAINER_CA_BUNDLE_PATH",
    "CONTAINER_CA_DIR",
    "ENV_FILE_PREFIX",
    "FALLBACK_AGENT",
    "HOST_WORKTREE_ROLES",
    "INTENT_CREATOR_AGENT_ID",
    "INTENT_CREATOR_IMAGE",
    "INTENT_CREATOR_ROLE",
    "MITM_PROXY_CA_DIR",
    "NO_PROXY_HOSTS",
    "PROXY_ATTACH_TIMEOUT_SECONDS",
    "PROXY_CONNECT_TIMEOUT_SECONDS",
    "PROXY_LISTEN_PORT",
    "PROXY_READY_TIMEOUT_SECONDS",
    "RETIRED_ENV_VARS",
    "SANDBOX_UV_ENV",
    "SANDBOX_WORKSPACE_MOUNT",
    "SINKHOLE_IP",
    "_ENV_FILE_STALE_SECONDS",
    "_EXPLICIT_OPT_OUT_VALUES",
    "_SidecarState",
    "_TRUTHY_ENV_VALUES",
    "_URL_DELIMITER_SPLIT_RE",
    "_URL_ENV_NAME_RE",
    "_attach_external_proxy",
    "_build_proxy_envs",
    "_ca_mount_args",
    "_container_ca_path",
    "_ensure_network",
    "_find_git_root",
    "_gateway_host_args",
    "_mitm_proxy_ca_paths",
    "_proxy_gateway_url",
    "_proxy_host_port",
    "_published_loopback_port",
    "_run_docker",
    "_sidecar_state",
    "_token_reduce_opt_in",
    "_url_env_credential_parts",
    "_wait_for_proxy",
    "antigravity_session_mounts",
    "build_network_isolation_args",
    "carries_url_password",
    "detect_holon_coherence",
    "dispatch_role_agent",
    "env_file_dir",
    "find_github_token",
    "generate_root_ca",
    "get_agent_session_mounts",
    "get_host_proxy_envs",
    "get_pi_mounts",
    "get_token_reduction_mounts_and_envs",
    "git_flow",
    "image_for_agent",
    "is_secret_env_item",
    "is_secret_env_name",
    "print_encodable",
    "redact_agent_secrets",
    "redact_cli_error",
    "redact_command",
    "redact_env_literals",
    "resolve_claude_credential",
    "resolve_role_image",
    "run_docker_container",
    "setup_token_reduction_proxy",
    "stage_budget_handdown_env",
    "starts_with_url_authority",
    "sweep_stale_env_files",
    "teardown_token_reduction_proxy",
    "trusted_url_authority",
    "write_env_file",
)


def module_name(path: Path) -> str:
    """The dotted module name of a source file under `SRC_DIR` (a package `__init__.py` names the package)."""
    parts = list(path.relative_to(SRC_DIR).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(source: str, module: str, is_package: bool) -> list[tuple[int, str]]:
    """Every `(line, dotted target)` imported anywhere in `source`, relative imports resolved.

    Args:
        source: Python source text.
        module: Dotted name of the module the source belongs to.
        is_package: True when the source is a package `__init__.py`.

    Returns:
        One entry per imported module, plus `<module>.<alias>` for each `from <module> import <alias>`, because the
        alias may itself be a submodule.
    """
    package = module if is_package else module.rpartition(".")[0]
    targets: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node, package)
            targets.append((node.lineno, base))
            targets.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
    return targets


def _resolve_from(node: ast.ImportFrom, package: str) -> str:
    """The absolute module named by a `from ... import` statement."""
    if node.level == 0:
        return node.module or ""
    anchor = package.split(".")[: len(package.split(".")) - (node.level - 1)]
    return ".".join([*anchor, node.module] if node.module else anchor)


def is_within(target: str, package: str) -> bool:
    """True when `target` is `package` itself or one of its submodules."""
    return target == package or target.startswith(f"{package}.")


def forbidden_edges(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    """`file:line -> target` for each import under `root` that reaches one of the `forbidden` packages."""
    edges: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for line, target in imported_modules(source, module_name(path), path.name == "__init__.py"):
            if any(is_within(target, package) for package in forbidden):
                edges.append(f"{path.relative_to(SRC_DIR)}:{line} -> {target}")
    return sorted(set(edges))


def test_flow_never_imports_the_cli_package() -> None:
    edges = forbidden_edges(PACKAGE_DIR / "flow", (CLI,))
    assert edges == [], "sandbox_executor.flow imports sandbox_executor.cli:\n" + "\n".join(edges)


def test_flow_names_the_cli_package_in_no_string_literal() -> None:
    """A dynamic `importlib.import_module("sandbox_executor.cli")` would bypass the AST walk."""
    hits = [
        str(path.relative_to(SRC_DIR))
        for path in sorted((PACKAGE_DIR / "flow").rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and is_within(node.value, CLI)
    ]
    assert hits == []


@pytest.mark.parametrize("forbidden", [CLI, FLOW])
def test_launcher_imports_neither_the_cli_nor_the_flow_engine(forbidden: str) -> None:
    edges = forbidden_edges(PACKAGE_DIR / "launcher", (forbidden,))
    assert edges == [], f"sandbox_executor.launcher imports {forbidden}:\n" + "\n".join(edges)


@pytest.mark.parametrize("name", MOVED_CLI_NAMES)
def test_moved_names_stay_importable_from_the_cli(name: str) -> None:
    assert hasattr(sandbox_executor.cli, name)


def test_walker_finds_function_local_and_relative_cli_imports() -> None:
    source = (
        "def f():\n"
        "    from sandbox_executor.cli import dispatch_role_agent\n"
        "def g():\n"
        "    from ...cli import get_host_proxy_envs\n"
        "def h():\n"
        "    from sandbox_executor import cli\n"
        "import sandbox_executor.cli.options\n"
    )
    targets = imported_modules(source, "sandbox_executor.flow.review.loop", is_package=False)
    hits = sorted({(line, target) for line, target in targets if is_within(target, CLI)})
    assert hits == [
        (2, "sandbox_executor.cli"),
        (2, "sandbox_executor.cli.dispatch_role_agent"),
        (4, "sandbox_executor.cli"),
        (4, "sandbox_executor.cli.get_host_proxy_envs"),
        (6, "sandbox_executor.cli"),
        (7, "sandbox_executor.cli.options"),
    ]


def test_walker_does_not_mistake_the_flow_review_cli_module_for_the_cli_package() -> None:
    source = "from . import cli\nfrom .cli import run_review_loop_cli\nimport sandbox_executor.flow.review.cli\n"
    targets = imported_modules(source, "sandbox_executor.flow.review", is_package=True)
    assert [target for _, target in targets if is_within(target, CLI)] == []
    assert (2, "sandbox_executor.flow.review.cli") in targets
