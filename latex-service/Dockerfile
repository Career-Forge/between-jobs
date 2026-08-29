# Sprint 3.2e renderer spike -- stateless LaTeX-to-PDF compile service.
# Built directly on texlive/texlive rather than multi-stage-copying its
# binaries into a slimmer base -- pdflatex depends on a large, interlinked
# texmf tree (fonts, formats, texmf.cnf) that doesn't survive a partial
# copy cleanly. texlive/texlive's full scheme guarantees every package
# forge-engines' resume template pulls in (lato, hyperref, tabularx,
# fancyhdr, marvosym, glyphtounicode, ...) is present without hand-
# curating a package list against a moving template. Right-sizing the
# image (a smaller texlive scheme + explicit package list, or a proper
# multi-stage copy of just the needed texmf subset) is a real follow-up
# once the renderer decision is final, not a spike concern.
FROM texlive/texlive:latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

EXPOSE 5700
CMD ["/opt/venv/bin/uvicorn", "latex_service.app:app", "--host", "0.0.0.0", "--port", "5700"]
