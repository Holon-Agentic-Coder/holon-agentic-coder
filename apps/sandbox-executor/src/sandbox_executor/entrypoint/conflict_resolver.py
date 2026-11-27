from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox_executor.agent_runner import get_runner, get_workspace_dir
from sandbox_executor.budgets import load_budgets
from sandbox_executor.entrypoint.conflict_resolver_verify import AGENT_FAILED, Verdict, run_cmd, verify_resolution
from sandbox_executor.entrypoint.planner import render_placeholders
from sandbox_executor.plan_paths import plan_file_for_branch
from sandbox_executor.resolver_result import bounded_output_tail
from sandbox_executor.turns import run_turn

TEMPLATE_PATH = os.path.join("holon-config", "prompts", "conflict_resolver.template.md")
DECISION_PATH = os.path.join(".holon", "conflict-resolver", "decision.txt")
TEXT_MAX_CHARS = 20_000
_USAGE_LIMIT = re.compile(r"session limit|usage limit|rate limit", re.IGNORECASE)
_FALLBACK_TEMPLATE = (
    "Resolve every conflict marker in only these files, keeping both sides' behaviour, without any git write; "
    "run lint and tests in the foreground; write `CANNOT_DECIDE: <reason>` to `{decision_file}` if the sides are "
    "incompatible.\n\n{conflict_paths}\n\n{conflict_files}\n"
)


@dataclass(frozen=True)
class ResolverInput:
    paths: tuple[str, ...]
    merge_base: str
    ours_sha: str
    theirs_sha: str
    target_ref: str
    intent: dict[str, Any]
    plan_path: str
    tier: str

    @classmethod
    def from_file(cls, path: str) -> ResolverInput:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read the conflict resolver input '{path}': {exc}") from exc
        paths = data.get("paths") if isinstance(data, dict) else None
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            raise ValueError("the conflict resolver input names no conflict paths")
        intent = data.get("intent")
        return cls(
            paths=tuple(paths),
            merge_base=str(data.get("merge_base") or ""),
            ours_sha=str(data.get("ours_sha") or "HEAD"),
            theirs_sha=str(data.get("theirs_sha") or "MERGE_HEAD"),
            target_ref=str(data.get("target_ref") or ""),
            intent=intent if isinstance(intent, dict) else {},
            plan_path=str(data.get("plan_path") or ""),
            tier=str(data.get("tier") or ""),
        )


