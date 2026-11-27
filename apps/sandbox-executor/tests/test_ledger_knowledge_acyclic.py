from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sandbox_executor import credential_redactor, knowledge
from sandbox_executor.credential_redactor import CredentialRedactor
from sandbox_executor.knowledge import records as knowledge_records
from sandbox_executor.ledger import ActionRecord, generate_action_id, try_append_action_record

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
_LEDGER_DIR = _SRC_ROOT / "sandbox_executor" / "ledger"
_CREDENTIAL_REDACTOR_FILE = _SRC_ROOT / "sandbox_executor" / "credential_redactor.py"


def _package_parts_for_file(file_path: Path, src_root: Path) -> tuple[str, ...]:
    return file_path.relative_to(src_root).parent.parts


def _resolve_import_from(node: ast.ImportFrom, package_parts: tuple[str, ...]) -> list[str]:
    if node.level > 0:
        pop_count = node.level - 1
        base_parts = package_parts[: len(package_parts) - pop_count] if pop_count > 0 else package_parts
        prefix = ".".join((*base_parts, node.module)) if node.module else ".".join(base_parts)
    else:
        prefix = node.module or ""

    targets: list[str] = []
    if prefix:
        targets.append(prefix)
    for alias in node.names:
        if alias.name == "*":
            continue
        targets.append(f"{prefix}.{alias.name}" if prefix else alias.name)
    return targets


def _extract_imported_modules(source: str, package_parts: tuple[str, ...]) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modules.update(_resolve_import_from(node, package_parts))
    return modules


def _matches_prefix(module_name: str, forbidden_prefix: str) -> bool:
    return module_name == forbidden_prefix or module_name.startswith(f"{forbidden_prefix}.")


