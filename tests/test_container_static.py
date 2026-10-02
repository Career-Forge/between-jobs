"""Static guards on the API's container files (launch plan P2.2): they read the
Dockerfile and .dockerignore as text, no Docker needed, so they run in the fast suite
and in CI. What they stop is the quiet kind of regression -- a `COPY . .` that drags a
.env into an image, a root user, a shell that eats SIGTERM, a second worker process,
a package data file the image silently lacks -- none of which fails a build or a test.

That the image really builds, runs as it says and serves /health was checked against
the built image by hand when these files were written (P2.2) and again after an
independent review; the checks below are the properties worth keeping true.

The .dockerignore guards simulate Docker's ignore matching (last matching rule wins; a
rule that matches a directory covers what is inside it; `**` crosses directories) over
the real file tree, rather than checking that certain strings appear in the file. An
earlier version did the latter and passed while the file let a `.env` under `src/`
straight through: Docker's bare `.env` matches only at the root of the build context.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_DOCKERFILE = (_ROOT / "Dockerfile").read_text()
_RULES = [
    line.strip()
    for line in (_ROOT / ".dockerignore").read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]


# -- a small model of Docker's ignore matching ---------------------------------------


def _regex(pattern: str) -> re.Pattern[str]:
    """One .dockerignore pattern as a regex over a '/'-separated path."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


_COMPILED = [(rule.startswith("!"), _regex(rule.removeprefix("!").strip("/"))) for rule in _RULES]


def _in_context(path: str) -> bool:
    """Whether `path` (relative to the repo root) reaches the build context. Rules are
    applied in order and the last one that matches decides; a rule matches a path if it
    matches the path itself or any directory above it."""
    parts = path.split("/")
    candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    included = True
    for negated, rx in _COMPILED:
        if any(rx.fullmatch(c) for c in candidates):
            included = negated
    return included


def _package_files() -> list[str]:
    """Every file the installed package is made of, as it sits in the working tree."""
    base = _ROOT / "src" / "between_jobs"
    found = []
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name in {".DS_Store"} or name.endswith(".pyc"):
                continue
            found.append(str(Path(directory, name).relative_to(_ROOT)))
    return sorted(found)


# -- .dockerignore ---------------------------------------------------------------------


def test_the_ignore_file_is_an_allow_list() -> None:
    """`*` first, then only re-inclusions. A deny-list can never name a file nobody
    thought of (a `.pem`, `secrets.json`, a nested `.env.production`)."""
    assert _RULES[0] == "*"
    assert [r for r in _RULES[1:] if not r.startswith("!") and r != "**/__pycache__"] == []


def test_every_file_the_package_is_made_of_reaches_the_image() -> None:
    """A new data file under the package (a .json, a template) is silently left out of
    the image until it is allowed here; the app would then break at run time, not at
    build time. This fails first."""
    missing = [p for p in _package_files() if not _in_context(p)]
    assert missing == []


def test_the_image_is_built_from_the_package_and_nothing_else_in_the_repo() -> None:
    reaching = {
        entry for entry in os.listdir(_ROOT) if _in_context(entry) and (_ROOT / entry).is_file()
    }
    assert reaching == {"pyproject.toml", "README.md", "LICENSE"} | (
        {"Dockerfile"} & set()  # the Dockerfile itself is always sent by Docker, rule or not
    )
    # ...and under every other top-level directory, nothing but the package's files.
    for directory in (e for e in os.listdir(_ROOT) if (_ROOT / e).is_dir()):
        if directory == "src":
            continue
        for found, _dirs, files in os.walk(_ROOT / directory):
            if any(skip in found for skip in ("node_modules", ".venv", ".git/")):
                continue
            for name in files:
                relative = str(Path(found, name).relative_to(_ROOT))
                assert not _in_context(relative), f"{relative} would reach the build context"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "CLAUDE.local.md",
        "AGENTS.md",
        "src/.env",
        "src/between_jobs/.env",
        "src/between_jobs/api/.env",
        "src/between_jobs/api/.env.production",
        "src/between_jobs/api/prod.env",
        "src/between_jobs/api/.env.bak",
        "src/between_jobs/api/service-account.json",
        "src/between_jobs/api/data/secrets.json",
        "src/between_jobs/server.pem",
        "src/between_jobs/id_rsa",
        "src/between_jobs/docs/notes.md",
        "src/between_jobs/AGENTS.md",
        "src/between_jobs/CLAUDE.local.md",
        "src/AGENTS.md",
        "docs/.env",
        ".git/config",
        "web/.env",
        "tests/test_app.py",
        "supabase/config.toml",
        "scripts/anything.py",
    ],
)
def test_a_secret_or_private_file_never_reaches_the_build_context(path: str) -> None:
    """The decoys a review planted: at the root, nested under src/, with other names
    and extensions, in directories that are ignored and in ones that are not."""
    assert not _in_context(path)


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "src/between_jobs/__init__.py",
        "src/between_jobs/api/app.py",
        "src/between_jobs/engines/anything.py",
        "src/between_jobs/py.typed",
        "src/between_jobs/api/data/geo_country_aliases.json",
    ],
)
def test_what_the_wheel_and_the_app_need_does_reach_the_build_context(path: str) -> None:
    assert _in_context(path)


