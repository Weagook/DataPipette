"""Process liveness and dependency readiness endpoints."""

from typing import Literal

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from datepipette.application.health import ReadinessProbe

router = APIRouter(prefix="/health", tags=["operations"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class DependencyResponse(BaseModel):
    status: Literal["available", "unavailable"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, DependencyResponse]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Report whether the API process can serve requests."""

    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Report whether required infrastructure is currently available."""

    probe: ReadinessProbe = request.app.state.readiness_probe
    result = await probe.check()
    response = ReadinessResponse(
        status="ready" if result.ready else "unavailable",
        checks={
            name: DependencyResponse(status="available" if dependency.available else "unavailable")
            for name, dependency in result.checks.items()
        },
    )
    if result.ready:
        return response
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=response.model_dump(mode="json"),
    )
