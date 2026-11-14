"""Parsing, normalising, matching and scoring of the planner's touch set (touch_set.py)."""

from __future__ import annotations

import pytest
from sandbox_executor.touch_set import (
    TouchSet,
    TouchSetEntry,
    entries_overlap,
    entry_matches,
    files_in_set,
    normalise_touch_path,
    out_of_set_files,
    overlapping_entries,
    parse_touch_set_section,
    score_touch_set,
)

PLAN = """# Plan

## Plan Description & Strategy

Text.

## Touch Set

- `src/app/core.py`
- `src/app/big.py` symbols: \\_load, save_all
  - an indented detail bullet, ignored: `src/ignored.py`
- `docs/` (whole directory)
1. `tests/**/test_*.py`
- `./src/app/core.py`
- `plans/P-1.md`
- `holon-knowledge/ledger/plans.jsonl`
- `src/app/wrapped.py` symbols: first,
  second

## Step 1: Do it

- `src/not/in/the/section.py`
"""


def paths(touch_set: TouchSet | None) -> list[str]:
    assert touch_set is not None
    return [entry.path for entry in touch_set.entries]


def test_parses_top_level_items_until_the_next_section():
    touch_set = parse_touch_set_section(PLAN)
    assert paths(touch_set) == [
        "src/app/core.py",
        "src/app/big.py",
        "docs/",
        "tests/**/test_*.py",
        "src/app/wrapped.py",
    ]


def test_parses_symbols_including_prettier_escapes_and_wrapped_lines():
    touch_set = parse_touch_set_section(PLAN)
    assert touch_set is not None
    symbols = {entry.path: entry.symbols for entry in touch_set.entries}
    assert symbols["src/app/big.py"] == ("_load", "save_all")
    assert symbols["src/app/wrapped.py"] == ("first", "second")
    assert symbols["src/app/core.py"] == ()


def test_missing_section_gives_none():
    assert parse_touch_set_section("# Plan\n\n## Plan Description & Strategy\n\n- `src/a.py`\n") is None


def test_level_three_heading_is_not_the_section():
    assert parse_touch_set_section("### Touch Set\n\n- `src/a.py`\n") is None


@pytest.mark.parametrize(
    "item", ["none", "None.", "`n/a`", "NA", "nil", "no files", "not applicable", "_none_", "*none*", "**N/A**"]
)
def test_placeholder_first_item_gives_an_empty_set(item):
    assert parse_touch_set_section(f"## touch set\n\n- {item}\n") == TouchSet()


def test_placeholder_item_keeps_the_real_entries_after_it():
    assert paths(parse_touch_set_section("## Touch Set\n\n- none\n- `src/a.py`\n")) == ["src/a.py"]


def test_item_without_backticks_takes_the_first_token():
    assert paths(parse_touch_set_section("## Touch Set\n\n- src/a.py the core module\n")) == ["src/a.py"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./src/a.py", "src/a.py"),
        ("/src/a.py", "src/a.py"),
        ("  `src/a.py`  ", "src/a.py"),
        ("src\\app\\a.py", "src/app/a.py"),
        ("old/a.py => new/a.py", "new/a.py"),
        ("src/{old => new}/a.py", "src/new/a.py"),
        ("src/{ => sub}/a.py", "src/sub/a.py"),
        ("src/{sub => }/a.py", "src/a.py"),
    ],
)
def test_normalise_touch_path(raw, expected):
    assert normalise_touch_path(raw) == expected


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/a.py", "src/a.py", True),
        ("src/a.py", "src/a.pyc", False),
        ("src/", "src/x/y.py", True),
        ("src/", "srcx/y.py", False),
        ("apps/*/tests/", "apps/x/tests/flow/test_a.py", True),
        ("apps/*/tests/", "apps/x/src/a.py", False),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/x/a.py", False),
        ("src/**/a.py", "src/a.py", True),
        ("src/**/a.py", "src/x/y/a.py", True),
        ("src/**", "src/x/y.py", True),
        ("**/test_*.py", "tests/flow/test_a.py", True),
        ("src/?.py", "src/a.py", True),
        ("src/[ab].py", "src/b.py", True),
        ("src/[!ab].py", "src/b.py", False),
        ("src/[a-c].py", "src/b.py", True),
        ("src/[a-c].py", "src/-.py", False),
        ("src/[!a-c].py", "src/d.py", True),
        ("src/[c-a].py", "src/-.py", True),
        ("src/[!].py", "src/[!].py", True),
        ("src/[!].py", "src/a.py", False),
        ("src/a.py", "./src/a.py", True),
        ("app/[id]/page.tsx", "app/[id]/page.tsx", True),
        ("app/[id]/page.tsx", "app/i/page.tsx", True),
    ],
)
def test_entry_matches(pattern, path, expected):
    assert entry_matches(TouchSetEntry(pattern), path) is expected