def _bounded(text: str, limit: int = TEXT_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[... truncated ...]"


# why: -c safe.directory trusts the mount without writing any git config
def _git_text(repo_dir: str, *args: str) -> str:
    result = run_cmd(["git", "-c", f"safe.directory={repo_dir}", *args], cwd=repo_dir)
    return result.stdout if result.returncode == 0 and isinstance(result.stdout, str) else ""


def _side_diff(repo_dir: str, request: ResolverInput, side: str) -> str:
    if not request.merge_base:
        return "(merge base unknown)"
    diff = _git_text(repo_dir, "diff", f"{request.merge_base}..{side}", "--", *request.paths)
    return _bounded(diff) or "(no change)"


def _conflict_files(repo_dir: str, paths: tuple[str, ...]) -> str:
    blocks = []
    for path in paths:
        try:
            body = _bounded((Path(repo_dir) / path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            body = "(absent from the working tree: one side deleted it)"
        blocks.append(f"### `{path}`\n\n````text\n{body}\n````")
    return "\n\n".join(blocks)


def _plan_content(repo_dir: str, request: ResolverInput) -> str:
    plan_path = request.plan_path or plan_file_for_branch(str(request.intent.get("branch") or ""))
    try:
        return _bounded((Path(repo_dir) / plan_path).read_text(encoding="utf-8")) if plan_path else "(no plan)"
    except OSError:
        return "(no plan)"


def build_prompt(repo_dir: str, request: ResolverInput, decision_file: str) -> str:
    try:
        template = (Path(repo_dir) / TEMPLATE_PATH).read_text(encoding="utf-8")
    except OSError:
        template = _FALLBACK_TEMPLATE
    values = {
        "repo_dir": repo_dir,
        "tier": request.tier,
        "ours_sha": request.ours_sha,
        "theirs_sha": request.theirs_sha,
        "merge_base": request.merge_base or "unknown",
        "target_ref": request.target_ref or "the target",
        "decision_file": decision_file,
        "conflict_paths": "\n".join(f"- `{path}`" for path in request.paths),
        "conflict_files": _conflict_files(repo_dir, request.paths),
        "ours_diff": _side_diff(repo_dir, request, request.ours_sha),
        "theirs_diff": _side_diff(repo_dir, request, request.theirs_sha),
        "intent_json": json.dumps(request.intent, indent=2),
        "plan_content": _plan_content(repo_dir, request),
    }
    return render_placeholders(template, values)


def run_agent_turn(repo_dir: str, runner_name: str, model: str, prompt: str) -> Verdict | None:
    runner = get_runner(runner_name)
    if callable(getattr(runner, "build_command", None)):
        cmd = runner.build_command(prompt, model)
    elif callable(getattr(runner, "compose_cmd", None)):
        cmd = runner.compose_cmd(model, prompt)
    else:
        return Verdict(AGENT_FAILED, reason=f"runner '{runner_name}' cannot build an agent command")
    outcome = run_turn(cmd, cwd=repo_dir, turn_budgets=load_budgets(os.environ).turn)
    output = f"{getattr(outcome, 'stdout', '') or ''}\n{getattr(outcome, 'stderr', '') or ''}"
    breach = getattr(outcome, "breach", None)
    code = getattr(outcome, "returncode", getattr(outcome, "exit_code", 0))
    if breach is not None:
        return Verdict(AGENT_FAILED, reason=f"agent turn timed out: {breach}", output_tail=bounded_output_tail(output))
    if code != 0:
        label = "usage limit reached" if _USAGE_LIMIT.search(output) else f"exited with code {code}"
        return Verdict(AGENT_FAILED, reason=f"agent turn {label}", output_tail=bounded_output_tail(output))
    return None


def resolve(repo_dir: str, request: ResolverInput, runner_name: str, model: str) -> Verdict:
    decision_file = os.path.join(repo_dir, DECISION_PATH)
    os.makedirs(os.path.dirname(decision_file), exist_ok=True)
    if os.path.exists(decision_file):
        os.remove(decision_file)
    failure = run_agent_turn(repo_dir, runner_name, model, build_prompt(repo_dir, request, decision_file))
    return failure or verify_resolution(repo_dir, decision_file)


def write_result(result_file: str, verdict: Verdict) -> bool:
    try:
        Path(result_file).write_text(json.dumps(verdict.to_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Error: could not write result file '{result_file}': {exc}", file=sys.stderr)
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve the code conflicts of an in-progress merge.")
    parser.add_argument("input_file", help="Role input JSON written by the host (may be the result file itself).")
    parser.add_argument("runner", help="Agent runner, for example claude-agent.")
    parser.add_argument("model", help="Model of this tier.")
    parser.add_argument("--result-file", required=True, help="Where to write the verdict JSON.")
    parser.add_argument("--workspace", default=None, help="Worktree to work in (default: the mounted workspace).")
    return parser.parse_args(argv)


# why: exit 0 whenever a verdict was written so the host decides from the verdict
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_dir = args.workspace or get_workspace_dir()
    try:
        request = ResolverInput.from_file(args.input_file)
    except ValueError as exc:
        verdict = Verdict(AGENT_FAILED, reason=bounded_output_tail(str(exc)))
    else:
        verdict = resolve(repo_dir, request, args.runner, args.model)
    print(f"Conflict resolver verdict: {verdict.verdict} ({verdict.reason})", file=sys.stderr)
    return 0 if write_result(args.result_file, verdict) else 1


if __name__ == "__main__":
    sys.exit(main())
