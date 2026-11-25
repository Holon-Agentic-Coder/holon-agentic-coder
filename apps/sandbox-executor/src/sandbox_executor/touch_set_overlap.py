from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sandbox_executor.git import is_metadata_path
from sandbox_executor.touch_set import TouchSet, TouchSetEntry, code_paths, entry_matches, is_literal_entry


def expand_entry(entry: TouchSetEntry, base_files: frozenset[str]) -> frozenset[str]:
    if is_metadata_path(entry.path):
        return frozenset()
    literal_paths = {entry.path} if is_literal_entry(entry) else set()
    has_bracket_glob_ambiguity = "[" in entry.path
    if literal_paths and not has_bracket_glob_ambiguity:
        return frozenset(literal_paths)
    matching_base_paths = (path for path in base_files if entry_matches(entry, path))
    return frozenset(literal_paths.union(matching_base_paths))


def expand_touch_set(touch_set: TouchSet, base_files: frozenset[str]) -> frozenset[str]:
    return frozenset().union(*(expand_entry(entry, base_files) for entry in touch_set.entries))


def declared_overlap(own: TouchSet, other: TouchSet, base_files: frozenset[str]) -> list[str]:
    return sorted(expand_touch_set(own, base_files) & expand_touch_set(other, base_files))


def _path_overlaps_own_entries(
    path: str,
    own: TouchSet,
    own_literal_paths: set[str],
    base_files: frozenset[str],
) -> bool:
    if path not in base_files:
        return path in own_literal_paths
    return any(entry_matches(entry, path) for entry in own.entries)


def changed_files_overlap(own: TouchSet, changed_files: Iterable[str], base_files: frozenset[str]) -> list[str]:
    own_literal_paths = {entry.path for entry in own.entries if is_literal_entry(entry)}
    return sorted(
        path
        for path in code_paths(list(changed_files))
        if _path_overlaps_own_entries(path, own, own_literal_paths, base_files)
    )


@dataclass(frozen=True)
class TouchSetOnBase:
    touch_set: TouchSet
    base_files: frozenset[str]

    def overlap(self, other: TouchSet) -> list[str]:
        return declared_overlap(self.touch_set, other, self.base_files)


def broad_entries(touch_set: TouchSet) -> list[str]:
    return [entry.path for entry in touch_set.entries if not is_literal_entry(entry)]
