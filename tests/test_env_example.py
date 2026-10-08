"""`.env.example` lists every environment variable the API and the operator scripts read.

The README sends people there for the full list, and a self-hoster running the container
has nothing else to go on: a variable that is read but not listed (a service address
that defaults to localhost, a feature switch) fails quietly -- the LaTeX service showed up
as `unreachable` in /health with no way to learn the name of the setting that fixes it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from between_jobs.api.worker_pings import healthcheck_env_name

_ROOT = Path(__file__).parent.parent

# Set by the platform the API runs on, not by the operator.
_PLATFORM_PROVIDED = {"RAILWAY_DEPLOYMENT_ID"}


def _variables_read_by_the_code() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}

    def record(name: object, path: Path) -> None:
        if isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
            found.setdefault(name, set()).add(str(path.relative_to(_ROOT)))

    paths = sorted([*(_ROOT / "src").rglob("*.py"), *(_ROOT / "scripts").rglob("*.py")])
    for path in paths:
        tree = ast.parse(path.read_text())
        # A module-level constant named like `_SOMETHING_ENV` / `_SOMETHING_ENV_VAR` holds
        # a variable name that is read through the constant (`os.environ.get(_X_ENV)`),
        # which the call matching below cannot see.
        for statement in tree.body:
            if (
                isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Constant)
                and any(
                    isinstance(target, ast.Name) and target.id.endswith(("_ENV", "_ENV_VAR"))
                    for target in statement.targets
                )
            ):
                record(statement.value.value, path)
        for node in ast.walk(tree):
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
                (
                    name in {"require_env", "optional_env", "strict_on_off", "getenv"}
                    or reads_environ
                )
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                record(node.args[0].value, path)
            for keyword in node.keywords:
                if keyword.arg == "disable_env" and isinstance(keyword.value, ast.Constant):
                    record(keyword.value.value, path)
                    # Each worker's healthcheck URL setting is named after its disable flag
                    # (worker_pings.py) and is never written out as a literal in the code.
                    if isinstance(keyword.value.value, str):
                        record(healthcheck_env_name(keyword.value.value), path)
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
        # read through the strict on/off helper, from a module-level constant
        "TESTER_PROGRAM_REQUIRED",
        "LATEX_SERVICE_BASE_URL",
        # read through `optional_env`, then checked as a URL
        "WEB_APP_URL",
        "DISABLE_OUTBOX_WORKER",
        # named after the worker's disable flag, not written out where it is read
        "HEALTHCHECKS_URL_OUTBOX_WORKER",
        "HEALTHCHECKS_URL_HIRING_SIGNAL_CACHE_PURGE",
        # error reporting
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_TRACES_SAMPLE_RATE",
        "RAILWAY_GIT_COMMIT_SHA",
        # read through the id-list helper, from a module-level constant
        "HIRING_SIGNALS_ALLOWED_USER_IDS",
        # read through a module-level constant, not a string literal at the call
        "JOB_SCORING_SYSTEM_PROMPT_PATH",
        # the Discord settings, each read through `optional_env` with its name spelled out
        "DISCORD_APPLICATION_ID",
        "DISCORD_PUBLIC_KEY",
        "DISCORD_BOT_TOKEN",
        # read only by a script under scripts/
        "ATS_FIELD_MAP_SIGNING_KEY",
        "SAMPLE_SOURCE_SUPABASE_URL",
    } <= set(read)


def test_every_variable_the_code_reads_is_in_the_example_file() -> None:
    undocumented = {
        name: sorted(files)
        for name, files in _variables_read_by_the_code().items()
        if name not in _documented() and name not in _PLATFORM_PROVIDED
    }
    assert undocumented == {}
