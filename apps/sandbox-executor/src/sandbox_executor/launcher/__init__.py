"""Container plumbing for role agents: role dispatch, container launch, credential env-files and the proxy sidecar.

Layering rule: ``sandbox_executor.launcher`` may import the domain modules (``git_flow``, ``budgets``,
``agent_defaults``, ``agent_runner``, ``claude_credential``, ``local_llm``, ``redaction``, ``token_reduction``,
``preflight``, ``naming``) but never ``sandbox_executor.cli`` or ``sandbox_executor.flow``; both of those import the
launcher instead (``tests/test_import_cycle.py`` enforces it).

Patch-target convention: a launcher function calls a helper owned by a sibling module through that module's attribute
(``env_files.env_file_dir()``, not a copied name), so a test patches each helper once, on its owning module
(``sandbox_executor.launcher.<module>.<name>``).
"""
