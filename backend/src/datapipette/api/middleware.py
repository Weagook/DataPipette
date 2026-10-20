"""Cross-cutting HTTP middleware."""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from datapipette.logging import request_id_context

logger = logging.getLogger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a safe request ID to responses and structured logs."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else str(uuid4())
        )
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            logger.info(
                "HTTP request completed method=%s path=%s status_code=%s duration_ms=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
            )
            request_id_context.reset(token)
