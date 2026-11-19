"""Keeps short-lived bearer tokens out of access logs.

A playback link carries its token in the query string (`?t=...`) because a <video>
element cannot send an Authorization header. The token is short-lived and scoped to
one recording, but it must still never be written to disk or shipped to a log service.
"""
from __future__ import annotations

import logging
import re

_TOKEN_PARAM = re.compile(r"([?&](?:t|token|access_token)=)[^&\s\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    return _TOKEN_PARAM.sub(r"\1[redacted]", text)


class RedactTokensFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


def install() -> None:
    """Idempotent. Attaches to the loggers that print request URLs."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactTokensFilter) for f in logger.filters):
            logger.addFilter(RedactTokensFilter())
