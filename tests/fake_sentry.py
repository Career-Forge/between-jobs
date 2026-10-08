"""A stand-in for the `sentry_sdk` package, so the error-reporting tests need nothing
installed and never touch a network.

`install` puts fakes of `sentry_sdk` and the three integration modules the code imports
into `sys.modules` (undone by the test's `monkeypatch`). The fake records what the code
asked of it; tests assert on that. Tests that need the real package are in
test_error_reporting_real_sdk.py and skip when it is absent."""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

import pytest


def _integration_class(name: str) -> type[Any]:
    def __init__(self: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    return type(name, (), {"__init__": __init__})


class FakeSentry:
    def __init__(self) -> None:
        self.init_calls: list[dict[str, Any]] = []
        self.captured: list[tuple[BaseException, dict[str, Any]]] = []
        self.flush_calls: list[tuple[float | None, int]] = []
        self.init_error: BaseException | None = None
        self.capture_error: BaseException | None = None
        self.flush_error: BaseException | None = None
        self.flush_gate: threading.Event | None = None
        self.flush_delay = 0.0
        self.StarletteIntegration: type[Any] = _integration_class("StarletteIntegration")
        self.FastApiIntegration: type[Any] = _integration_class("FastApiIntegration")
        self.LoggingIntegration: type[Any] = _integration_class("LoggingIntegration")

    # -- the SDK surface the code uses --------------------------------------------
    def init(self, **options: Any) -> None:
        self.init_calls.append(options)
        if self.init_error is not None:
            raise self.init_error

    def capture_exception(self, error: BaseException, **scope: Any) -> None:
        if self.capture_error is not None:
            raise self.capture_error
        self.captured.append((error, scope))

    def flush(self, timeout: float | None = None) -> None:
        self.flush_calls.append((timeout, threading.get_ident()))
        if self.flush_gate is not None:
            self.flush_gate.wait(30)
        if self.flush_delay:
            threading.Event().wait(self.flush_delay)
        if self.flush_error is not None:
            raise self.flush_error

    # -- wiring ----------------------------------------------------------------------
    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
        def module(name: str, **attributes: Any) -> types.ModuleType:
            mod = types.ModuleType(name)
            for key, value in attributes.items():
                setattr(mod, key, value)
            return mod

        for mod in (
            module(
                "sentry_sdk",
                init=self.init,
                capture_exception=self.capture_exception,
                flush=self.flush,
            ),
            module("sentry_sdk.integrations"),
            module(
                "sentry_sdk.integrations.starlette", StarletteIntegration=self.StarletteIntegration
            ),
            module("sentry_sdk.integrations.fastapi", FastApiIntegration=self.FastApiIntegration),
            module("sentry_sdk.integrations.logging", LoggingIntegration=self.LoggingIntegration),
        ):
            monkeypatch.setitem(sys.modules, mod.__name__, mod)
        return self

    @property
    def options(self) -> dict[str, Any]:
        (options,) = self.init_calls
        return options
