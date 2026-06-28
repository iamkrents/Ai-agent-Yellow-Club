"""Helper to load pure, dependency-free functions out of the real source files.

We cannot import web_app_server directly in an offline/Linux environment because
its import chain pulls in external services (requests, openpyxl, telegram, the
bundled Windows venv, etc.). Instead we lift the exact source of the pure
functions via AST and exec them in an isolated namespace that only provides the
standard-library names they actually use (re, datetime). This keeps the tests in
sync with the live code without requiring the full runtime.
"""

from __future__ import annotations

import ast
import re
import textwrap
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _segment(src: str, tree: ast.AST, name: str) -> str:
    """Return the source segment of a top-level def/assign by name."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.get_source_segment(src, node)
    raise LookupError(f"top-level symbol {name!r} not found")


def _method_segment(src: str, tree: ast.AST, class_name: str, method: str) -> str:
    """Return the dedented body of a method so it can run as a free function."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == method:
                    return textwrap.dedent(ast.get_source_segment(src, sub))
    raise LookupError(f"method {class_name}.{method} not found")


def load_web_app_pure():
    """Load the pure functions we want to test from web_app_server.py."""
    path = ROOT / "web_app_server.py"
    src = _source(path)
    tree = ast.parse(src)
    ns: dict = {"re": re, "datetime": datetime}

    # Module-level helpers (the second depends on the first + the month table).
    exec(_segment(src, tree, "_MONTH_NAME_TO_NUMBER"), ns)
    exec(_segment(src, tree, "_month_from_staff_question"), ns)
    exec(_segment(src, tree, "_looks_like_mk_month_analytics_question"), ns)

    # Pure staticmethod lifted into a free function.
    method_src = _method_segment(src, tree, "MiniAppContext", "_time_to_minutes")
    # Drop the @staticmethod decorator line if present.
    method_src = "\n".join(
        line for line in method_src.splitlines() if line.strip() != "@staticmethod"
    )
    exec(method_src, ns)

    return ns
