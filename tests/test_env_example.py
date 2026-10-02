"""`.env.example` lists every environment variable the API reads.

The README sends people there for the full list, and a self-hoster running the container
has nothing else to go on: a variable that is read but not listed (a service address
that defaults to localhost, a feature switch) fails quietly -- the LaTeX service showed up
as `unreachable` in /health with no way to learn the name of the setting that fixes it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent

# Set by the platform the API runs on, not by the operator.
_PLATFORM_PROVIDED = {"RAILWAY_DEPLOYMENT_ID"}


def _variables_read_by_the_code() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}

    def record(name: object, path: Path) -> None:
        if isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            found.setdefault(name, set()).add(str(path.relative_to(_ROOT / "src")))

    for path in sorted((_ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Subscript)
                and ast.unparse(node.value).endswith("environ")
                and isinstance(node.slice, ast.Constant)
            ):
                record(node.slice.value, path)
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            reads_environ = (
                isinstance(func, ast.Attribute)
                and func.attr in {"get", "getenv", "pop"}
                and ast.unparse(func.value) in {"os", "os.environ"}
            )
            if (
                (name in {"require_env", "optional_env", "getenv"} or reads_environ)
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                record(node.args[0].value, path)
            for keyword in node.keywords:
                if keyword.arg == "disable_env" and isinstance(keyword.value, ast.Constant):
                    record(keyword.value.value, path)
    return found


def _documented() -> set[str]:
    text = (_ROOT / ".env.example").read_text()
    return set(re.findall(r"(?m)^#?\s*([A-Z][A-Z0-9_]{2,})=", text))


def test_the_scan_finds_the_variables_it_should() -> None:
    """Guards the guard: if the scan stopped matching, the test below would pass on nothing."""
    read = _variables_read_by_the_code()
    assert len(read) >= 20
    assert {
        "SUPABASE_URL",
        "WORKER_LEASES",
        "LATEX_SERVICE_BASE_URL",
        "DISABLE_OUTBOX_WORKER",
    } <= set(read)


def test_every_variable_the_code_reads_is_in_the_example_file() -> None:
    undocumented = {
        name: sorted(files)
        for name, files in _variables_read_by_the_code().items()
        if name not in _documented() and name not in _PLATFORM_PROVIDED
    }
    assert undocumented == {}
