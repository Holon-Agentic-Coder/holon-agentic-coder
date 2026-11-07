"""Unit tests for CLI option parsing of --target-worktree and rejection of --repo-dir."""

import pytest

from sandbox_executor.cli import build_parser


def test_add_repo_dir_option_is_removed():
    """Verify that add_repo_dir_option does not exist in options or cli modules."""
    import sandbox_executor.cli as cli
    import sandbox_executor.cli.options as options

    assert not hasattr(options, "add_repo_dir_option")
    assert not hasattr(cli, "add_repo_dir_option")
    assert hasattr(options, "add_target_worktree_option")
    assert hasattr(cli, "add_target_worktree_option")


@pytest.mark.parametrize(
    ("cmd_prefix", "expected_default"),
    [
        (["flow", "intent.md"], "."),
        (["doctor"], "."),
        (["calibrate", "I-123/P-456/E-789/_"], "."),
        (["review", "HEAD"], "."),
        (["ledger", "query"], "."),
        (["promote"], "."),
        (["reconcile"], "."),
        (["record", "--action", "test-action"], "."),
        (["stats"], "."),
        (["veto", "list"], "."),
        (["veto", "lift", "--context", "test-context"], "."),
        (["consult"], "."),
        (["intent", "intent.md"], None),
        (["plan", "intent.md"], None),
        (["execute", "P-123"], None),
        (["review-loop", "HEAD"], "."),
        (["pr-review-loop", "123"], "."),
    ],
)
def test_target_worktree_option_parsing_and_defaults(cmd_prefix: list[str], expected_default: str | None):
    """Test that each subcommand parses --target-worktree correctly and assigns the expected default."""
    parser = build_parser()

    # 1. Parse with explicit --target-worktree
    args = parser.parse_args([*cmd_prefix, "--target-worktree", "/custom/worktree/path"])
    assert getattr(args, "target_worktree", None) == "/custom/worktree/path"

    # 2. Parse without --target-worktree, checking default
    args_default = parser.parse_args(cmd_prefix)
    assert getattr(args_default, "target_worktree", None) == expected_default


@pytest.mark.parametrize(
    "cmd_prefix",
    [
        ["flow", "intent.md"],
        ["doctor"],
        ["calibrate", "I-123/P-456/E-789/_"],
        ["review", "HEAD"],
        ["ledger", "query"],
        ["promote"],
        ["reconcile"],
        ["record", "--action", "test-action"],
        ["stats"],
        ["veto", "list"],
        ["veto", "lift", "--context", "test-context"],
        ["consult"],
        ["intent", "intent.md"],
        ["plan", "intent.md"],
        ["execute", "P-123"],
        ["review-loop", "HEAD"],
        ["pr-review-loop", "123"],
    ],
)
def test_obsolete_repo_dir_option_is_rejected(cmd_prefix: list[str]):
    """Test that passing --repo-dir to any command results in SystemExit code 2 (unrecognized argument)."""
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([*cmd_prefix, "--repo-dir", "/obsolete/path"])

    assert exc_info.value.code == 2
