"""Request ids and structured logs, shared by all four services.

A page load in core-api fans out to two or three internal services. Without a
shared id, a slow or failing panel meant reading four logs side by side and
matching timestamps. Now every request carries an ``X-Request-ID`` — taken
from the caller when present, minted otherwise — which is echoed on the
response, put on every log line written while handling it, and forwarded by
core-api to the services it calls.

``LOG_FORMAT=json`` switches to one JSON object per line for a log shipper;
the default stays human-readable for a terminal.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Optional

HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
#: A caller-supplied id is accepted only if it looks like one, so a header
#: cannot be used to inject newlines or markup into the logs.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

access_log = logging.getLogger("f1.access")


def current_request_id() -> str:
    return _request_id.get()


def outgoing_headers() -> dict:
    """Headers to forward on a downstream call, so the id follows the request."""
    rid = _request_id.get()
    return {HEADER: rid} if rid != "-" else {}


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for key in ("method", "path", "status", "duration_ms", "service"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(level: str = "INFO", fmt: str = "text", service: str = "") -> None:
    """Replace ``logging.basicConfig``: same levels, plus the request id."""
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        ))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class RequestIdMiddleware:
    """Pure ASGI middleware, so streamed responses (Bernie) are not buffered."""

    def __init__(self, app, service: str = "") -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming: Optional[str] = None
        for name, value in scope.get("headers") or []:
            if name.decode("latin-1").lower() == HEADER.lower():
                incoming = value.decode("latin-1")
                break
        rid = incoming if incoming and _SAFE_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = _request_id.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((HEADER.lower().encode(), rid.encode()))
                message = dict(message, headers=headers)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            if not path.startswith("/health"):
                access_log.info(
                    "%s %s %s %.0fms", scope.get("method"), path, status["code"],
                    (time.perf_counter() - started) * 1000,
                    extra={
                        "method": scope.get("method"), "path": path,
                        "status": status["code"], "service": self.service,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            _request_id.reset(token)
