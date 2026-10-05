"""Finds every function in the API that filters rows by the caller (`.eq("user_id", ...)`).

Those are the places isolation actually happens: the API reaches Postgres with the service
role, which bypasses row-level security, so a query that forgets its `user_id` filter shows
one user another user's data. The store-level suite must name each of these functions, so
adding a query that filters by the caller without adding a case for it fails a test."""

from __future__ import annotations

import ast
from pathlib import Path

API = Path(__file__).parents[3] / "src" / "between_jobs" / "api"


def _is_owner_filter(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "eq"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "user_id"
    )


def ownership_filtered_functions(api_dir: Path = API) -> dict[str, int]:
    """`{"module.function": number of user_id filters}`, a nested helper counted under the
    top-level function (or `Class.method`) it lives in."""
    found: dict[str, int] = {}
    for path in sorted(api_dir.glob("*.py")):
        tree = ast.parse(path.read_text())

        def visit(node: ast.AST, owner: str | None, *, module: str = path.stem) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, child.name, module=module)
                    continue
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    name = child.name if owner is None else f"{owner}.{child.name}"
                    hits = sum(1 for n in ast.walk(child) if _is_owner_filter(n))
                    if hits:
                        found[f"{module}.{name}"] = found.get(f"{module}.{name}", 0) + hits
                    continue
                visit(child, owner, module=module)

        visit(tree, None)
    return found


def all_functions(api_dir: Path = API) -> set[str]:
    """Every "module.function" (or "module.Class.method") in the API, filtered or not."""
    names: set[str] = set()
    for path in sorted(api_dir.glob("*.py")):
        tree = ast.parse(path.read_text())

        def visit(node: ast.AST, owner: str | None, *, module: str = path.stem) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, child.name, module=module)
                elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(
                        f"{module}.{child.name if owner is None else f'{owner}.{child.name}'}"
                    )
                else:
                    visit(child, owner, module=module)

        visit(tree, None)
    return names
