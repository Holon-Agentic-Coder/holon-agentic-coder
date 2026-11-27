import os

import pytest
from sandbox_executor.entrypoint import executor

# why: the noqa imports register autouse fixtures, which pytest only applies once they are in the module namespace.
from tests.executor_fixtures import (
    _agent_turns_use_run_cmd,  # noqa: F401
    _workspace_is_mounted,  # noqa: F401
)

# why: assembled from fragments so this file never spells the legacy capitalised plural heading out.
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


def _sub_intent_plan(body: str) -> str:
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


# why: every parser edge case found so far lives in this one regression corpus.
SUB_INTENT_PARSER_CORPUS = [
    pytest.param(_sub_intent_plan(_NESTED_BODY), _NESTED_EXPECTED, id="nested-bullets-fold"),
    *[
        pytest.param(_sub_intent_plan(_indented_body(" " * n)), _INDENTED_EXPECTED, id=f"list-indented-{n}-spaces")
        for n in (1, 2, 3)
    ],
    pytest.param(_sub_intent_plan("    - indented code, not a list"), [], id="four-space-lines-are-not-items"),
    pytest.param(
        _sub_intent_plan("\t- tab-indented code, not a list"), [], id="tab-indented-first-line-is-not-an-item"
    ),
    pytest.param(
        _sub_intent_plan("- First\n\t- tab-nested detail\n- Second"),
        ["First\n- tab-nested detail", "Second"],
        id="tab-nested-bullet-folds",
    ),
    pytest.param(
        _sub_intent_plan("1. First\n   1. detail one\n   2. detail two\n2. Second"),
        ["First\n1. detail one\n2. detail two", "Second"],
        id="nested-numbered-folds",
    ),
    pytest.param(
        _sub_intent_plan("- One\n* Two\n+ Three\n1) Four\n2. Five"),
        ["One", "Two", "Three", "Four", "Five"],
        id="mixed-markers",
    ),
    pytest.param(
        _sub_intent_plan("- First\n\n  more about first\n\n\n- Second\n\n- Third"),
        ["First\nmore about first", "Second", "Third"],
        id="blank-lines-and-continuation",
    ),
    pytest.param(_sub_intent_plan(_NESTED_BODY).replace("\n", "\r\n"), _NESTED_EXPECTED, id="crlf-line-endings"),
    pytest.param(_sub_intent_plan("- None\n- N/A\n- n/a.\n- na"), [], id="placeholders-only"),
    pytest.param(_sub_intent_plan("- None\n- Real child"), ["Real child"], id="placeholder-beside-real-item"),
    pytest.param(_sub_intent_plan("- (none)"), [], id="parenthesised-none"),
    pytest.param(_sub_intent_plan("- Not applicable"), [], id="not-applicable"),
    pytest.param(_sub_intent_plan("- None required; work is in one step"), [], id="none-required-with-reason"),
    pytest.param(_sub_intent_plan("- **None**"), [], id="bold-none"),
    pytest.param(_sub_intent_plan("- _n/a_"), [], id="emphasised-n-a"),
    pytest.param(_sub_intent_plan("- No sub-intents: the plan fits the budget."), [], id="no-sub-intents-with-reason"),
    pytest.param(_sub_intent_plan("- None (plan fits the budget)"), [], id="none-parenthetical"),
    pytest.param(_sub_intent_plan("- N/A (single step)"), [], id="n-a-parenthetical"),
    pytest.param(_sub_intent_plan("- No sub-intents needed"), [], id="no-sub-intents-needed"),
    pytest.param(_sub_intent_plan("- Not needed for this plan"), [], id="not-needed-for-this-plan"),
    pytest.param(_sub_intent_plan("- nil"), [], id="nil"),
    pytest.param(_sub_intent_plan("- **No** sub-intents"), [], id="bold-no"),
    pytest.param(
        _sub_intent_plan(
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
    pytest.param(_sub_intent_plan("-   \n- Real child"), ["Real child"], id="empty-item-skipped"),
    *[
        pytest.param(
            _sub_intent_plan(f"- First\n````md\n{inner}\n- Nested example\n{inner}\n````\n- Second"),
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
    *[
        pytest.param(
            f"# Plan\n\n{fence}markdown\n## Sub-intent list\n\n- Quoted example\n{fence}\n\n## Risks\n\n- none\n",
            [],
            id=f"section-quoted-in-{name}-fence",
        )
        for name, fence in [("backtick", "```"), ("tilde", "~~~")]
    ],
    pytest.param(_sub_intent_plan("```text\n- Example child\n```"), [], id="fence-under-the-heading"),
    pytest.param(
        "# Plan\n\n~~~~markdown\n## Sub-intent list\n- Quoted example\n~~~\n- still quoted\n~~~~~\n\n"
        "## Sub-intent list\n\n```\n- fenced, not a child\n```\n- Real child\n- Another real child\n",
        ["Real child", "Another real child"],
        id="real-list-after-closed-fence",
    ),
    pytest.param(
        _sub_intent_plan("``` inline `code` ```\n- Real child"),
        ["Real child"],
        id="backtick-info-with-backtick-is-not-a-fence",
    ),
    pytest.param(
        _sub_intent_plan("~~~ a`b\n- fenced\n~~~\n- Real child"), ["Real child"], id="tilde-info-may-hold-a-backtick"
    ),
    pytest.param(
        _sub_intent_plan("- Add CLI flag\n    ```bash\n    # run the tests\n    uv run pytest\n    ```\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="four-space-bash-fence-with-comment",
    ),
    pytest.param(
        _sub_intent_plan("1. Add CLI flag\n    ```python\n    # helper\n    - not a child\n    ```\n2. Update docs"),
        ["Add CLI flag", "Update docs"],
        id="numbered-four-space-python-fence",
    ),
    pytest.param(
        _sub_intent_plan("- Add CLI flag\n  ```bash\n  # run the tests\n  ```\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="two-space-fence",
    ),
    pytest.param(
        _sub_intent_plan("- Add CLI flag\n\t\t~~~\n\t\t## not a heading\n\t\t~~~\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="tab-indented-fence",
    ),
    pytest.param(
        _sub_intent_plan("- Add CLI flag\n        ````\n```\n## not a heading\n````\n- Update docs"),
        ["Add CLI flag", "Update docs"],
        id="deep-fence-closes-at-any-indent",
    ),
    pytest.param(
        _sub_intent_plan("- First\n    # not a heading\n- Second"),
        ["First\n# not a heading", "Second"],
        id="four-space-hash-line-folds",
    ),
    pytest.param(
        _sub_intent_plan("- First\n   ## Next\n- not a child"), ["First"], id="three-space-heading-ends-section"
    ),
    # why: real (non-placeholder) items, so only the heading level decides.
    pytest.param("# Plan\n\n# Sub-intent list\n\n- Add the parser\n", [], id="level-1-heading-does-not-open"),
    pytest.param("# Plan\n\n#### Sub-intent list\n\n- Add the parser\n", [], id="level-4-heading-does-not-open"),
    *[
        pytest.param(
            f"# Plan\n\n## Sub{hyphen}intent list\n\n- Add the parser\n", ["Add the parser"], id=f"heading-{name}"
        )
        for name, hyphen in [("unicode-hyphen", "\u2010"), ("non-breaking-hyphen", "\u2011")]
    ],
    pytest.param(
        _sub_intent_plan("- Add the parser\n\nNotes\n-----\n- Remember to run lint\n- Keep docs current"),
        ["Add the parser"],
        id="setext-dash-heading-ends-section",
    ),
    pytest.param(
        _sub_intent_plan("- Add the parser\n\nOther\n=====  \n- Ship it"),
        ["Add the parser"],
        id="setext-equals-heading-ends-section",
    ),
    pytest.param(
        "# Plan\n\nSub-intent\nlist\n---\n\n- Add the parser\n",
        ["Add the parser"],
        id="multi-line-setext-heading-text-is-the-whole-paragraph",
    ),
    pytest.param(_sub_intent_plan("- Add A\nlazy text\n---\n- Add B"), ["Add A", "Add B"], id="lazy-dash"),
    pytest.param(_sub_intent_plan("- Add A\nlazy\n===\n- Add B"), ["Add A", "Add B"], id="lazy-equals"),
    pytest.param(
        _sub_intent_plan("- Add A\n  more\nNotes\n---\n- Add B"),
        ["Add A\nmore", "Add B"],
        id="lazy-after-continuation",
    ),
    pytest.param("# Plan\n\n- step\nSub-intent list\n---\n- Add x\n", [], id="lazy-line-does-not-open"),
    pytest.param("# Plan\n\n- step\n\n  Sub-intent list\n---\n- Add x\n", [], id="item-content-does-not-open"),
    pytest.param(_sub_intent_plan("- Add A\n***\nNotes\n---\n- Add B"), ["Add A"], id="paragraph-after-break"),
    pytest.param("# Plan\n\n- step\n## Sub-intent list\nNotes\n---\n- Add x\n", [], id="paragraph-after-atx-heading"),
    pytest.param(_sub_intent_plan("1. Add A\n\n  Notes\n---\n- Add B"), ["Add A"], id="line-left-of-item-content"),
    pytest.param(
        _sub_intent_plan("- Add A\n\n  ```\n  x\n  ```\n\n  Notes\n---\n- Add B"),
        ["Add A\nNotes", "Add B"],
        id="item-content-after-nested-fence",
    ),
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n    ---\n- Add B"), ["Add A\n---", "Add B"], id="deep-underline"),
    pytest.param(_sub_intent_plan("    Notes\n---\n- Add B"), ["Add B"], id="indented-code-is-not-heading-text"),
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n--\n- Add B"), ["Add A"], id="two-dash-underline"),
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n-\n- Add B"), ["Add A"], id="one-dash-underline"),
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
    pytest.param(_sub_intent_plan("- A\n---\n- B"), ["A", "B"], id="break-after-item-is-not-an-underline"),
    pytest.param(
        _sub_intent_plan("- A\n  more about A\n  ---\n- B"),
        ["A\nmore about A", "B"],
        id="break-under-continuation-is-not-an-underline",
    ),
    pytest.param(
        _sub_intent_plan("- First\n```\n- fenced\n## Next\n- fenced too"), ["First"], id="unclosed-fence-after-item"
    ),
    pytest.param(_sub_intent_plan("```\n- fenced"), [], id="unclosed-fence-before-items"),
    *[
        pytest.param(_sub_intent_plan(f"- A\n- B\n\n{rule}\n\n- C"), ["A", "B", "C"], id=f"thematic-break-{name}")
        for name, rule in [("stars", "* * *"), ("dashes", "---"), ("underscores", "___"), ("spaced-dashes", "- - -")]
    ],
    pytest.param(_sub_intent_plan("- A\n  * * *\n- B"), ["A", "B"], id="indented-thematic-break-does-not-fold"),
    pytest.param(_sub_intent_plan("- A\n    - - -\n- B"), ["A\n- - -", "B"], id="four-space-break-like-line-folds"),
    pytest.param(_sub_intent_plan("- A\n- ...\n- (!)\n- B"), ["A", "B"], id="punctuation-only-item-skipped"),
    # why: rows match CommonMark except the fold rule, which keeps an indented line in the item above it.
    pytest.param(_sub_intent_plan("- Add A\n```\nx\n```\nNotes\n---\n- Add B"), ["Add A"], id="fence-resets-paragraph"),
    pytest.param(
        _sub_intent_plan("- Add A\n```\nx\n```\n\n  Notes\n---\n- Add B"),
        ["Add A"],
        id="fence-left-of-content-ends-list",
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n\n  ***\n\n  Notes\n---\n- Add B"),
        ["Add A\nNotes", "Add B"],
        id="break-in-item-keeps-the-item-open",
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n\n   ***\nNotes\n---\n- Add B"), ["Add A"], id="break-at-3-columns-in-item"
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n      ***\nNotes\n---\n- Add B"),
        ["Add A\n***", "Add B"],
        id="break-like-line-past-content-is-continuation",
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n***\n\n  Notes\n---\n- Add B"), ["Add A"], id="break-left-of-content-ends-list"
    ),
    pytest.param(_sub_intent_plan("- Add A\n***\n---\n- Add B"), ["Add A", "Add B"], id="break-is-not-heading-text"),
    pytest.param(
        _sub_intent_plan("   - Add A\nNotes\n---\n- Add B"), ["Add A", "Add B"], id="item-at-3-columns-takes-lazy"
    ),
    pytest.param(_sub_intent_plan("    - code\nNotes\n---\n- Add B"), [], id="item-like-indented-code-then-heading"),
    pytest.param(
        _sub_intent_plan("- Add A\n\n    - sub\n\n  Notes\n---\n- Add B"),
        ["Add A\n- sub\nNotes", "Add B"],
        id="nested-item-keeps-content-column",
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n  more\n\n  Notes\n---\n- Add B"),
        ["Add A\nmore\nNotes", "Add B"],
        id="continuation-keeps-content-column",
    ),
    pytest.param(_sub_intent_plan("   Notes\n---\n- Add B"), [], id="3-column-paragraph-is-heading-text"),
    pytest.param(
        _sub_intent_plan("- Add A\n\nNotes\n\n  Other\n---\n- Add B"), ["Add A"], id="paragraph-drops-content-column"
    ),
    pytest.param(
        _sub_intent_plan("-\tAdd A\n\n    more\nOther\n---\n- Add B"),
        ["Add A\nmore", "Add B"],
        id="tab-after-marker-is-content-column-4",
    ),
    # why: in tab-underline-is-4-cols CommonMark reads "\t---" as paragraph text; the fold rule puts it in item A.
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n   ---\n- Add B"), ["Add A"], id="underline-at-3-columns"),
    pytest.param(
        _sub_intent_plan("- Add A\n\nNotes\n\t---\n- Add B"), ["Add A\n---", "Add B"], id="tab-underline-is-4-cols"
    ),
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n-=-\n- Add B"), ["Add A", "Add B"], id="mixed-underline"),
    pytest.param(_sub_intent_plan("- Add A\n\nNotes\n= =\n- Add B"), ["Add A", "Add B"], id="spaced-underline"),
    pytest.param("# Plan\n\nSub-intent list\n--\n--\n- Add the parser\n", ["Add the parser"], id="underline-consumed"),
    pytest.param(
        "# Plan\n\n## Sub-intent list\n===\n---\n- Add the parser\n",
        [],
        id="equals-line-after-atx-heading-is-a-paragraph",
    ),
    pytest.param("# Plan\n\nSub-intent\n\nlist\n---\n\n- Add the parser\n", [], id="blank-line-splits-heading-text"),
    pytest.param(_sub_intent_plan("- Add A\n``\n- Add B\n``"), ["Add A", "Add B"], id="two-backticks-are-not-a-fence"),
    pytest.param(
        _sub_intent_plan("```\n```python\n- Example child\n```\n- Add B"),
        ["Add B"],
        id="fence-with-info-does-not-close",
    ),
    pytest.param(
        _sub_intent_plan("- Add A\n#hashtag\n- Add B"), ["Add A", "Add B"], id="hash-without-space-not-heading"
    ),
    pytest.param(_sub_intent_plan("- Add A\n####### not a heading\n- Add B"), ["Add A", "Add B"], id="seven-hashes"),
    pytest.param(_sub_intent_plan("- Add X\n\n##\n\n- Do Y"), ["Add X"], id="empty-h2-ends-section"),
    pytest.param(_sub_intent_plan("- Add X\n\n#\n\n- Do Y"), ["Add X"], id="empty-h1-ends-section"),
    pytest.param(
        _sub_intent_plan("- Add X\n##   \n- Do Y"), ["Add X"], id="empty-h2-with-trailing-spaces-ends-section"
    ),
    pytest.param(
        _sub_intent_plan("- Add X\n### ###\n- Do Y"), ["Add X"], id="closing-hashes-only-heading-ends-section"
    ),
    pytest.param(_sub_intent_plan("- Add X\n#######\n- Do Y"), ["Add X", "Do Y"], id="seven-bare-hashes-not-heading"),
    pytest.param(_sub_intent_plan("- Add X\n#5 bolts\n- Do Y"), ["Add X", "Do Y"], id="hash-before-digit-not-heading"),
    pytest.param("# Plan\n\n## Sub-intent list ##\n\n- Add the parser\n", ["Add the parser"], id="closing-hashes"),
    pytest.param("# Plan\n\n## Sub_intent_list\n\n- Add the parser\n", ["Add the parser"], id="underscore-heading"),
    pytest.param(
        "# Plan\n\n## Sub-intent list\n\n- Add A\n\n## Sub-intents\n\n  - Add B\n    - detail\n",
        ["Add A", "Add B\n- detail"],
        id="second-section-sets-its-own-level",
    ),
    pytest.param("# Plan\n\n- step\n## Sub-intent list\n\n  Notes\n---\n- Add x\n", [], id="heading-ends-the-list"),
    pytest.param(
        _sub_intent_plan("- Add A\n    \n  more\n- Add B"), ["Add A\nmore", "Add B"], id="whitespace-only-line"
    ),
    pytest.param(_sub_intent_plan("- A\n   ***\n- B"), ["A", "B"], id="break-at-3-columns-skipped"),
    pytest.param(_sub_intent_plan("- A\n  ___\n- B"), ["A", "B"], id="underscore-break-skipped"),
    pytest.param(_sub_intent_plan("- A\n  **\n- B"), ["A\n**", "B"], id="two-stars-fold"),
    pytest.param(
        _sub_intent_plan("  - Add A\n-   Add B\n  - Add C"), ["Add A", "Add B", "Add C"], id="first-item-fixes-level"
    ),
    pytest.param(
        _sub_intent_plan("- Add the CLI flag\n--verbose enables logging\n- Add docs"),
        ["Add the CLI flag", "Add docs"],
        id="dash-without-space-is-not-an-item",
    ),
    pytest.param(_sub_intent_plan("- _None_."), [], id="emphasised-none-with-full-stop"),
    pytest.param(_sub_intent_plan("- \u201cNone\u201d"), [], id="curly-quoted-none"),
    pytest.param(_sub_intent_plan("10. Add A\n11. Add B"), ["Add A", "Add B"], id="multi-digit-ordered-items"),
]


@pytest.mark.parametrize(("plan", "expected"), SUB_INTENT_PARSER_CORPUS)
def test_parse_sub_intent_list_regression_corpus(plan, expected):
    items = executor.parse_sub_intent_list(plan)

    assert [item["description"] for item in items] == expected
    assert [item["goal"] for item in items] == [description.split("\n")[0] for description in expected]


def test_parse_sub_intent_list_builds_slug_description_and_goal():
    assert executor.parse_sub_intent_list(_sub_intent_plan(_NESTED_BODY)) == [
        {"slug": "add-the-parser", "description": _NESTED_EXPECTED[0], "goal": "Add the parser"},
        {"slug": "add-the-cli-flag", "description": _NESTED_EXPECTED[1], "goal": "Add the CLI flag"},
    ]


def test_parse_sub_intent_list_cuts_long_slugs_to_32_characters_at_a_word_boundary():
    text = "Add " + "x" * 23 + " parser for every ledger row"
    (item,) = executor.parse_sub_intent_list(_sub_intent_plan(f"- {text}"))

    assert item["slug"] == "add-" + "x" * 23
    assert item["goal"] == text


def test_parse_sub_intent_list_strips_leading_hyphens_before_truncating_the_slug():
    (item,) = executor.parse_sub_intent_list(
        _sub_intent_plan("- **Add** the parser for every ledger row and every intent row")
    )

    assert item["slug"] == "add-the-parser-for-every-ledger"


def test_parse_sub_intent_list_falls_back_to_a_generic_slug():
    (item,) = executor.parse_sub_intent_list(_sub_intent_plan("- Добавить парсер"))

    assert item["slug"] == "sub-intent"
    assert item["goal"] == "Добавить парсер"


def test_should_decompose_executes_a_plan_exactly_at_the_budget():
    # why: only entropy strictly above the budget is over budget, so "at or below" executes.
    decision = executor.should_decompose({"entropy": 5.0, "entropy_budget": 5.0}, "# Plan\n")

    assert decision.decision == executor.DECISION_EXECUTE


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (_sub_intent_plan(_indented_body("  ")), executor.DECISION_DECOMPOSED),
        (_sub_intent_plan("- None\n- N/A"), executor.DECISION_OVER_BUDGET),
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
    assert executor.is_placeholder_item(item) is True


def test_should_decompose_treats_a_missing_entropy_as_zero():
    decision = executor.should_decompose({"entropy_budget": 5.0}, "# Plan\n")

    assert (decision.decision, decision.entropy) == (executor.DECISION_EXECUTE, 0.0)


# why: the phrases are built from fragments so this test file does not match itself.
def test_no_placeholder_sub_intent_text_in_source():
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