def test_the_model_agrees_with_docker_on_the_cases_that_caught_the_old_file() -> None:
    """If this model were wrong the tests above would prove nothing; pin the one
    behaviour that matters: a bare name matches only at the root, `**/` matches anywhere."""
    assert not _regex(".env").fullmatch("src/between_jobs/api/.env")
    assert _regex("**/.env").fullmatch("src/between_jobs/api/.env")
    assert _regex("**/.env").fullmatch(".env")


# -- Dockerfile ------------------------------------------------------------------------


def _instructions(keyword: str) -> list[str]:
    """Each `keyword ...` instruction in the Dockerfile, line continuations joined."""
    flat = re.sub(r"\\\n\s*", " ", _DOCKERFILE)
    return [
        line.strip()
        for line in flat.splitlines()
        if line.strip().upper().startswith(keyword.upper() + " ")
    ]


def _stages() -> list[str]:
    return re.split(r"(?im)^FROM\s", _DOCKERFILE)[1:]


def test_the_image_never_runs_as_root() -> None:
    final = _stages()[-1]
    users = re.findall(r"(?im)^USER\s+(\S+)", final)
    assert users, "the runtime stage never drops root"
    assert users[-1].split(":")[0].lower() not in {"root", "0"}  # also `root:root`, `0:0`


def test_nothing_is_copied_wholesale_so_no_secret_can_ride_along() -> None:
    for line in _instructions("COPY") + _instructions("ADD"):
        sources = [t for t in line.split()[1:-1] if not t.startswith("--")]
        for source in sources:
            assert os.path.normpath(source) not in {".", "*"}, line


def test_every_file_the_dockerfile_copies_exists_and_reaches_the_context() -> None:
    for line in _instructions("COPY"):
        if "--from=" in line:
            continue
        for source in [t for t in line.split()[1:-1] if not t.startswith("--")]:
            path = _ROOT / source
            assert path.exists(), f"{source} is COPYed but missing"
            files = (
                [source]
                if path.is_file()
                else [str(p.relative_to(_ROOT)) for p in path.rglob("*.py")]
            )
            assert files and all(_in_context(f) for f in files), f"{source} is not in the context"


def test_it_starts_exactly_one_uvicorn_that_is_pid_1_and_binds_the_platforms_port() -> None:
    cmd = _instructions("CMD")
    assert len(cmd) == 1
    command = cmd[0]
    assert "exec uvicorn between_jobs.api.app:app" in command  # exec: PID 1 gets SIGTERM
    assert "--host 0.0.0.0" in command
    assert "--port ${PORT:-8000}" in command  # a blank PORT counts as unset
    # Exactly one process, explicitly: the app starts its own background workers in its
    # lifespan, so each extra process would run all of them again, and uvicorn takes its
    # worker count from $WEB_CONCURRENCY (which some platforms set) unless told.
    assert re.search(r"--workers 1(?!\d)", command)
    assert "--reload" not in command
    assert "--timeout-graceful-shutdown" in command


def test_the_graceful_shutdown_window_is_shorter_than_the_documented_platform_grace() -> None:
    window = int(re.search(r"--timeout-graceful-shutdown\s+(\d+)", _DOCKERFILE).group(1))  # type: ignore[union-attr]
    readme = (_ROOT / "README.md").read_text()
    documented = int(re.search(r"longer than\s+(\d+)\s+seconds", readme).group(1))  # type: ignore[union-attr]
    assert window <= documented


def test_every_stage_uses_the_python_the_project_requires() -> None:
    requires = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    minimum = re.search(r">=\s*(\d+\.\d+)", requires).group(1)  # type: ignore[union-attr]
    default = re.search(r"(?m)^ARG PYTHON_VERSION=(\S+)", _DOCKERFILE).group(1)  # type: ignore[union-attr]
    assert default == minimum, "the image should run on the oldest Python the project supports"
    for stage in _stages():
        assert stage.startswith("python:${PYTHON_VERSION}-slim"), stage.splitlines()[0]


def test_the_runtime_stage_has_unbuffered_output() -> None:
    assert "PYTHONUNBUFFERED=1" in _stages()[-1]  # the API logs JSON lines to stdout


def test_the_healthcheck_follows_the_same_port_default_as_the_command() -> None:
    health = "\n".join(_instructions("HEALTHCHECK"))
    assert "os.environ.get('PORT') or '8000'" in health
