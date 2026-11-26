from __future__ import annotations

from sandbox_executor.git import GitRepository


def taken_child_ids(repo: GitRepository, parent_prefix: str, kind: str) -> set[str]:
    parent = parent_prefix.removesuffix("/_").rstrip("/")
    taken: set[str] = set()
    for root in (f"refs/heads/{parent}/", f"refs/remotes/origin/{parent}/"):
        res = repo.run(["for-each-ref", "--format=%(refname)", root], check=False)
        if res.returncode != 0:
            continue
        for ref in res.stdout.splitlines():
            child = ref.strip().removeprefix(root).split("/", 1)[0]
            if child.startswith(f"{kind}-"):
                taken.add(child)
    return taken
