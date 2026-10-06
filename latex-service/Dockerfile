# syntax=docker/dockerfile:1
#
# The LaTeX compile service: pdflatex behind a small FastAPI app on port 5700.
#
# What ships in this image is pinned, so a rebuild next month gives the same TeX Live:
#   - the base image, by version tag AND digest (the digest is what Docker resolves; the
#     tag is there for the human reading this and for bump tooling);
#   - TeX Live, by a dated snapshot of the tlnet package repository, not "whatever CTAN
#     serves today";
#   - the TeX Live packages, by an explicit list below.
# Nothing here is `latest`. (The build-only tools in the first stage and the Python
# dependencies, which pyproject.toml gives as minimum versions, are not pinned.)
# latex-service/README.md says how to bump each pin and how to prove a bump left the
# output unchanged (scripts/compare_renders.py).
#
# Why not texlive/texlive: the full scheme is 9 GB unpacked, and its slim tags are all
# `latest-*`, which move under you (pinned by digest, latest-minimal plus this package
# list still measured 1.8 GB, and it brings an unpinned Debian testing Python). Installing
# TeX Live ourselves from a frozen snapshot onto a pinned Python image is about a third of
# that and repeatable.

# python:3.12 slim on Debian 13 ("trixie"), the same family as the API image.
# Verify these two are still current at hub.docker.com/_/python before bumping.
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

# -- texlive: install TeX Live from the frozen snapshot, then prove the packages are enough ---
FROM ${PYTHON_IMAGE} AS texlive

# One dated snapshot of the whole tlnet repository (https://texlive.info/tlnet-archive/,
# a daily archive kept since 2019). The tree below is installed from this and nothing
# else. Pick the date for what you want to pin; the installer's checksum must match
# that day's install-tl-unx.tar.gz.sha512 (see the README).
ARG TLNET_SNAPSHOT=2026/10/04
ARG TLNET_REPOSITORY=https://texlive.info/tlnet-archive/${TLNET_SNAPSHOT}/tlnet
ARG INSTALL_TL_SHA512=0df4f94bbada6050e846d6d6bcdb9295e75881235fd00095661288a11696261652c4f02d2db3b0bba506ec0db2c0ac57a948ea11de96a78c0c887df1287da751

# Build-only tools: install-tl and tlmgr are Perl; gnupg lets them verify the signed
# package database. None of this reaches the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gnupg perl wget xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/install-tl
# scheme-infraonly is the bare installer plus tlmgr -- no engines, no packages -- so the
# explicit list further down is the whole story. Settings that matter:
#   instopt_letter 0     the default paper stays A4, as in the texlive/texlive images;
#                        the templates ask for letter themselves.
#   no docs, no sources  the largest part of a stock install, never read by a compile.
# TEXMFHOME, TEXMFCONFIG and TEXMFVAR stay inside the tree rather than under ~: the runtime
# user has no home directory. The profile is written inline so it is reviewable here.
RUN wget -q "${TLNET_REPOSITORY}/install-tl-unx.tar.gz" \
    && echo "${INSTALL_TL_SHA512}  install-tl-unx.tar.gz" | sha512sum -c - \
    && tar -xzf install-tl-unx.tar.gz --strip-components=1 \
    && printf '%s\n' \
        'selected_scheme scheme-infraonly' \
        'TEXDIR /opt/texlive' \
        'TEXMFLOCAL /opt/texlive/texmf-local' \
        'TEXMFSYSCONFIG /opt/texlive/texmf-config' \
        'TEXMFSYSVAR /opt/texlive/texmf-var' \
        'TEXMFCONFIG /opt/texlive/texmf-config' \
        'TEXMFVAR /opt/texlive/texmf-var' \
        'TEXMFHOME /opt/texlive/texmf-local' \
        'instopt_adjustpath 0' \
        'instopt_adjustrepo 0' \
        'instopt_letter 0' \
        'instopt_portable 0' \
        'instopt_write18_restricted 1' \
        'tlpdbopt_autobackup 0' \
        'tlpdbopt_create_formats 1' \
        'tlpdbopt_desktop_integration 0' \
        'tlpdbopt_file_assocs 0' \
        'tlpdbopt_generate_updmap 0' \
        'tlpdbopt_install_docfiles 0' \
        'tlpdbopt_install_srcfiles 0' \
        'tlpdbopt_post_code 1' \
        > texlive.profile \
    && perl install-tl --no-interaction --profile=texlive.profile --repository "${TLNET_REPOSITORY}" \
    && rm -rf /tmp/install-tl

