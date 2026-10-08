"""FastAPI application factory and process entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from datepipette import __version__
from datepipette.api.middleware import RequestContextMiddleware
from datepipette.api.router import api_v1_router
from datepipette.application.health import ReadinessProbe
from datepipette.config import Settings, get_settings
from datepipette.infrastructure.health import InfrastructureReadinessProbe
from datepipette.infrastructure.runtime import create_runtime_resources
from datepipette.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Build an API application with optionally injected test dependencies."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        if readiness_probe is not None:
            app.state.readiness_probe = readiness_probe
            yield
            return

        resources = create_runtime_resources(resolved_settings)
        app.state.readiness_probe = InfrastructureReadinessProbe(
            database_engine=resources.database_engine,
            redis_client=resources.redis_client,
            timeout_seconds=resolved_settings.readiness_timeout_seconds,
        )
        try:
            yield
        finally:
            await resources.close()

    docs_url = "/docs" if resolved_settings.api_docs_enabled else None
    openapi_url = "/openapi.json" if resolved_settings.api_docs_enabled else None
    application = FastAPI(
        title="DatePipette API",
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.include_router(api_v1_router)
    return application


app = create_app()


def run() -> None:
    """Run the development API server."""

    settings = get_settings()
    uvicorn.run(
        "datepipette.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
    )
