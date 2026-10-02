"""Static guards on the API's container files (launch plan P2.2): they read the
Dockerfile and .dockerignore as text, no Docker needed, so they run in the fast suite
and in CI. What they stop is the quiet kind of regression -- a `COPY . .` that drags a
.env into an image, a root user, a shell that eats SIGTERM, a second worker process --
none of which fails a build or a test.

That the image really builds, runs as it says and serves /health was checked against
the built image by hand when these files were written (P2.2); the checks below are the
properties worth keeping true.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_DOCKERFILE = (_ROOT / "Dockerfile").read_text()
_IGNORED = {
    line.strip()
    for line in (_ROOT / ".dockerignore").read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}


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
    assert users[-1].lower() not in {"root", "0", "0:0"}


def test_nothing_is_copied_wholesale_so_no_secret_can_ride_along() -> None:
    for line in _instructions("COPY") + _instructions("ADD"):
        sources = [t for t in line.split()[1:-1] if not t.startswith("--")]
        assert not any(s in {".", "./", "*"} for s in sources), line


def test_every_file_the_dockerfile_copies_exists_and_is_not_ignored() -> None:
    for line in _instructions("COPY"):
        if "--from=" in line:
            continue
        for source in [t for t in line.split()[1:-1] if not t.startswith("--")]:
            assert (_ROOT / source).exists(), f"{source} is COPYed but missing"
            assert source not in _IGNORED and source.rstrip("/") not in _IGNORED, (
                f"{source} is COPYed but .dockerignore excludes it"
            )


@pytest.mark.parametrize("name", ["README.md", "LICENSE", "pyproject.toml", "src"])
def test_what_hatchling_needs_to_build_the_wheel_is_not_ignored(name: str) -> None:
    """The build reads the readme and the license file for the wheel's metadata."""
    assert name not in _IGNORED


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.*",
        "CLAUDE.local.md",
        "AGENTS.md",
        ".git",
        ".venv",
        "node_modules",
        "web",
        "extension",
        "tests",
        "docs",
        "supabase",
    ],
)
def test_secrets_private_notes_and_bulk_stay_out_of_the_build_context(name: str) -> None:
    assert name in _IGNORED


def test_the_example_env_file_is_the_one_env_file_allowed_in() -> None:
    assert "!.env.example" in _IGNORED


def test_it_starts_exactly_one_uvicorn_that_is_pid_1_and_binds_the_platforms_port() -> None:
    cmd = _instructions("CMD")
    assert len(cmd) == 1
    command = cmd[0]
    assert "exec uvicorn between_jobs.api.app:app" in command  # exec: PID 1 gets SIGTERM
    assert "--port ${PORT}" in command
    assert "--host 0.0.0.0" in command
    # One process: the app starts its own background workers in its lifespan, so each
    # extra worker process would run all of them again.
    assert "--workers" not in command
    assert "--reload" not in command
    assert "--timeout-graceful-shutdown" in command


def test_the_graceful_shutdown_window_is_shorter_than_the_documented_platform_grace() -> None:
    window = int(re.search(r"--timeout-graceful-shutdown\s+(\d+)", _DOCKERFILE).group(1))  # type: ignore[union-attr]
    readme = (_ROOT / "README.md").read_text()
    documented = int(re.search(r"longer than\s+(\d+)\s+seconds", readme).group(1))  # type: ignore[union-attr]
    assert window <= documented


def test_the_python_version_matches_what_the_project_requires() -> None:
    requires = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    minimum = re.search(r">=\s*(\d+\.\d+)", requires).group(1)  # type: ignore[union-attr]
    default = re.search(r"(?m)^ARG PYTHON_VERSION=(\S+)", _DOCKERFILE).group(1)  # type: ignore[union-attr]
    assert default == minimum, "the image should run on the oldest Python the project supports"


def test_the_runtime_stage_is_a_slim_image_with_unbuffered_output() -> None:
    final = _stages()[-1]
    assert final.startswith("python:${PYTHON_VERSION}-slim")
    assert "PYTHONUNBUFFERED=1" in final  # the API logs JSON lines to stdout
