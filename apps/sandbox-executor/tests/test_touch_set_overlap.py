from __future__ import annotations

import time

import pytest
from sandbox_executor.touch_set import TouchSet, TouchSetEntry
from sandbox_executor.touch_set_overlap import (
    TouchSetOnBase,
    broad_entries,
    changed_files_overlap,
    declared_overlap,
    expand_entry,
    expand_touch_set,
)

BASE = frozenset(
    {
        "src/app.py",
        "src/pkg/core.py",
        "docs/guide.md",
        "tests/test_old.py",
        "tests/flow/a/test_old.py",
        "apps/x/tests/test_old.py",
        "app/[id]/page.tsx",
        "src/a.py",
    }
)


def _set(*paths: str) -> TouchSet:
    return TouchSet(tuple(TouchSetEntry(path) for path in paths))


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("src/new.py", {"src/new.py"}),
        ("src/app.py", {"src/app.py"}),
        ("src/", {"src/app.py", "src/pkg/core.py", "src/a.py"}),
        ("tests/test_*.py", {"tests/test_old.py"}),
        ("tests/flow/**/test_*.py", {"tests/flow/a/test_old.py"}),
        ("lib/", set()),
        ("app/[id]/page.tsx", {"app/[id]/page.tsx"}),
        ("app/[new]/page.tsx", {"app/[new]/page.tsx"}),
        ("src/[ab].py", {"src/[ab].py", "src/a.py"}),
        ("plans/P-1.md", set()),
        ("holon-knowledge/", set()),
    ],
)
def test_expand_entry(entry, expected):
    assert expand_entry(TouchSetEntry(entry), BASE) == frozenset(expected)


def test_expand_touch_set_is_the_union():
    assert expand_touch_set(_set("docs/", "src/new.py"), BASE) == {"docs/guide.md", "src/new.py"}


@pytest.mark.parametrize(
    ("own", "other", "expected"),
    [
        (("src/new.py",), ("src/new.py",), ["src/new.py"]),
        (("src/app.py",), ("src/",), ["src/app.py"]),
        (("tests/test_*.py", "tests/flow/**/test_*.py"), ("tests/test_new.py", "tests/flow/b/test_new.py"), []),
        (("apps/x/tests/",), ("apps/x/tests/test_new.py",), []),
        (("apps/x/tests/",), ("apps/x/tests/test_old.py",), ["apps/x/tests/test_old.py"]),
        (("src/",), ("src/pkg/",), ["src/pkg/core.py"]),
        (("lib/",), ("lib/",), []),
        (("plans/",), ("plans/",), []),
    ],
)
def test_declared_overlap(own, other, expected):
    assert declared_overlap(_set(*own), _set(*other), BASE) == expected
    assert declared_overlap(_set(*other), _set(*own), BASE) == expected


def test_touch_set_on_base_overlaps_like_declared_overlap():
    own = TouchSetOnBase(_set("src/", "src/new.py"), BASE)
    assert own.overlap(_set("src/app.py", "src/new.py", "src/other_new.py")) == ["src/app.py", "src/new.py"]


@pytest.mark.parametrize(
    ("own", "changed", "expected"),
    [
        (("tests/test_*.py", "tests/flow/**/test_*.py"), ["tests/test_new.py", "tests/flow/a/test_new.py"], []),
        (("apps/x/tests/",), ["apps/x/tests/test_new.py"], []),
        (("apps/x/tests/",), ["apps/x/tests/test_old.py", "src/app.py"], ["apps/x/tests/test_old.py"]),
        (("src/new.py",), ["src/new.py"], ["src/new.py"]),
        (("app/[new]/page.tsx",), ["app/[new]/page.tsx"], ["app/[new]/page.tsx"]),
        (("src/",), ["./src/app.py", "src/app.py", "src/{a => pkg}/core.py"], ["src/app.py", "src/pkg/core.py"]),
        (("plans/",), ["plans/P-1.md"], []),
    ],
)
def test_changed_files_overlap(own, changed, expected):
    assert changed_files_overlap(_set(*own), changed, BASE) == expected


def test_broad_entries_lists_folders_and_globs_in_order():
    touch_set = _set("docs/", "src/app.py", "tests/test_*.py", "app/[id]/page.tsx", "a?.py")
    assert broad_entries(touch_set) == ["docs/", "tests/test_*.py", "a?.py"]
    assert broad_entries(_set("src/app.py")) == []


def test_expansion_against_a_large_base_is_fast():
    base = frozenset(f"pkg{n % 50}/sub{n % 7}/module_{n}.py" for n in range(5000))
    touch_set = _set("pkg1/", "pkg2/**/*.py", "pkg*/sub3/module_1*.py", "pkg3/sub1/module_3.py")
    started = time.perf_counter()
    expanded = expand_touch_set(touch_set, base)
    assert time.perf_counter() - started < 1.0
    assert "pkg1/sub1/module_1.py" in expanded and "pkg3/sub1/module_3.py" in expanded
