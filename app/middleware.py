"""Custom ASGI middleware for DESS CRM."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import request_id_var

logger = logging.getLogger("app.request")

_INCOMING_HEADER = "x-request-id"
_OUTGOING_HEADER = "X-Request-ID"
_MAX_CALLER_ID_LENGTH = 128


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Stamp every request with a UUID, expose it in logs and response headers.

    Accepts an incoming `X-Request-ID` if the caller sends one (capped at 128
    chars so a malicious client cannot inflate log volume), otherwise generates
    a fresh UUID4. The ID lives in a ContextVar for the duration of the
    request so any log statement inside the handler gets it automatically.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(_INCOMING_HEADER, "")
        rid = (
            incoming
            if 0 < len(incoming) <= _MAX_CALLER_ID_LENGTH
            else str(uuid.uuid4())
        )
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.exception(
                "request_unhandled",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            response.headers[_OUTGOING_HEADER] = rid
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        finally:
            request_id_var.reset(token)
