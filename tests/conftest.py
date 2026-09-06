"""Repo-wide test fixtures.

Every route test in this repo boots the real FastAPI app (and thus its
`lifespan`) via TestClient/ASGI against a stubbed, unreachable
SUPABASE_URL. `app.py`'s outbox worker (Horizon Sprint 4.0), job
registry poller worker (Job Finder P2), saved-search matcher worker
(Job Finder P9b), and Gmail reply checker worker (Gmail reply/status
parsing R3) all poll Supabase immediately on startup -- without
disabling them here, that first poll would throw an unhandled connection
error inside its own background task on every single test run. This is
the one thing every test file needs regardless of what it's actually
testing, so it lives here rather than being copy-pasted into each file's
own `_stub_env` fixture.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_outbox_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_OUTBOX_WORKER", "1")
    monkeypatch.setenv("DISABLE_JOB_REGISTRY_POLLER", "1")
    monkeypatch.setenv("DISABLE_SAVED_SEARCH_MATCHER", "1")
    monkeypatch.setenv("DISABLE_GMAIL_REPLY_CHECKER", "1")
