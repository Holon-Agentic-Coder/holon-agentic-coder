"""AST & BM25 Codebase Indexer for RAG-based targeted context injection."""

import ast
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


class RAGCodebaseIndexer:
    """Combines AST symbol extraction and keyword indexing to generate compact codebase context maps."""

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.symbol_map: dict[str, list[dict[str, Any]]] = {}  # symbol_name -> list of locations
        self.file_index: dict[str, list[str]] = {}  # file_path -> lines of text
        self.build_index()

    def build_index(self) -> None:
        """Traverses workspace files, building AST symbol maps and keyword indexes."""
        self.symbol_map.clear()
        self.file_index.clear()

        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [
                d for d in dirs if d not in (".git", ".venv", "__pycache__", "node_modules", "build", ".idea")
            ]

            for file in files:
                if file.endswith((".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh", ".hcl")):
                    rel_path = os.path.relpath(os.path.join(root, file), self.root_dir)
                    full_path = os.path.join(root, file)

                    try:
                        with open(full_path, encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                        lines = content.splitlines()
                        self.file_index[rel_path] = lines

                        if file.endswith(".py"):
                            self._extract_ast_symbols(rel_path, content)
                    except Exception as e:
                        logger.debug("Failed to index file %s: %s", rel_path, e)

    def _extract_ast_symbols(self, rel_path: str, content: str) -> None:
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbol_name = node.name
                    loc = {
                        "file": rel_path,
                        "line": node.lineno,
                        "type": "class" if isinstance(node, ast.ClassDef) else "function",
                    }
                    if symbol_name not in self.symbol_map:
                        self.symbol_map[symbol_name] = []
                    self.symbol_map[symbol_name].append(loc)
        except Exception as e:
            logger.debug("AST parsing skipped for %s: %s", rel_path, e)

    def graph_find_symbol(self, symbol_name: str) -> list[dict[str, Any]]:
        """Finds all occurrences and definitions of a symbol across the AST codebase index."""
        return self.symbol_map.get(symbol_name, [])

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Performs keyword relevance search across the codebase index."""
        query_words = set(re.findall(r"\w+", query.lower()))
        if not query_words:
            return []

        results = []

        for rel_path, lines in self.file_index.items():
            score = 0
            matching_lines = []

            for idx, line in enumerate(lines, start=1):
                line_words = set(re.findall(r"\w+", line.lower()))
                match_count = len(query_words & line_words)
                if match_count > 0:
                    score += match_count
                    matching_lines.append({"line_no": idx, "content": line.strip()})

            if score > 0:
                results.append(
                    {
                        "file": rel_path,
                        "score": score,
                        "matches": matching_lines[:3],
                    }
                )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def build_context_bootstrap(self, query: str | None = None) -> str:
        """Generates a compact context bootstrap summary string for initial intent/plan injection."""
        summary = ["### Codebase Symbol Map & Overview\n"]

        # 1. Top classes & functions
        summary.append("**Indexed Symbols:**")
        for idx, (sym, locs) in enumerate(sorted(self.symbol_map.items())):
            loc_strs = [f"`{loc_item['file']}:{loc_item['line']}`" for loc_item in locs[:2]]
            summary.append(f"- `{sym}` ({locs[0]['type']}): {', '.join(loc_strs)}")
            if idx >= 19:
                break

        if query:
            summary.append("\n**Query Relevant Snippets:**")
            search_res = self.semantic_search(query, top_k=3)
            for res in search_res:
                summary.append(f"File: `{res['file']}` (score: {res['score']})")
                for match in res["matches"]:
                    summary.append(f"  L{match['line_no']}: {match['content']}")

        return "\n".join(summary)
