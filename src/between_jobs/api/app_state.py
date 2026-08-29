"""Shared FastAPI dependency-provider functions, reading from `app.state`.

Split out from app.py so route modules (e.g. telegram_webhook.py) can
depend on these without importing the app module itself -- app.py needs
to include those routers, so the reverse import would be circular.
"""

from __future__ import annotations

from typing import cast

import httpx
from fastapi import Request
from supabase import AsyncClient

from .telegram_client import TelegramClient


def get_supabase(request: Request) -> AsyncClient:
    return cast(AsyncClient, request.app.state.supabase)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return cast(httpx.AsyncClient, request.app.state.http)


def get_telegram_client(request: Request) -> TelegramClient:
    return cast(TelegramClient, request.app.state.telegram_client)


def get_webhook_secret(request: Request) -> str:
    return cast(str, request.app.state.telegram_webhook_secret)
