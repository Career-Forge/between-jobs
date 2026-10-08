"""Log records as the service writes them: through the JSON formatter, after `redact`, with
their `extra={"ctx": ...}` fields. pytest's `caplog.text` renders neither the context nor the
redaction, so an assertion that a secret is absent from `caplog.text` says nothing about the
line that reaches stdout."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from between_jobs.api.logging_setup import JsonFormatter


def rendered(records: Iterable[logging.LogRecord]) -> str:
    formatter = JsonFormatter()
    return "\n".join(formatter.format(record) for record in records)