def _find_forbidden_imports(package_dir: Path, src_root: Path, forbidden_prefix: str) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for py_file in sorted(package_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        package_parts = _package_parts_for_file(py_file, src_root)
        rel_path = str(py_file.relative_to(src_root))
        for imported in sorted(_extract_imported_modules(source, package_parts)):
            if _matches_prefix(imported, forbidden_prefix):
                violations.append((rel_path, imported))
    return violations


def _fragment(length: int) -> str:
    return (uuid4().hex * 4)[:length]


def test_ast_helper_detects_absolute_relative_and_deferred_knowledge_imports() -> None:
    package_parts = ("sandbox_executor", "ledger")
    cases = [
        "import sandbox_executor.knowledge\n",
        "from sandbox_executor import knowledge\n",
        "from sandbox_executor.knowledge.records import CredentialRedactor\n",
        "from .. import knowledge\n",
        "from ..knowledge.records import CredentialRedactor\n",
        "def f():\n    from sandbox_executor.knowledge.records import CredentialRedactor\n",
        "def f():\n    from ..knowledge import records\n",
    ]
    for source in cases:
        imported = _extract_imported_modules(source, package_parts)
        assert any(_matches_prefix(m, "sandbox_executor.knowledge") for m in imported), source


def test_ledger_package_has_zero_knowledge_imports() -> None:
    violations = _find_forbidden_imports(_LEDGER_DIR, _SRC_ROOT, "sandbox_executor.knowledge")
    assert violations == []


def test_credential_redactor_is_acyclic_leaf_module() -> None:
    source = _CREDENTIAL_REDACTOR_FILE.read_text(encoding="utf-8")
    imported = _extract_imported_modules(source, ("sandbox_executor",))
    for target in imported:
        assert not _matches_prefix(target, "sandbox_executor.ledger")
        assert not _matches_prefix(target, "sandbox_executor.knowledge")
        top_level = target.split(".", 1)[0]
        is_allowed = (
            top_level in sys.stdlib_module_names
            or top_level == "__future__"
            or _matches_prefix(target, "sandbox_executor.redaction")
        )
        assert is_allowed, f"Unexpected import in credential_redactor.py: {target}"


def test_knowledge_reexports_credential_redactor_identically() -> None:
    assert knowledge_records.CredentialRedactor is credential_redactor.CredentialRedactor
    assert knowledge.CredentialRedactor is credential_redactor.CredentialRedactor


@pytest.mark.parametrize(
    ("text_factory", "expected_marker"),
    [
        (lambda s: f"key={'sk' + '-' + s[:24]}", "[REDACTED_SECRET]"),
        (lambda s: f"key={'sk' + '-ant-' + s[:24]}", "[REDACTED_SECRET]"),
        (lambda s: f"key={'AI' + 'za' + s[:35]}", "[REDACTED_SECRET]"),
        (lambda s: f"pat={'gh' + 'p_' + s[:36]}", "[REDACTED_SECRET]"),
        (lambda s: f"pat={'github_' + 'pat_' + s[:82]}", "[REDACTED_SECRET]"),
        (lambda s: f"Authorization: Bearer {s[:20]}", "Bearer [REDACTED_SECRET]"),
        (
            lambda s: f"-----BEGIN RSA PRIVATE KEY-----\n{s[:32]}\n-----END RSA PRIVATE KEY-----",
            "[REDACTED_PRIVATE_KEY]",
        ),
        (lambda s: f"https://api.example.com/v1?token={s[:16]}&ok=1", "?token=[REDACTED_SECRET]&ok=1"),
        (lambda s: f'api_key="{s[:16]}"', 'api_key="[REDACTED_SECRET]"'),
        (lambda s: f"https://user:{s[:16]}@host.example/path", "*******"),
        (lambda s: f"token={'gh' + 'o_' + s[:24]}", "*******"),
        (lambda s: f"token={'sk_' + 'live_' + s[:16]}", "*******"),
    ],
)
def test_credential_redactor_redact_text_parity(text_factory, expected_marker: str) -> None:
    secret = _fragment(90)
    raw = text_factory(secret)
    redacted = CredentialRedactor.redact_text(raw)
    assert secret[:12] not in redacted
    assert expected_marker in redacted


def test_credential_redactor_redact_structure_and_edge_cases() -> None:
    secret_a = _fragment(24)
    secret_b = _fragment(24)
    key_token = "sk" + "-" + secret_a
    val_token = "AI" + "za" + _fragment(35)
    payload = {
        f"header:{key_token}": [
            {"nested": f"Bearer {secret_b}"},
            val_token,
            42,
            None,
            True,
            "",
        ],
    }

    redacted = CredentialRedactor.redact_structure(payload)
    assert CredentialRedactor.redact_text("") == ""
    assert CredentialRedactor.redact_structure(123) == 123
    assert CredentialRedactor.redact_structure(None) is None

    serialized = json.dumps(redacted)
    assert secret_a not in serialized
    assert secret_b not in serialized
    assert val_token not in serialized
    assert "[REDACTED_SECRET]" in serialized
    (redacted_key,) = redacted.keys()
    assert redacted[redacted_key][2:] == [42, None, True, ""]


def test_try_append_action_record_redacts_via_extracted_redactor(tmp_path: Path) -> None:
    secret = _fragment(24)
    token = "sk" + "-" + secret
    ledger_path = tmp_path / "executions.jsonl"

    action_rec = ActionRecord(
        action_id=generate_action_id(),
        execution_id="E-1",
        action="run",
        category="code",
        target="target",
        outcome="failure",
        error_context=f"failed with {token}",
    )
    written_action = try_append_action_record(str(ledger_path), action_rec, label="test-action")
    assert written_action is not None

    dict_rec = {
        "action_id": generate_action_id(),
        "execution_id": "E-1",
        "action": "run",
        "category": "code",
        "target": "target",
        "outcome": "failure",
        "error_context": f"https://user:{secret}@host.example/repo",
    }
    written_dict = try_append_action_record(str(ledger_path), dict_rec, label="test-dict")
    assert written_dict is not None

    content = ledger_path.read_text(encoding="utf-8")
    assert secret not in content
    rows = [json.loads(line) for line in content.splitlines()]
    assert rows[0]["error_context"] == "failed with [REDACTED_SECRET]"
    assert "*******" in rows[1]["error_context"]
