# syntax=docker/dockerfile:1
#
# The Between Jobs API: one container, one uvicorn process.
#
# One process on purpose. The API starts its background workers (job registry
# poller, outbox, saved-search matcher, Gmail reply checker, cache purge) in its own
# lifespan, so `--workers N` would run all of them N times in one container. The
# three that have no claim of their own hold a lease so a second copy stands by
# (WORKER_LEASES, see .env.example), but that is for an overlapping deploy or a
# stray replica, not a way to scale: scale by running more containers behind a
# platform's load balancer if it ever comes to that.
#
# Configuration is environment variables (see .env.example); nothing is baked in, and
# .dockerignore keeps .env out of the build. SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
# are required; Telegram is optional. The database migrations are not run here -- apply
# them first (`supabase db push`), or the leased workers report `lease_unknown` and
# /health answers 503, on purpose.
ARG PYTHON_VERSION=3.12

# -- build: dependencies and the package, into a venv we can copy out whole -----------
FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
# hatchling reads the readme and the license file for the wheel's metadata.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

# -- runtime ------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="between-jobs api" \
      org.opencontainers.image.source="https://github.com/Career-Forge/between-jobs" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# PYTHONUNBUFFERED: the API logs JSON lines to stdout; they must not sit in a buffer.
# PORT: a platform sets its own at run time; this is only the default for `docker run`.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PORT=8000

# Not root: nothing here needs it, and the container holds the Supabase service-role key.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin app

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER app

EXPOSE 8000

# For `docker run` / compose users (a platform such as Railway runs its own check).
# /health is 503 when a worker has died, gone stale, or cannot ask its lease.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=4)"]

# `exec` makes uvicorn PID 1, so the platform's SIGTERM reaches it directly instead of
# a shell that would swallow it. --timeout-graceful-shutdown is how long uvicorn waits
# for open requests after SIGTERM before the lifespan shutdown stops the workers; keep
# it below the platform's SIGTERM-to-SIGKILL window (on Railway:
# RAILWAY_DEPLOYMENT_DRAINING_SECONDS, whose default is 0 -- set it, e.g. to 30).
CMD ["sh", "-c", "exec uvicorn between_jobs.api.app:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 20"]
