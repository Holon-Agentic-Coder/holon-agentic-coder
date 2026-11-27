# why: the parser lives apart from the review_loop entrypoint to keep that entrypoint within its line budget
from __future__ import annotations

import argparse

from sandbox_executor.review_ensemble import DEFAULT_ENSEMBLE_SIZE, ENSEMBLE_SIZE_HELP


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review_loop",
        description="Holon Review Loop Agent container entrypoint.",
    )
    parser.add_argument("target", help="Target execution branch name, PR number, or PR URL.")
    parser.add_argument("pos_agent", nargs="?", default=None, help="Agent runner (positional fallback).")
    parser.add_argument("pos_model", nargs="?", default=None, help="Model name (positional fallback).")
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent runner to execute (default: the fallback runner, antigravity-agent).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name to pass to agent (default: the runner's default model).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum review loop iterations (default: 25).",
    )
    parser.add_argument(
        "--iteration",
        type=int,
        default=1,
        help=(
            "Label of the first pass (default: 1), used for the ledger review_iteration fields and the posted "
            "'Iteration {n}' heading. The flow engine owns the counter, saves it in the flow checkpoint "
            "(review_iterations_done), and on a resume from the review stage passes the continued count as this "
            "label so ledger review_iteration values and posted 'Iteration {n}' headings form one monotonic sequence "
            "across halts and resumes; it is never a loop limit and is never set by operators."
        ),
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        default=False,
        help=(
            "Run in resolver mode: read findings from --findings-file (or --result-file), adjudicate every finding "
            "to applied or rejected, verify with lint and tests before pushing, and write the resolver outcome JSON."
        ),
    )
    parser.add_argument(
        "--findings-file",
        default=None,
        help="Path to input findings JSON for --resolve mode (defaults to --result-file).",
    )
    parser.add_argument(
        "--ensemble-size",
        type=int,
        default=DEFAULT_ENSEMBLE_SIZE,
        help=ENSEMBLE_SIZE_HELP,
    )
    parser.add_argument(
        "--auto-pr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Automatically create PR if only a branch exists (default: True). The empty-diff guard runs either way; "
            "the exit code is 0 only when the consensus is approved and posted."
        ),
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        default=False,
        help="Skip pushing git commits or branches to remote.",
    )
    parser.add_argument(
        "--skip-post",
        action="store_true",
        default=False,
        help="Skip posting review consensus to GitHub.",
    )
    parser.add_argument(
        "--result-file",
        default=None,
        help="Path to write structured review outcome JSON for host transport.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run review evaluation without applying changes or git commits.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output review loop results in structured JSON format.",
    )
    parser.add_argument(
        "--token-reduce",
        action="store_true",
        default=False,
        help="Enable proxy-based token reduction.",
    )
    parser.add_argument(
        "--mitm-web",
        action="store_true",
        default=False,
        help="Enable mitmweb proxy dashboard.",
    )
    parser.add_argument(
        "--target-worktree",
        default=None,
        help="Target worktree directory override.",
    )
    parser.add_argument(
        "--review-paths",
        action="append",
        default=None,
        help="Comma-separated or repeated repository paths to scope a conflict-resolution re-review.",
    )
    parser.add_argument(
        "--delta-request",
        default=None,
        help="Worktree-relative JSON file of the host's scoped delta-review decision for this pass.",
    )
    return parser
