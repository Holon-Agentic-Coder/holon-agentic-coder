from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from sandbox_executor.flow.github.adapter import GitHubAdapter
from sandbox_executor.flow.github.ci_head_checks import HeadCheck, RunRerunner, is_cancelled


def gh_run_rerunner(
    repo_dir: str, proxy_env: Mapping[str, str] | None = None, github: GitHubAdapter | None = None
) -> RunRerunner:
    adapter = github or GitHubAdapter(repo_dir, dict(proxy_env) if proxy_env else None)

    def rerun(run_id: str) -> bool:
        try:
            return adapter.rerun_run(run_id)
        except Exception:
            return False

    return rerun


@dataclass
class CancelledReruns:
    dispatch: RunRerunner
    log: Callable[[str], None]
    run_ids: list[str] = field(default_factory=list)
    seen_check_runs: set[int] = field(default_factory=set)

    def retrigger(self, checks: list[HeadCheck]) -> str | None:
        seen = self.seen_check_runs
        cancelled = [c for c in checks if is_cancelled(c) and int(c.get("check_run_id") or 0) not in seen]
        unrunnable = [str(c.get("name")) for c in cancelled if c.get("run_id") is None]
        if unrunnable:
            return f"cancelled check {', '.join(unrunnable)} has no Actions run to re-trigger"
        run_ids = list(dict.fromkeys(str(check["run_id"]) for check in cancelled))
        again = [run_id for run_id in run_ids if run_id in self.run_ids]
        # why: a cancelled or stale run is never a pass, so a run cancelled again after its one re-trigger fails closed
        if again:
            return f"run {', '.join(again)} was cancelled again after its re-trigger (cancelled twice)"
        self.seen_check_runs.update(int(check.get("check_run_id") or 0) for check in cancelled)
        for run_id in run_ids:
            self.log(f"Merged-head CI: re-triggering cancelled run {run_id}.")
            if not self.dispatch(run_id):
                return f"re-triggering cancelled run {run_id} failed"
            self.run_ids.append(run_id)
        return None
