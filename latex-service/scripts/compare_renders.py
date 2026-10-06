#!/usr/bin/env python3
"""Compile LaTeX sources through two latex-service images and compare the PDFs.

The point is to prove that changing the image (a new TeX Live snapshot, an added or
removed package) did not change what a document looks like. For every .tex file given,
both images compile it through the service's own HTTP API, then the script compares the
page count, each page's size, and each page rendered to pixels. Any difference is a
failure; fix it (usually by adding the missing TeX Live package) rather than accepting it.

    scripts/compare_renders.py --old latex-service:before --new latex-service:after a.tex b.tex

The .tex files are yours to supply -- the service compiles whatever it is given, and this
script has no templates of its own. Use sources captured from real runs (sanitized), and
include special characters and a multi-page one.

Needs: docker, and poppler's `pdfinfo` and `pdftoppm` on PATH (brew install poppler /
apt install poppler-utils). Nothing else: the pixel comparison is plain Python.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Compiled:
    status: int
    pdf: bytes  # empty unless status == 200
    error_body: str  # the service's JSON error, if any


@dataclass(frozen=True)
class Raster:
    width: int
    height: int
    pixels: bytes  # RGB, row-major


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def start_container(image: str) -> tuple[str, int]:
    name = f"latex-compare-{uuid.uuid4().hex[:8]}"
    _run(["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::5700", image])
    port_line = _run(["docker", "port", name, "5700/tcp"]).stdout.splitlines()[0]
    port = int(port_line.rsplit(":", 1)[1])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
                return name, port
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.5)
    _run(["docker", "stop", name], check=False)
    raise SystemExit(f"{image}: service did not become healthy within 60s")


def compile_source(port: int, tex: Path) -> Compiled:
    body = json.dumps({"latex": tex.read_text(encoding="utf-8")}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/compile", data=body, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return Compiled(response.status, response.read(), "")
    except urllib.error.HTTPError as e:
        return Compiled(e.code, b"", e.read().decode("utf-8", errors="replace"))


def page_sizes(pdf: Path) -> list[str]:
    out = _run(["pdfinfo", "-f", "1", "-l", "9999", str(pdf)]).stdout
    return re.findall(r"^Page\s+\d+ size:\s+(.+)$", out, re.MULTILINE)


def render_pages(pdf: Path, prefix: Path, dpi: int) -> list[Path]:
    """Render to PPM (a raw format plain Python can read) and PNG (for a human to look at)."""
    _run(["pdftoppm", "-r", str(dpi), str(pdf), str(prefix)])
    _run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(prefix)])
    return sorted(prefix.parent.glob(f"{prefix.name}-*.ppm"))


def read_ppm(path: Path) -> Raster:
    data = path.read_bytes()
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if match is None:
        raise ValueError(f"{path}: not a binary 8-bit PPM")
    return Raster(int(match.group(1)), int(match.group(2)), data[match.end() :])


def diff_rasters(a: Raster, b: Raster, tolerance: int) -> tuple[int, int]:
    """(pixels differing by more than `tolerance` in any channel, largest channel delta)."""
    if a.pixels == b.pixels:
        return 0, 0
    differing = 0
    largest = 0
    for i in range(0, len(a.pixels), 3):
        delta = max(
            abs(a.pixels[i] - b.pixels[i]),
            abs(a.pixels[i + 1] - b.pixels[i + 1]),
            abs(a.pixels[i + 2] - b.pixels[i + 2]),
        )
        largest = max(largest, delta)
        if delta > tolerance:
            differing += 1
    return differing, largest


def compare_one(
    tex: Path, old: Compiled, new: Compiled, work: Path, dpi: int, tolerance: int
) -> bool:
    ok = True
    print(f"\n{tex.name}")
    if old.status != new.status:
        print(f"  FAIL  HTTP status differs: old {old.status}, new {new.status}")
        return False
    if old.status != 200:
        print(f"  both images answered HTTP {old.status} (the error path); not a render comparison")
        return True

    pdfs: dict[str, Path] = {}
    for label, compiled in (("old", old), ("new", new)):
        pdfs[label] = work / f"{tex.stem}.{label}.pdf"
        pdfs[label].write_bytes(compiled.pdf)

    sizes = {label: page_sizes(pdf) for label, pdf in pdfs.items()}
    pages = {label: len(s) for label, s in sizes.items()}
    if pages["old"] != pages["new"]:
        print(f"  FAIL  page count: old {pages['old']}, new {pages['new']}")
        return False
    print(f"  pages: {pages['new']}   size: {', '.join(sorted(set(sizes['new'])))}")
    if sizes["old"] != sizes["new"]:
        print(f"  FAIL  page size: old {sizes['old']}, new {sizes['new']}")
        ok = False

    rasters = {
        label: render_pages(pdf, work / f"{tex.stem}.{label}", dpi) for label, pdf in pdfs.items()
    }
    for number, (old_page, new_page) in enumerate(
        zip(rasters["old"], rasters["new"], strict=True), 1
    ):
        a, b = read_ppm(old_page), read_ppm(new_page)
        if (a.width, a.height) != (b.width, b.height):
            print(
                f"  FAIL  page {number}: raster size {a.width}x{a.height} vs {b.width}x{b.height}"
            )
            ok = False
            continue
        strict, largest = diff_rasters(a, b, 0)
        loose = diff_rasters(a, b, tolerance)[0] if strict else 0
        verdict = "ok  " if loose == 0 else "FAIL"
        print(
            f"  {verdict}  page {number}: {a.width}x{a.height}px, differing pixels "
            f"{strict} (any delta), {loose} (delta > {tolerance}), largest delta {largest}/255"
        )
        ok = ok and loose == 0
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--old", required=True, help="reference image (the one to stay equal to)")
    parser.add_argument("--new", required=True, help="image under test")
    parser.add_argument("--dpi", type=int, default=110, help="render resolution (default 110)")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=8,
        help="per-channel delta (0-255) a pixel may differ by before it counts (default 8,\n"
        "about 3%%: anti-aliasing noise, not a moved or missing glyph)",
    )
    parser.add_argument("--out", type=Path, help="keep the PDFs and page images here")
    parser.add_argument("sources", nargs="+", type=Path, help=".tex files to compile")
    args = parser.parse_args()

    for tool in ("docker", "pdfinfo", "pdftoppm"):
        if shutil.which(tool) is None:
            print(f"{tool} not found on PATH", file=sys.stderr)
            return 2

    work = args.out or Path(tempfile.mkdtemp(prefix="latex-compare-"))
    work.mkdir(parents=True, exist_ok=True)
    containers: list[str] = []
    try:
        old_name, old_port = start_container(args.old)
        containers.append(old_name)
        new_name, new_port = start_container(args.new)
        containers.append(new_name)

        all_ok = True
        for tex in args.sources:
            old = compile_source(old_port, tex)
            new = compile_source(new_port, tex)
            all_ok = compare_one(tex, old, new, work, args.dpi, args.tolerance) and all_ok
    finally:
        for name in containers:
            _run(["docker", "stop", name], check=False)
    print(f"\nfiles kept in {work}" if args.out else "")
    print("RESULT:", "identical renders" if all_ok else "DIFFERENCES FOUND")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
