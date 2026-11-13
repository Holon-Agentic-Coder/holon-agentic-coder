"""List markdown files that are modified or untracked in a repo working tree."""

from typing import Any


def changed_markdown_files(repo: Any) -> list[str]:
    """Return paths of `.md` files reported by `git status --porcelain`."""
    md_files: list[str] = []
    status_res = repo.run(["status", "--porcelain"], check=False)
    if status_res.returncode != 0:
        return md_files
    for line in status_res.stdout.splitlines():
        line = line.strip()
        if not line or len(line) < 3:
            continue
        file_part = line[2:].strip()
        if " -> " in file_part:
            file_part = file_part.split(" -> ")[1].strip()
        file_part = file_part.strip("\"'")
        if file_part.endswith(".md"):
            md_files.append(file_part)
    return md_files
