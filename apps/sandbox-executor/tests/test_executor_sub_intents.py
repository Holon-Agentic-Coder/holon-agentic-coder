"""Tests for ``entrypoint/executor/sub_intents.py``: the decomposition decision and explicit sub-intent list parsing."""

import os

import pytest
from sandbox_executor.entrypoint import executor

from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401  (autouse fixture)
    _workspace_is_mounted,  # noqa: F401  (autouse fixture)
)

# --- Entropy budget handoff and explicit sub-intent lists (no fabricated child work) ---------------------------------

# The legacy capitalised plural heading, assembled from fragments so this file never spells it out.
LEGACY_HEADING_WORD = "Sub" + "-" + "Intents"


@pytest.mark.parametrize(
    ("plan_row", "intent_row", "expected"),
    [
        ({"entropy": 12.6, "entropy_budget": 16.0}, None, executor.DECISION_EXECUTE),
        ({"entropy": 12.6}, {"entropy_budget": 16.0}, executor.DECISION_EXECUTE),
        ({"entropy": 12.6}, {"entropy_budget": 5.0}, executor.DECISION_OVER_BUDGET),
        ({"entropy": 12.6, "entropy_budget": 5.0}, {"entropy_budget": 16.0}, executor.DECISION_OVER_BUDGET),
        ({"entropy": 12.6}, None, executor.DECISION_EXECUTE),
        ({"entropy": 16.0}, None, executor.DECISION_OVER_BUDGET),
        ({"entropy": None, "entropy_budget": None}, None, executor.DECISION_EXECUTE),
    ],
)
def test_should_decompose_uses_the_persisted_budget(plan_row, intent_row, expected):
    decision = executor.should_decompose(plan_row, "# Plan\nStandard plan without a list", intent_row)

    assert decision.decision == expected
    assert decision.sub_intents == []


def test_should_decompose_regression_intent_budget_16_with_entropy_12_6_executes():
    decision = executor.should_decompose({"plan_id": "P-1", "entropy": 12.6}, "# Plan\n", {"entropy_budget": 16})

    assert decision.decision == executor.DECISION_EXECUTE
    assert decision.entropy == 12.6
    assert decision.entropy_budget == 16.0


def test_should_decompose_over_budget_never_invents_child_work():
    decision = executor.should_decompose({"plan_id": "P-1", "entropy": 12.6, "entropy_budget": 5.0}, "# Plan\n")

    assert decision.decision == executor.DECISION_OVER_BUDGET
    assert decision.sub_intents == []


