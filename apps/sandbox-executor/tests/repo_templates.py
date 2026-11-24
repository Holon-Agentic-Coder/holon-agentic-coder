"""Build fixture git repositories once and hand every test its own copy.

Two rules of use:

- A template is built once per session (per xdist worker) and is never written to: ``finish_template`` makes the
  tree read-only, so a test that commits in or pushes to the template fails loudly.
- A test only works in its own copy, made by ``copy_template``. The copy's remotes and settings that named the
  template (a remote URL, ``core.hooksPath``) name the copy, without a git process.
"""

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from tests.flow.helpers import _set_user_write


def remove_sample_hooks(root: Path) -> None:
    """Delete git's ``hooks/*.sample`` files under ``root``: they are never run, so no copy needs them."""
    for sample in root.glob("**/hooks/*.sample"):
        sample.unlink()


def finish_template(root: Path) -> Path:
    """Drop the sample hooks of a built template and make its tree read-only; returns ``root``."""
    remove_sample_hooks(root)
    _set_user_write(root, enabled=False)
    return root


def _git_dirs(root: Path) -> Iterable[Path]:
    """Every git directory under ``root``: a bare repository itself, or the ``.git`` of a clone."""
    for dirpath, dirnames, filenames in os.walk(root):
        if "HEAD" in filenames and "config" in filenames and "objects" in dirnames:
            dirnames.clear()
            yield Path(dirpath)


def _reroot_git_dir(git_dir: Path, template: Path, target: Path) -> None:
    """Make one copied git directory name ``target`` wherever it named ``template``."""
    if (git_dir / "worktrees").is_dir():
        raise ValueError(
            f"{template} has a linked worktree ({git_dir.relative_to(target)}/worktrees): it stores absolute paths "
            "that copy_template does not rewrite, so build this repository per test instead"
        )
    # FETCH_HEAD names the URL fetched from, which is a template path.
    (git_dir / "FETCH_HEAD").unlink(missing_ok=True)
    config = git_dir / "config"
    text = config.read_text(encoding="utf-8")
    # The longer spelling first: where the temporary directory is a symlink, one spelling ends with the other.
    template_roots = sorted({str(template), os.path.realpath(template)}, key=len, reverse=True)
    for template_root in template_roots:
        text = text.replace(template_root, str(target))
    config.write_text(text, encoding="utf-8")
    reread = config.read_text(encoding="utf-8")
    assert not any(root in reread for root in template_roots), f"{config} still names the shared template {template}"


def copy_template(template: Path, target: Path, names: Iterable[str] = ()) -> Path:
    """Copy the repositories of ``template`` into ``target`` and re-point them at the copy; returns ``target``.

    Args:
        template: Root of a template finished with ``finish_template``.
        target: Directory the copy goes to; created when missing. Entries it already has are left alone.
        names: Top-level directories of the template to copy; all of them when empty.

    Raises:
        ValueError: The template has a linked worktree.
    """
    target.mkdir(parents=True, exist_ok=True)
    for name in names or sorted(os.listdir(template)):
        copied = target / name
        shutil.copytree(template / name, copied, symlinks=True)
        _set_user_write(copied, enabled=True)
        for git_dir in _git_dirs(copied):
            _reroot_git_dir(git_dir, template, target)
    return target
