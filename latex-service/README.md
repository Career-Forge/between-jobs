# latex-service

Stateless LaTeX-to-PDF compile service. Built for Sprint 3.2e's renderer
spike (Resume Studio's Structure mode preview) -- see
`docs/UNIFIED_CAPABILITY_MAP.md` in the parent repo for the decision this
service supports.

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

## Commands

```
ruff check . && ruff format --check .
mypy
pytest
```