# The explicit package list. tlmgr pulls in each package's declared dependencies, so this
# names what the templates need, not every file; the ones marked "undeclared" are loaded
# at run time but missing from their parent's dependency list, which is why a bare
# `tlmgr install lato` is not enough. To add one: see "Adding a TeX Live package" in the
# README, then re-run the render comparison.
#
#   latex-bin      pdflatex itself, the LaTeX kernel, and the hyphenation patterns the format
#                  is built with (it also brings lualatex's engine; unused, but not worth
#                  hand-pruning).
#   lato           the typeface of both templates (\usepackage[default]{lato}).
#   fontaxes, figureversions, xkeyval
#                  loaded by lato.sty -- undeclared.
#   marvosym       \usepackage{marvosym} in the resume template.
#   titlesec enumitem preprint setspace
#                  section rules, list spacing, fullpage (it lives in the "preprint"
#                  package), line spacing in the cover letter.
#   microtype geometry fancyhdr hyperref babel-english
#                  typography, margins, empty headers/footers, links, English patterns.
#   tools          tabularx and verbatim.
#   graphics       the color package (usenames,dvipsnames).
#   amsfonts       Computer Modern's Type 1 outlines: the math glyphs ($|$, \bullet) are
#                  drawn from them -- undeclared.
#   fontname       the font-name map kpathsea reads on every font lookup -- undeclared.
#   mptopdf        supp-pdf.mkii, which the pdfTeX graphics driver reads -- undeclared.
RUN /opt/texlive/bin/*/tlmgr install \
        latex-bin \
        lato fontaxes figureversions xkeyval \
        marvosym \
        titlesec enumitem preprint setspace \
        microtype geometry fancyhdr hyperref babel-english \
        tools graphics \
        amsfonts fontname mptopdf

# A canary compile: it loads every package the templates load, so a missing one fails the
# build here instead of failing a user's PDF later. Keep it in step with the list above.
RUN mkdir /tmp/canary && cd /tmp/canary \
    && printf '%s\n' \
        '\documentclass[letterpaper,11pt]{article}' \
        '\usepackage{latexsym,fullpage,titlesec,marvosym,verbatim,enumitem,fancyhdr,tabularx,setspace}' \
        '\usepackage[usenames,dvipsnames]{color}' \
        '\usepackage[hidelinks]{hyperref}' \
        '\usepackage[english]{babel}' \
        '\input{glyphtounicode}' \
        '\usepackage[default]{lato}' \
        '\usepackage[T1]{fontenc}' \
        '\usepackage[protrusion=false,expansion=false]{microtype}' \
        '\usepackage[top=0.3in,bottom=0.3in,left=0.4in,right=0.4in]{geometry}' \
        '\begin{document}\textbf{Lato}\ \textit{italic}\ $|$\ $\bullet$\end{document}' \
        > canary.tex \
    && /opt/texlive/bin/*/pdflatex -interaction=nonstopmode -halt-on-error -no-shell-escape canary.tex \
    && rm -rf /tmp/canary

# -- runtime ---------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="between-jobs latex-service" \
      org.opencontainers.image.source="https://github.com/Career-Forge/between-jobs" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

COPY --from=texlive /opt/texlive /opt/texlive
# Put the three binaries that matter on PATH (what `tlmgr path add` does, for just these):
# the compiler the service runs, its engine, and kpsewhich for debugging a missing file.
# Link files, not the directory: kpathsea finds texmf.cnf relative to the real location of
# the binary, which a symlinked bin/ directory on PATH would hide. The glob is the
# architecture directory (x86_64-linux or aarch64-linux).
RUN for b in pdflatex pdftex kpsewhich; do ln -s /opt/texlive/bin/*/"$b" "/usr/local/bin/$b"; done

# Not root: the service compiles text it was handed and writes only to a temp directory.
RUN groupadd --system --gid 10001 latex \
    && useradd --system --uid 10001 --gid latex --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin latex

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

USER latex
EXPOSE 5700
CMD ["/opt/venv/bin/uvicorn", "latex_service.app:app", "--host", "0.0.0.0", "--port", "5700"]
