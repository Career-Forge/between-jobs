# latex-service

Stateless LaTeX-to-PDF compile service. When the Between Jobs API produces a
resume or cover-letter PDF it sends the LaTeX here and gets the PDF back (set
`LATEX_SERVICE_BASE_URL` on the API to point at this service).

One endpoint that matters: `POST /compile` with `{"latex": "..."}`,
returns the compiled PDF bytes (`application/pdf`) or a structured error
with the `pdflatex` log attached. Content-agnostic on purpose -- this
service has no knowledge of forge-engines' templates or calibration; it
compiles whatever LaTeX source it's given, so the preview it produces is
never at risk of drifting from the actual exported artifact.

## Running locally

Needs a `pdflatex` on `PATH` (this repo doesn't assume one is installed --
use the Docker image instead unless you already have a TeX distribution):

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # pdflatex-dependent tests skip if pdflatex is absent
uvicorn latex_service.app:app --port 5700
```

## Running via Docker

```
docker build -t latex-service .
docker run --rm -p 5700:5700 latex-service
```

To run the test suite against the image's real `pdflatex` (nothing skips):

```
docker run --rm --user root -v "$PWD/tests:/app/tests:ro" latex-service \
  sh -c '/opt/venv/bin/pip install -q pytest pytest-asyncio httpx && cd /app && /opt/venv/bin/pytest -p no:cacheprovider'
```

The service runs as an unprivileged user (uid 10001) and writes only to a temp
directory, so `docker run --read-only --tmpfs /tmp ...` works.

## How the image is built

The `Dockerfile` pins every input, so a rebuild gives the same TeX Live:

- **Base image**: `python:3.12.15-slim-trixie`, by tag and digest (`PYTHON_IMAGE`).
  The digest is what Docker resolves. The tag is for people.
- **TeX Live**: installed in a build stage from one dated snapshot of the `tlnet`
  package repository (`TLNET_SNAPSHOT`, served by the daily archive at
  <https://texlive.info/tlnet-archive/>), starting from `scheme-infraonly` (no
  engines, no packages), without docs or sources. The installer is checked against a
  pinned SHA-512 (`INSTALL_TL_SHA512`) and the package database is signature-verified.
- **Packages**: an explicit list in the `Dockerfile`, with a comment per group saying
  why it is there. Only what the resume and cover-letter templates load is installed
  (plus what `latex-bin` and the other listed packages declare as dependencies).
- **A canary compile** at the end of the build stage loads every package the templates
  load. A missing package fails the build, not a user's PDF.
- Only `pdflatex` (and `pdftex`, `kpsewhich`) are on `PATH`. There is no `xelatex` or
  `lualatex`, and no `-shell-escape`: the service never passes it.

The TeX Live layers come before `COPY src`, so editing the Python rebuilds only the
last two layers. Compared with the old `texlive/texlive:latest` base (the full scheme,
unpinned) the image is roughly 570 MB unpacked instead of 9 GB.

### Bumping a pin

- **Base image**: pick the tag at <https://hub.docker.com/_/python>, then
  `docker buildx imagetools inspect python:<tag>` for its digest. Edit `PYTHON_IMAGE`.
- **TeX Live snapshot**: pick a date that exists under
  <https://texlive.info/tlnet-archive/>, set `TLNET_SNAPSHOT` to `YYYY/MM/DD`, and set
  `INSTALL_TL_SHA512` to the contents of
  `https://texlive.info/tlnet-archive/YYYY/MM/DD/tlnet/install-tl-unx.tar.gz.sha512`.
  Both can also be passed as `--build-arg`. That archive is a third-party host; if it
  is unreachable the build fails (it never falls back to a moving repository). Once a
  TeX Live release is over, its final repository is frozen in the TUG historic archive
  (for example `https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final`);
  point `TLNET_REPOSITORY` there if you need a pin that outlives the snapshot archive.

Then run the comparison below before shipping.

### Adding a TeX Live package

A template that starts using a new package fails with HTTP 422 and
`File 'foo.sty' not found` in the log. To fix it:

1. Find the TeX Live package that ships the file. The name is usually the CTAN name, but
   not always (`fullpage.sty` is in `preprint`). This prints it for the pinned snapshot:
   ```
   curl -sL https://texlive.info/tlnet-archive/2026/10/04/tlnet/tlpkg/texlive.tlpdb.xz \
     | xz -dc | awk -v f=/foo.sty '/^name /{n=$2} substr($0,length($0)-length(f)+1)==f{print n}' | sort -u
   ```
2. Add it to the `tlmgr install` list in the `Dockerfile`, with a note in the comment
   block saying what needs it. If a template loads it directly, add its `\usepackage`
   line to the canary compile too.
3. Rebuild. If the compile now fails on a *different* missing file, that is a dependency
   the package does not declare (`lato` needs `fontaxes` and `xkeyval`, for example). Add
   that one as well, with the same note. To list every file a compile reads, run
   `pdflatex -recorder` and read the `.fls` file.
4. Run the render comparison.

## Checking that a change did not alter the output

`scripts/compare_renders.py` compiles the same sources through two images and compares
the page count, the page sizes and every page rendered to pixels. Any difference is a
failure to fix, not to accept -- a substituted font or a missing glyph shows up as
changed pixels.

```
docker build -t latex-service:before <checkout of the commit you are replacing>/latex-service
docker build -t latex-service:after .
scripts/compare_renders.py --old latex-service:before --new latex-service:after \
  sample-resume.tex sample-cover-letter.tex
```

It needs `docker` and poppler's `pdfinfo` and `pdftoppm` (`brew install poppler`, or
`apt install poppler-utils`). It ships no sources of its own: give it `.tex` files
captured from real runs (sanitized -- no real names or contact details), including a
multi-page one and one with special characters (`& % $ # _ { } ~ ^ \`) and non-ASCII
letters. Pass `--out DIR` to keep the PDFs and page images for a look.

For a stricter check, run both images with `-e SOURCE_DATE_EPOCH=1700000000 -e
FORCE_SOURCE_DATE=1`: pdfTeX then writes no timestamp or random ID, and the PDFs from
two equivalent images are byte-identical (`cmp`).

## Commands

```
ruff check . && ruff format --check .
mypy
pytest
```
