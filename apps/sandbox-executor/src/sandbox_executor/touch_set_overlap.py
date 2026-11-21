"""Whether two flows touch a common file, decided on concrete files: the Stage 3 admission overlap rule.

Both sides are reduced to file paths against the files that exist on the base branch (``base_files``). A literal entry
names itself, whether it exists or not; a folder or glob entry names only the base files it matches, never a path that
is absent on the base. So a new file overlaps only an identical literal path in the other set, and folder or glob
entries meet only on existing files. Metadata paths (``git.is_metadata_path``) never overlap.
Pure logic with no I/O; :mod:`flow.base_files` lists the base files and :mod:`flow.admission_decision` applies the rule.
"""

from __future__ import annotations

from collections.abc import Iterable

from sandbox_executor.git import is_metadata_path
from sandbox_executor.touch_set import TouchSet, TouchSetEntry, code_paths, entry_matches, is_literal_entry


def expand_entry(entry: TouchSetEntry, base_files: frozenset[str]) -> frozenset[str]:
    """The files an entry names: a literal itself (dropped when metadata), a folder or glob its matching base files."""
    if is_metadata_path(entry.path):
        return frozenset()
    literal = {entry.path} if is_literal_entry(entry) else set()
    if literal and "[" not in entry.path:
        return frozenset(literal)
    # A bracket class may be a glob (src/[ab].py) or part of a literal path (app/[id]/page.tsx): keep both readings.
    return frozenset(literal.union(path for path in base_files if entry_matches(entry, path)))


def expand_touch_set(touch_set: TouchSet, base_files: frozenset[str]) -> frozenset[str]:
    """The union of the files every entry of ``touch_set`` names (:func:`expand_entry`)."""
    return frozenset().union(*(expand_entry(entry, base_files) for entry in touch_set.entries))


def declared_overlap(own: TouchSet, other: TouchSet, base_files: frozenset[str]) -> list[str]:
    """The sorted files both declared touch sets name (a local flow or an older waiter against this plan)."""
    return sorted(expand_touch_set(own, base_files) & expand_touch_set(other, base_files))


def changed_files_overlap(own: TouchSet, changed_files: Iterable[str], base_files: frozenset[str]) -> list[str]:
    """The sorted changed files of a pull request ``own`` touches.

    A changed file present on the base overlaps when some own entry matches it; one absent from the base (an added
    file) overlaps only an identical own literal entry.
    """
    literals = {entry.path for entry in own.entries if is_literal_entry(entry)}
    return sorted(
        path
        for path in code_paths(list(changed_files))
        if (path in literals if path not in base_files else any(entry_matches(entry, path) for entry in own.entries))
    )


def broad_entries(touch_set: TouchSet) -> list[str]:
    """The folder or glob entry paths of a touch set, in their declared order (the Stage 2 advisory warning)."""
    return [entry.path for entry in touch_set.entries if not is_literal_entry(entry)]