@pytest.mark.parametrize("heading", ["## Sub-intent list", "### Sub-intent list", f"## {LEGACY_HEADING_WORD}"])
@pytest.mark.parametrize("marker", ["- ", "* ", "1. "])
def test_should_decompose_honours_an_explicit_list(heading, marker):
    plan = (
        f"# Plan\n\n{heading}\n\n{marker}Create DB schema\n{marker}Build API routes\n"
        "\n## Step 1: Other\n\n- Not a child\n"
    )

    decision = executor.should_decompose({"entropy": 2.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_DECOMPOSED
    assert decision.sub_intents == [
        {"slug": "create-db-schema", "description": "Create DB schema", "goal": "Create DB schema"},
        {"slug": "build-api-routes", "description": "Build API routes", "goal": "Build API routes"},
    ]


def test_should_decompose_explicit_list_wins_over_budget_and_dedupes_slugs():
    plan = "# Plan\n\n## Sub-intent list\n\n- Write tests\n-   \n- Write tests\n"

    decision = executor.should_decompose({"entropy": 40.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_DECOMPOSED
    assert [item["slug"] for item in decision.sub_intents] == ["write-tests", "write-tests-2"]


def _section(body: str) -> str:
    """Wrap a body in a plan whose ``## Sub-intent list`` section is followed by a non-list section."""
    return f"# Plan\n\n## Sub-intent list\n\n{body}\n\n## Risks\n\n- not a child\n"


_NESTED_BODY = (
    "- Add the parser\n  - covers tokens\n  - covers errors\n- Add the CLI flag\n    wired into the argument parser"
)


_NESTED_EXPECTED = [
    "Add the parser\n- covers tokens\n- covers errors",
    "Add the CLI flag\nwired into the argument parser",
]


def _indented_body(indent: str) -> str:
    return (
        f"{indent}- Add the parser\n{indent}  - covers tokens\n{indent}\t- covers errors\n"
        f"{indent}* Add the CLI flag\n{indent}    1. wired in"
    )


_INDENTED_EXPECTED = ["Add the parser\n- covers tokens\n- covers errors", "Add the CLI flag\n1. wired in"]


# One regression corpus for parse_sub_intent_list: (plan markdown, expected item descriptions in order). Each item's
# goal is the first line of its description. Every parser edge case found so far lives here.
SUB_INTENT_PARSER_CORPUS = [
    # Nested bullets and indented continuation lines fold into the item above them.
    pytest.param(_section(_NESTED_BODY), _NESTED_EXPECTED, id="nested-bullets-fold"),
    # A top-level list may be indented 1-3 spaces; the first item fixes the level.
    *[
        pytest.param(_section(_indented_body(" " * n)), _INDENTED_EXPECTED, id=f"list-indented-{n}-spaces")
        for n in (1, 2, 3)
    ],
    pytest.param(_section("    - indented code, not a list"), [], id="four-space-lines-are-not-items"),
    pytest.param(_section("\t- tab-indented code, not a list"), [], id="tab-indented-first-line-is-not-an-item"),
    pytest.param(
        _section("- First\n\t- tab-nested detail\n- Second"),
        ["First\n- tab-nested detail", "Second"],
        id="tab-nested-bullet-folds",
    ),
    pytest.param(
        _section("1. First\n   1. detail one\n   2. detail two\n2. Second"),
        ["First\n1. detail one\n2. detail two", "Second"],
        id="nested-numbered-folds",
    ),
    pytest.param(
        _section("- One\n* Two\n+ Three\n1) Four\n2. Five"), ["One", "Two", "Three", "Four", "Five"], id="mixed-markers"
    ),
    pytest.param(
        _section("- First\n\n  more about first\n\n\n- Second\n\n- Third"),
        ["First\nmore about first", "Second", "Third"],
        id="blank-lines-and-continuation",
    ),
    pytest.param(_section(_NESTED_BODY).replace("\n", "\r\n"), _NESTED_EXPECTED, id="crlf-line-endings"),
    pytest.param(_section("- None\n- N/A\n- n/a.\n- na"), [], id="placeholders-only"),
    pytest.param(_section("- None\n- Real child"), ["Real child"], id="placeholder-beside-real-item"),
    # Wrapped or reworded placeholders say "no child work" and never become children.
    pytest.param(_section("- (none)"), [], id="parenthesised-none"),
    pytest.param(_section("- Not applicable"), [], id="not-applicable"),
    pytest.param(_section("- None required; work is in one step"), [], id="none-required-with-reason"),
    pytest.param(_section("- **None**"), [], id="bold-none"),
    pytest.param(_section("- _n/a_"), [], id="emphasised-n-a"),
    pytest.param(_section("- No sub-intents: the plan fits the budget."), [], id="no-sub-intents-with-reason"),
    # The first whole word (none, n/a, na, nil, no, not) decides, whatever follows.
    pytest.param(_section("- None (plan fits the budget)"), [], id="none-parenthetical"),
    pytest.param(_section("- N/A (single step)"), [], id="n-a-parenthetical"),
    pytest.param(_section("- No sub-intents needed"), [], id="no-sub-intents-needed"),
    pytest.param(_section("- Not needed for this plan"), [], id="not-needed-for-this-plan"),
    pytest.param(_section("- nil"), [], id="nil"),
    pytest.param(_section("- **No** sub-intents"), [], id="bold-no"),
    # A real item that merely starts with the letters of a placeholder word is not a placeholder (word boundary).
    pytest.param(
        _section(
            "- Nonetheless refactor the parser\n- Normalise the ledger rows\n- Notify the operator on failure\n"
            "- Note the budget in the summary"
        ),
        [
            "Nonetheless refactor the parser",
            "Normalise the ledger rows",
            "Notify the operator on failure",
            "Note the budget in the summary",
        ],
        id="word-boundary-real-items",
    ),
    pytest.param(_section("-   \n- Real child"), ["Real child"], id="empty-item-skipped"),
    # A longer outer fence closes only on the same character at least as long.
    *[
        pytest.param(
            _section(f"- First\n````md\n{inner}\n- Nested example\n{inner}\n````\n- Second"),
            ["First", "Second"],
            id=f"nested-fence-{name}",
        )
        for name, inner in [
            ("shorter-backticks", "```"),
            ("other-char", "~~~~"),
            ("other-char-longer", "~~~~~"),
            ("trailing-text", "``` not a closer"),
        ]
    ],
    # A list quoted inside a fence is never read.
    *[
        pytest.param(
            f"# Plan\n\n{fence}markdown\n## Sub-intent list\n\n- Quoted example\n{fence}\n\n## Risks\n\n- none\n",
            [],
            id=f"section-quoted-in-{name}-fence",
        )
        for name, fence in [("backtick", "```"), ("tilde", "~~~")]
    ],
    pytest.param(_section("```text\n- Example child\n```"), [], id="fence-under-the-heading"),
    pytest.param(
        "# Plan\n\n~~~~markdown\n## Sub-intent list\n- Quoted example\n~~~\n- still quoted\n~~~~~\n\n"
        "## Sub-intent list\n\n```\n- fenced, not a child\n```\n- Real child\n- Another real child\n",
        ["Real child", "Another real child"],
        id="real-list-after-closed-fence",
    ),
    pytest.param(
        _section("``` inline `code` ```\n- Real child"), ["Real child"], id="backtick-info-with-backtick-is-not-a-fence"
    ),
    pytest.param(_section("~~~ a`b\n- fenced\n~~~\n- Real child"), ["Real child"], id="tilde-info-may-hold-a-backtick"),
    # A fence nested in an item at 4+ columns holding a "# comment" line neither leaks nor ends the section.
    pytest.param(
        _section("- Add CLI flag\n    ```bash\n    # run the tests\n    uv run pytest\n    ```\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="four-space-bash-fence-with-comment",
    ),
    pytest.param(
        _section("1. Add CLI flag\n    ```python\n    # helper\n    - not a child\n    ```\n2. Update docs"),
        ["Add CLI flag", "Update docs"],
        id="numbered-four-space-python-fence",
    ),
    pytest.param(
        _section("- Add CLI flag\n  ```bash\n  # run the tests\n  ```\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="two-space-fence",
    ),
    pytest.param(
        _section("- Add CLI flag\n\t\t~~~\n\t\t## not a heading\n\t\t~~~\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="tab-indented-fence",
    ),
    pytest.param(
        _section("- Add CLI flag\n        ````\n```\n## not a heading\n````\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="deep-fence-closes-at-any-indent",
    ),
    # Headings end the section only at 0-3 columns; deeper "#" lines fold into the item above.
    pytest.param(
        _section("- First\n    # not a heading\n- Second"),
        ["First\n# not a heading", "Second"],
        id="four-space-hash-line-folds",
    ),
    pytest.param(_section("- First\n   ## Next\n- not a child"), ["First"], id="three-space-heading-ends-section"),
    # Real (non-placeholder) items, so only the heading level decides.
    pytest.param("# Plan\n\n# Sub-intent list\n\n- Add the parser\n", [], id="level-1-heading-does-not-open"),
    pytest.param("# Plan\n\n#### Sub-intent list\n\n- Add the parser\n", [], id="level-4-heading-does-not-open"),
    # A Unicode hyphen or non-breaking hyphen in the heading normalises like an ASCII hyphen.
    *[
        pytest.param(
            f"# Plan\n\n## Sub{hyphen}intent list\n\n- Add the parser\n", ["Add the parser"], id=f"heading-{name}"
        )
        for name, hyphen in [("unicode-hyphen", "\u2010"), ("non-breaking-hyphen", "\u2011")]
    ],
    # A setext heading (paragraph line underlined with "=" or "-") ends the section like an ATX heading.
    # The heading text must be a plain paragraph, so a blank line separates it from the item above.
    pytest.param(
        _section("- Add the parser\n\nNotes\n-----\n- Remember to run lint\n- Keep docs current"),
        ["Add the parser"],
        id="setext-dash-heading-ends-section",
    ),
    pytest.param(
        _section("- Add the parser\n\nOther\n=====  \n- Ship it"),
        ["Add the parser"],
        id="setext-equals-heading-ends-section",
    ),
    pytest.param(
        "# Plan\n\nSub-intent\nlist\n---\n\n- Add the parser\n",
        ["Add the parser"],
        id="multi-line-setext-heading-text-is-the-whole-paragraph",
    ),
    # A text line straight after an item (or its continuation) is a lazy continuation of the item, never a
    # setext heading text line: "---" under it is a thematic break and "===" is more continuation text.
    pytest.param(_section("- Add A\nlazy text\n---\n- Add B"), ["Add A", "Add B"], id="lazy-dash"),
    pytest.param(_section("- Add A\nlazy\n===\n- Add B"), ["Add A", "Add B"], id="lazy-equals"),
    pytest.param(
        _section("- Add A\n  more\nNotes\n---\n- Add B"),
        ["Add A\nmore", "Add B"],
        id="lazy-after-continuation",
    ),
    pytest.param("# Plan\n\n- step\nSub-intent list\n---\n- Add x\n", [], id="lazy-line-does-not-open"),
    pytest.param("# Plan\n\n- step\n\n  Sub-intent list\n---\n- Add x\n", [], id="item-content-does-not-open"),
    # A paragraph after a thematic break, an ATX heading or an item's narrower-than-content line starts afresh, so it
    # can be setext heading text; a line inside an item's fenced block area stays item content.
    pytest.param(_section("- Add A\n***\nNotes\n---\n- Add B"), ["Add A"], id="paragraph-after-break"),
    pytest.param("# Plan\n\n- step\n## Sub-intent list\nNotes\n---\n- Add x\n", [], id="paragraph-after-atx-heading"),
    pytest.param(_section("1. Add A\n\n  Notes\n---\n- Add B"), ["Add A"], id="line-left-of-item-content"),
    pytest.param(
        _section("- Add A\n\n  ```\n  x\n  ```\n\n  Notes\n---\n- Add B"),
        ["Add A\nNotes", "Add B"],
        id="item-content-after-nested-fence",
    ),
    # The underline is at most 3 columns deep, a text line at 4+ columns with no paragraph open is indented
    # code, and a one- or two-dash underline still makes a heading.
    pytest.param(_section("- Add A\n\nNotes\n    ---\n- Add B"), ["Add A\n---", "Add B"], id="deep-underline"),
    pytest.param(_section("    Notes\n---\n- Add B"), ["Add B"], id="indented-code-is-not-heading-text"),
    pytest.param(_section("- Add A\n\nNotes\n--\n- Add B"), ["Add A"], id="two-dash-underline"),
    pytest.param(_section("- Add A\n\nNotes\n-\n- Add B"), ["Add A"], id="one-dash-underline"),
    pytest.param(
        "# Plan\n\nSub-intent list\n---------------\n\n- Add the parser\n\nRisks\n-----\n\n- Ship it\n",
        ["Add the parser"],
        id="level-2-setext-heading-opens-and-ends",
    ),
    pytest.param(
        "# Plan\n\nSub-intent list\n===============\n\n- Add the parser\n",
        [],
        id="level-1-setext-does-not-open",
    ),
    # A "---" line straight after a list item, or under a continuation line, is a thematic break, not an underline.
    pytest.param(_section("- A\n---\n- B"), ["A", "B"], id="break-after-item-is-not-an-underline"),
    pytest.param(
        _section("- A\n  more about A\n  ---\n- B"),
        ["A\nmore about A", "B"],
        id="break-under-continuation-is-not-an-underline",
    ),
    # An unclosed fence runs to the end of the plan; items before it survive.
    pytest.param(_section("- First\n```\n- fenced\n## Next\n- fenced too"), ["First"], id="unclosed-fence-after-item"),
    pytest.param(_section("```\n- fenced"), [], id="unclosed-fence-before-items"),
    # Thematic breaks and punctuation-only items never become children.
    *[
        pytest.param(_section(f"- A\n- B\n\n{rule}\n\n- C"), ["A", "B", "C"], id=f"thematic-break-{name}")
        for name, rule in [("stars", "* * *"), ("dashes", "---"), ("underscores", "___"), ("spaced-dashes", "- - -")]
    ],
    pytest.param(_section("- A\n  * * *\n- B"), ["A", "B"], id="indented-thematic-break-does-not-fold"),
    # A break-like line at 4+ columns is not a break; it folds into the item above.
    pytest.param(_section("- A\n    - - -\n- B"), ["A\n- - -", "B"], id="four-space-break-like-line-folds"),
    pytest.param(_section("- A\n- ...\n- (!)\n- B"), ["A", "B"], id="punctuation-only-item-skipped"),
    # Each row below pins one parser rule; the rules match CommonMark for what opens, ends or skips the section.
    # Descriptions follow the fold rule instead: an indented line folds into the item above even where CommonMark
    # makes it part of a later paragraph (row tab-underline-is-4-cols).
    # A fence resets the paragraph state, and a fence left of the item content column ends the list.
    pytest.param(_section("- Add A\n```\nx\n```\nNotes\n---\n- Add B"), ["Add A"], id="fence-resets-paragraph"),
    pytest.param(
        _section("- Add A\n```\nx\n```\n\n  Notes\n---\n- Add B"),
        ["Add A"],
        id="fence-left-of-content-ends-list",
    ),
    # A thematic break inside an item keeps the item open; one left of the content column ends the list; a break is
    # never setext heading text; a break-like line 4+ columns past the content column is paragraph text, not a break.
    pytest.param(
        _section("- Add A\n\n  ***\n\n  Notes\n---\n- Add B"),
        ["Add A\nNotes", "Add B"],
        id="break-in-item-keeps-the-item-open",
    ),
    pytest.param(_section("- Add A\n\n   ***\nNotes\n---\n- Add B"), ["Add A"], id="break-at-3-columns-in-item"),
    pytest.param(
        _section("- Add A\n      ***\nNotes\n---\n- Add B"),
        ["Add A\n***", "Add B"],
        id="break-like-line-past-content-is-continuation",
    ),
    pytest.param(_section("- Add A\n***\n\n  Notes\n---\n- Add B"), ["Add A"], id="break-left-of-content-ends-list"),
    pytest.param(_section("- Add A\n***\n---\n- Add B"), ["Add A", "Add B"], id="break-is-not-heading-text"),
    # Items and paragraphs: a 3-column item still takes lazy lines; an item-like 4-column line with no paragraph open is
    # indented code; nested items and continuation lines keep the item content column; a 3-column plain paragraph is
    # heading text; a plain paragraph ends the list, so a later 2-column line is not item content.
    pytest.param(_section("   - Add A\nNotes\n---\n- Add B"), ["Add A", "Add B"], id="item-at-3-columns-takes-lazy"),
    pytest.param(_section("    - code\nNotes\n---\n- Add B"), [], id="item-like-indented-code-then-heading"),
    pytest.param(
        _section("- Add A\n\n    - sub\n\n  Notes\n---\n- Add B"),
        ["Add A\n- sub\nNotes", "Add B"],
        id="nested-item-keeps-content-column",
    ),
    pytest.param(
        _section("- Add A\n  more\n\n  Notes\n---\n- Add B"),
        ["Add A\nmore\nNotes", "Add B"],
        id="continuation-keeps-content-column",
    ),
    pytest.param(_section("   Notes\n---\n- Add B"), [], id="3-column-paragraph-is-heading-text"),
    pytest.param(_section("- Add A\n\nNotes\n\n  Other\n---\n- Add B"), ["Add A"], id="paragraph-drops-content-column"),
    pytest.param(
        _section("-\tAdd A\n\n    more\nOther\n---\n- Add B"),
        ["Add A\nmore", "Add B"],
        id="tab-after-marker-is-content-column-4",
    ),
    # Setext underline: 3 columns is still an underline, a tab is 4 columns, mixed or spaced runs are not underlines,
    # and the underline line is consumed rather than read as a paragraph of its own. In the tab row, CommonMark reads
    # "\t---" as a continuation of the paragraph "Notes"; the fold rule puts it in item A's description instead.
    pytest.param(_section("- Add A\n\nNotes\n   ---\n- Add B"), ["Add A"], id="underline-at-3-columns"),
    pytest.param(_section("- Add A\n\nNotes\n\t---\n- Add B"), ["Add A\n---", "Add B"], id="tab-underline-is-4-cols"),
    pytest.param(_section("- Add A\n\nNotes\n-=-\n- Add B"), ["Add A", "Add B"], id="mixed-underline"),
    pytest.param(_section("- Add A\n\nNotes\n= =\n- Add B"), ["Add A", "Add B"], id="spaced-underline"),
    pytest.param("# Plan\n\nSub-intent list\n--\n--\n- Add the parser\n", ["Add the parser"], id="underline-consumed"),
    pytest.param(
        "# Plan\n\n## Sub-intent list\n===\n---\n- Add the parser\n",
        [],
        id="equals-line-after-atx-heading-is-a-paragraph",
    ),
    pytest.param("# Plan\n\nSub-intent\n\nlist\n---\n\n- Add the parser\n", [], id="blank-line-splits-heading-text"),
    # Fences: two backticks are not a fence, and a closing fence takes no info string.
    pytest.param(_section("- Add A\n``\n- Add B\n``"), ["Add A", "Add B"], id="two-backticks-are-not-a-fence"),
    pytest.param(
        _section("```\n```python\n- Example child\n```\n- Add B"), ["Add B"], id="fence-with-info-does-not-close"
    ),
    # ATX headings: a space after the hashes, at most six hashes, closing hashes stripped, underscores normalised.
    pytest.param(_section("- Add A\n#hashtag\n- Add B"), ["Add A", "Add B"], id="hash-without-space-not-heading"),
    pytest.param(_section("- Add A\n####### not a heading\n- Add B"), ["Add A", "Add B"], id="seven-hashes"),
    # An empty ATX heading (a bare "#" or "##", or one holding only closing hashes) still ends the section.
    pytest.param(_section("- Add X\n\n##\n\n- Do Y"), ["Add X"], id="empty-h2-ends-section"),
    pytest.param(_section("- Add X\n\n#\n\n- Do Y"), ["Add X"], id="empty-h1-ends-section"),
    pytest.param(_section("- Add X\n##   \n- Do Y"), ["Add X"], id="empty-h2-with-trailing-spaces-ends-section"),
    pytest.param(_section("- Add X\n### ###\n- Do Y"), ["Add X"], id="closing-hashes-only-heading-ends-section"),
    pytest.param(_section("- Add X\n#######\n- Do Y"), ["Add X", "Do Y"], id="seven-bare-hashes-not-heading"),
    pytest.param(_section("- Add X\n#5 bolts\n- Do Y"), ["Add X", "Do Y"], id="hash-before-digit-not-heading"),
    pytest.param("# Plan\n\n## Sub-intent list ##\n\n- Add the parser\n", ["Add the parser"], id="closing-hashes"),
    pytest.param("# Plan\n\n## Sub_intent_list\n\n- Add the parser\n", ["Add the parser"], id="underscore-heading"),
    pytest.param(
        "# Plan\n\n## Sub-intent list\n\n- Add A\n\n## Sub-intents\n\n  - Add B\n    - detail\n",
        ["Add A", "Add B\n- detail"],
        id="second-section-sets-its-own-level",
    ),
    pytest.param("# Plan\n\n- step\n## Sub-intent list\n\n  Notes\n---\n- Add x\n", [], id="heading-ends-the-list"),
    # Section body: whitespace-only lines, 3-column and underscore breaks, two stars, markers without a space.
    pytest.param(_section("- Add A\n    \n  more\n- Add B"), ["Add A\nmore", "Add B"], id="whitespace-only-line"),
    pytest.param(_section("- A\n   ***\n- B"), ["A", "B"], id="break-at-3-columns-skipped"),
    pytest.param(_section("- A\n  ___\n- B"), ["A", "B"], id="underscore-break-skipped"),
    pytest.param(_section("- A\n  **\n- B"), ["A\n**", "B"], id="two-stars-fold"),
    pytest.param(_section("  - Add A\n-   Add B\n  - Add C"), ["Add A", "Add B", "Add C"], id="first-item-fixes-level"),
    pytest.param(
        _section("- Add the CLI flag\n--verbose enables logging\n- Add docs"),
        ["Add the CLI flag", "Add docs"],
        id="dash-without-space-is-not-an-item",
    ),
    # Placeholders: emphasis followed by a full stop, and curly quotes.
    pytest.param(_section("- _None_."), [], id="emphasised-none-with-full-stop"),
    pytest.param(_section("- \u201cNone\u201d"), [], id="curly-quoted-none"),
    # Ordered items with multi-digit numbers.
    pytest.param(_section("10. Add A\n11. Add B"), ["Add A", "Add B"], id="multi-digit-ordered-items"),
]


@pytest.mark.parametrize(("plan", "expected"), SUB_INTENT_PARSER_CORPUS)
def test_parse_sub_intent_list_regression_corpus(plan, expected):
    items = executor.parse_sub_intent_list(plan)

    assert [item["description"] for item in items] == expected
    assert [item["goal"] for item in items] == [description.split("\n")[0] for description in expected]


def test_parse_sub_intent_list_builds_slug_description_and_goal():
    assert executor.parse_sub_intent_list(_section(_NESTED_BODY)) == [
        {"slug": "add-the-parser", "description": _NESTED_EXPECTED[0], "goal": "Add the parser"},
        {"slug": "add-the-cli-flag", "description": _NESTED_EXPECTED[1], "goal": "Add the CLI flag"},
    ]


def test_parse_sub_intent_list_cuts_long_slugs_to_32_characters_at_a_word_boundary():
    # The slug is cut to the 32-character slug limit at a word boundary, leaving no trailing hyphen.
    text = "Add " + "x" * 23 + " parser for every ledger row"
    (item,) = executor.parse_sub_intent_list(_section(f"- {text}"))

    assert item["slug"] == "add-" + "x" * 23
    assert item["goal"] == text


def test_parse_sub_intent_list_strips_leading_hyphens_before_truncating_the_slug():
    # Leading punctuation is dropped before the 32-character cut, so it costs no character.
    (item,) = executor.parse_sub_intent_list(_section("- **Add** the parser for every ledger row and every intent row"))

    assert item["slug"] == "add-the-parser-for-every-ledger"


def test_parse_sub_intent_list_falls_back_to_a_generic_slug():
    # An item with no ASCII letter or digit still gets a slug.
    (item,) = executor.parse_sub_intent_list(_section("- Добавить парсер"))

    assert item["slug"] == "sub-intent"
    assert item["goal"] == "Добавить парсер"


def test_should_decompose_executes_a_plan_exactly_at_the_budget():
    # Only entropy strictly above the budget is over budget ("at or below" executes).
    decision = executor.should_decompose({"entropy": 5.0, "entropy_budget": 5.0}, "# Plan\n")

    assert decision.decision == executor.DECISION_EXECUTE


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (_section(_indented_body("  ")), executor.DECISION_DECOMPOSED),
        (_section("- None\n- N/A"), executor.DECISION_OVER_BUDGET),
        ("# Plan\n\n```markdown\n## Sub-intent list\n\n- Quoted example\n```\n", executor.DECISION_OVER_BUDGET),
    ],
    ids=["indented-list-decomposes", "placeholders-are-over-budget", "fenced-list-is-over-budget"],
)
def test_should_decompose_follows_the_parsed_list(plan, expected):
    assert executor.should_decompose({"entropy": 40.0, "entropy_budget": 5.0}, plan).decision == expected


def test_should_decompose_ignores_per_step_recommendation_bullets():
    plan = (
        "# Plan\n\n## Step 1: Thing\n\n- **Sub\u2011intent recommendation:** YES\n- **Sub\u2011intent:** NONE\n"
        "\n### Sub-intent notes\n\n- not a list either\n"
    )

    decision = executor.should_decompose({"entropy": 2.0, "entropy_budget": 5.0}, plan)

    assert decision.decision == executor.DECISION_EXECUTE
    assert decision.sub_intents == []


@pytest.mark.parametrize(
    "item",
    [
        "`\tNone`",
        "_None_;",
        "_None_!",
        "_None_?",
        "_None_:",
        "_None_,",
        "[None]",
        "<none>",
        "{none}",
        "[N/A]",
    ],
)
def test_is_placeholder_item_normalises_whitespace_wrappers_and_trailing_punctuation(item):
    # Whitespace inside wrappers collapses, every wrapper pair is stripped, and trailing punctuation that sits outside a
    # wrapper is dropped before the first-word test, so each of these says "no child work".
    assert executor.is_placeholder_item(item) is True


def test_should_decompose_treats_a_missing_entropy_as_zero():
    decision = executor.should_decompose({"entropy_budget": 5.0}, "# Plan\n")

    assert (decision.decision, decision.entropy) == (executor.DECISION_EXECUTE, 0.0)


def test_no_placeholder_sub_intent_text_in_source():
    """The removed fallback goals must not reappear anywhere under src/ (phrases built from fragments)."""
    phrases = [
        "Implement component " + "core logic",
        "Implement integration tests " + "and validation",
        "sub-intent-" + "part-",
    ]
    src_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    offenders = []
    for dirpath, _dirs, files in os.walk(src_root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            offenders.extend(f"{path}: {phrase}" for phrase in phrases if phrase in text)
    assert offenders == []
