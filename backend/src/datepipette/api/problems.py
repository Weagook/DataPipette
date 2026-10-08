"""RFC 9457-style safe API problems."""

from typing import Any, cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from datepipette.logging import request_id_context


class ErrorDetail(BaseModel):
    location: list[str]
    message: str
    type: str


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str | None
    errors: list[ErrorDetail]


class ApiProblem(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}


async def api_problem_handler(_request: Request, error: Exception) -> JSONResponse:
    exc = cast(ApiProblem, error)
    payload: dict[str, Any] = {
        "type": f"https://docs.datepipette.dev/problems/{exc.code.replace('_', '-')}",
        "title": exc.title,
        "status": exc.status_code,
        "code": exc.code,
        "detail": exc.detail,
        "request_id": request_id_context.get(),
        "errors": [],
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers=exc.headers,
        media_type="application/problem+json",
    )


async def validation_problem_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    exc = cast(RequestValidationError, error)
    errors = [
        {
            "location": [str(part) for part in item["loc"]],
            "message": item["msg"],
            "type": item["type"],
        }
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "type": "https://docs.datepipette.dev/problems/request-validation-failed",
            "title": "Request validation failed",
            "status": 422,
            "code": "request_validation_failed",
            "detail": "The request contains invalid fields.",
            "request_id": request_id_context.get(),
            "errors": errors,
        },
        media_type="application/problem+json",
    )
