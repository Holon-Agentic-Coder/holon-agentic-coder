from typing import Any


def _extract_porcelain_path(porcelain_line: str) -> str:
    path_entry = porcelain_line[2:].strip()
    if " -> " in path_entry:
        path_entry = path_entry.split(" -> ")[1].strip()
    return path_entry.strip("\"'")


def changed_markdown_files(repo: Any) -> list[str]:
    md_files: list[str] = []
    status_res = repo.run(["status", "--porcelain"], check=False)
    if status_res.returncode != 0:
        return md_files
    for raw_line in status_res.stdout.splitlines():
        porcelain_line = raw_line.strip()
        if not porcelain_line or len(porcelain_line) < 3:
            continue
        file_path = _extract_porcelain_path(porcelain_line)
        if file_path.endswith(".md"):
            md_files.append(file_path)
    return md_files
