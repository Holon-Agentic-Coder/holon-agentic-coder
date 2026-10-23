import pathlib
import re
import unittest

from sandbox_executor.entrypoint import planner

# tests/ -> sandbox-executor/ -> apps/ -> repository root
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PROMPTS_DIR = REPO_ROOT / "holon-config" / "prompts"


def find_unmatched_braces(text: str) -> list[str]:
    """Return a description of every unmatched brace in a prompt template.

    Template brace groups -- placeholders (``{plan_id}``) and instructional groups (``{Explain your choice ...}``, which
    Prettier may wrap across lines) -- never nest, so a template is well-formed when every ``{`` is closed by a ``}``
    before the next ``{`` opens, no ``}`` appears without an open group, and nothing is left open at end of file. This
    is generic: it does not depend on which names are placeholders, so an unclosed ``{plan_id`` is caught either by the
    next ``{`` opening while it is still open or by reaching end of file.
    """
    problems: list[str] = []
    open_line: int | None = None
    for line_no, line in enumerate(text.splitlines(), 1):
        for col, ch in enumerate(line, 1):
            if ch == "{":
                if open_line is not None:
                    problems.append(f"line {line_no}:{col}: '{{' opened while the '{{' from line {open_line} is open")
                open_line = line_no
            elif ch == "}":
                if open_line is None:
                    problems.append(f"line {line_no}:{col}: '}}' without a matching '{{'")
                open_line = None
    if open_line is not None:
        problems.append(f"line {open_line}: '{{' never closed before end of file")
    return problems


class TestPromptTemplates(unittest.TestCase):
    def test_prompt_templates_exist(self):
        """The guard below must actually run against the shipped templates, not silently pass on none."""
        templates = sorted(PROMPTS_DIR.glob("*.template.md"))
        self.assertTrue(templates, f"no prompt templates found under {PROMPTS_DIR}")

    def test_prompt_templates_have_no_unmatched_braces(self):
        """Every shipped prompt template must be free of unmatched '{' and '}'."""
        for template in sorted(PROMPTS_DIR.glob("*.template.md")):
            with self.subTest(template=template.name):
                problems = find_unmatched_braces(template.read_text(encoding="utf-8"))
                self.assertEqual(problems, [], f"{template.name} has unmatched braces:\n" + "\n".join(problems))

    def test_rendered_plan_path_has_no_stray_brace(self):
        """Rendering a shipped template must never produce a 'plans/...' path containing a brace."""
        replacements = {
            "{timestamp}": "1792765112",
            "{agent}": "claude-agent",
            "{model}": "claude-opus-5-5",
            "{safe_model}": "claude-opus-5-5",
            "{plan_id}": "P-1792765112-claude-agent-claude-opus-5-5",
        }
        for template in sorted(PROMPTS_DIR.glob("*.template.md")):
            with self.subTest(template=template.name):
                rendered = planner.render_placeholders(template.read_text(encoding="utf-8"), replacements)
                for path in re.findall(r"plans/[^\s`'\"]+\.md", rendered):
                    self.assertNotRegex(path, r"[{}]", f"{template.name} renders a plan path with a brace: {path}")


class TestFindUnmatchedBraces(unittest.TestCase):
    def test_well_formed_text_passes(self):
        text = "Plan {plan_id} at {timestamp}.\n- **Why:** {Explain the choice, which Prettier\n  may wrap.}\n"
        self.assertEqual(find_unmatched_braces(text), [])

    def test_instructional_groups_pass(self):
        """Instructional groups of any shape are well-formed as long as they close."""
        text = "{references, e.g., safety.md#X}\n{bullets}\n{bullets / short text}"
        self.assertEqual(find_unmatched_braces(text), [])

    def test_detects_unmatched_open_brace_followed_by_another_group(self):
        """An unclosed '{' -- placeholder or not -- is reported when the next '{' opens while it is still open."""
        for text in ("write to plans/{plan_id.md and {agent}", "{any words here\nand then {plan_id}"):
            with self.subTest(text=text):
                problems = find_unmatched_braces(text)
                self.assertTrue(any("opened while" in p for p in problems), problems)

    def test_detects_unmatched_open_brace_at_end_of_file(self):
        """An unclosed '{' -- placeholder or not -- is reported when the file ends while it is still open."""
        for text in ("Plan ID: {plan_id", "Plan {plan_id}\nPlan ID: {plan_id", "trailing {"):
            with self.subTest(text=text):
                problems = find_unmatched_braces(text)
                self.assertTrue(any("never closed" in p for p in problems), problems)

    def test_detects_stray_closing_brace(self):
        problems = find_unmatched_braces("plans/P-1}.md")
        self.assertTrue(any("without a matching" in p for p in problems), problems)
