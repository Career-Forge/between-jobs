"""The child process that reads one uploaded resume file (see `profile_import_extract`).

Started as `python -P -m between_jobs.api.profile_import_worker KIND TIMEOUT MEMORY CPU`. The
file's bytes arrive on standard input; one line of JSON leaves on standard output and nothing
else does. It exists so that a file that is expensive to read, or built to be, can be stopped:
the parent kills this process at the deadline, and the limits below are enforced by the
operating system whether or not any code remembers to check a clock.

Nothing about the file is trusted here, and nothing about the service is available: the parent
starts this process with no secrets in its environment, and the process opens no network
connection and writes no file.
"""

from __future__ import annotations

import sys

from .profile_import_extract import DocumentKind, extraction_reply

try:
    import resource
except ImportError:  # Windows has no such module: the limits below are then simply not set
    resource = None  # type: ignore[assignment]


def _lower_limit(name: str, wanted: int) -> None:
    """Sets one limit to `wanted`, or less if the existing hard limit is lower. A platform
    without that limit, or one that refuses it, just goes without."""
    try:
        which = getattr(resource, name)
        _, hard = resource.getrlimit(which)
        value = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
        resource.setrlimit(which, (value, hard))
    except (AttributeError, ValueError, OSError):
        pass


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def apply_limits(*, memory_bytes: int, cpu_seconds: int) -> None:
    """The child's resource limits, set before it touches the file: CPU time (the process is
    killed past it), no core dump of a stranger's data, no file larger than zero bytes, and, on
    Linux, where the API runs, the address space. Elsewhere that limit is not reliably
    enforced, so it is not set; the parent's kill and the CPU limit still apply."""
    if resource is None:
        return
    limits = [("RLIMIT_CPU", cpu_seconds), ("RLIMIT_CORE", 0), ("RLIMIT_FSIZE", 0)]
    if _is_linux():
        limits.append(("RLIMIT_AS", memory_bytes))
    for name, wanted in limits:
        _lower_limit(name, wanted)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] not in ("pdf", "docx"):
        return 2
    kind: DocumentKind = "pdf" if argv[0] == "pdf" else "docx"
    try:
        timeout, memory_bytes, cpu_seconds = float(argv[1]), int(argv[2]), int(argv[3])
    except ValueError:
        return 2
    apply_limits(memory_bytes=memory_bytes, cpu_seconds=cpu_seconds)
    data = sys.stdin.buffer.read()
    sys.stdout.buffer.write(extraction_reply(data, kind, timeout))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