def test_score_touch_set_hand_computed():
    touch_set = TouchSet(
        (TouchSetEntry("src/a.py"), TouchSetEntry("docs/"), TouchSetEntry("src/unused.py"), TouchSetEntry("tests/*.py"))
    )
    changed = ["src/a.py", "docs/x.md", "docs/y.md", "src/extra.py", "executions/E-1.md", "plans/P-1.md"]
    score = score_touch_set(touch_set, changed)
    # 2 of 4 entries matched; 3 of 4 non-metadata changed files covered.
    assert score.precision == 0.5
    assert score.recall == 0.75
    assert score.unpredicted_paths == ("src/extra.py",)
    assert score.unused_entries == ("src/unused.py", "tests/*.py")
    assert score.to_dict() == {
        "precision": 0.5,
        "recall": 0.75,
        "unpredicted_paths": ["src/extra.py"],
        "unused_entries": ["src/unused.py", "tests/*.py"],
    }


def test_score_with_zero_denominators():
    assert score_touch_set(TouchSet(), ["src/a.py"]).precision is None
    assert score_touch_set(TouchSet(), ["src/a.py"]).recall == 0.0
    metadata_only = score_touch_set(TouchSet((TouchSetEntry("src/a.py"),)), ["plans/P-1.md", "holon-knowledge/x"])
    assert metadata_only.recall is None
    assert metadata_only.precision == 0.0
    assert score_touch_set(TouchSet((TouchSetEntry("src/a.py"),)), ["plans/{P-1.md => P-2.md}"]).recall is None


def test_row_round_trip():
    touch_set = parse_touch_set_section(PLAN)
    assert touch_set is not None
    row = touch_set.to_row()
    assert row[1] == {"path": "src/app/big.py", "symbols": ["_load", "save_all"]}
    assert row[0] == {"path": "src/app/core.py"}
    assert TouchSet.from_row(row) == touch_set


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ([], TouchSet()),
        (["./src/a.py", "docs/"], TouchSet((TouchSetEntry("src/a.py"), TouchSetEntry("docs/")))),
        ("src/a.py", None),
        ([{"symbols": ["x"]}], None),
        ([{"path": "src/a.py", "symbols": "x"}], None),
        ([3], None),
    ],
)
def test_from_row_is_tolerant(value, expected):
    assert TouchSet.from_row(value) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("src/app.py", "src/app.py", True),
        ("src/app.py", "src/other.py", False),
        ("src/app.py", "src/", True),
        ("src/app.py", "lib/", False),
        ("src/app.py", "src/*.py", True),
        ("src/pkg/app.py", "src/*.py", False),
        ("src/pkg/app.py", "src/**/*.py", True),
        ("src/", "src/pkg/", True),
        ("src/", "srcx/", False),
        ("src/pkg/", "src/**", True),
        ("docs/", "src/**", False),
        ("src/a*.py", "src/b*.py", False),  # neither literal prefix starts the other
        ("src/*.md", "src/b*.py", True),  # conservative: the literal prefix src/ starts src/b
        ("src/a/*.py", "src/b/*.py", False),
        ("apps/*/tests/", "apps/web/", True),
        ("plans/P-1.md", "plans/", False),  # metadata never overlaps
        ("holon-knowledge/", "holon-knowledge/ledger/plans.jsonl", False),
    ],
)
def test_entries_overlap(a, b, expected):
    assert entries_overlap(TouchSetEntry(a), TouchSetEntry(b)) is expected
    assert entries_overlap(TouchSetEntry(b), TouchSetEntry(a)) is expected


def test_overlapping_entries_lists_every_pair():
    own = TouchSet((TouchSetEntry("src/"), TouchSetEntry("docs/a.md")))
    other = TouchSet((TouchSetEntry("src/app.py"), TouchSetEntry("lib/"), TouchSetEntry("docs/**")))
    assert overlapping_entries(own, other) == [("src/", "src/app.py"), ("docs/a.md", "docs/**")]


def test_files_in_set_drops_metadata_and_duplicates():
    touch_set = TouchSet((TouchSetEntry("src/"), TouchSetEntry("plans/")))
    changed = ["./src/app.py", "src/app.py", "lib/x.py", "plans/P-1.md", "src/{old => new}.py"]
    assert files_in_set(touch_set, changed) == ["src/app.py", "src/new.py"]


def test_out_of_set_files_ignores_metadata():
    touch_set = TouchSet((TouchSetEntry("src/*.py"),))
    changed = ["src/app.py", "src/pkg/deep.py", "executions/E-1.md", "holon-knowledge/ledger/executions.jsonl"]
    assert out_of_set_files(touch_set, changed) == ["src/pkg/deep.py"]
    assert out_of_set_files(TouchSet(), ["plans/P-1.md"]) == []
