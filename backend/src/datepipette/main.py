"""FastAPI application factory and process entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from datepipette import __version__
from datepipette.api.middleware import RequestContextMiddleware
from datepipette.api.problems import (
    ApiProblem,
    api_problem_handler,
    validation_problem_handler,
)
from datepipette.api.router import api_v1_router
from datepipette.application.access_control import (
    DatasetAccessAuthorizer,
    DatasetGrantService,
    GroupService,
)
from datepipette.application.auth_service import AuthenticationService
from datepipette.application.catalog import CatalogService
from datepipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionGrantService,
)
from datepipette.application.connections import ConnectionService
from datepipette.application.dataset_preview import DatasetPreviewService
from datepipette.application.dataset_validation import DatasetValidationService
from datepipette.application.datasets import DatasetService
from datepipette.application.exports import DatasetExportService, ExportDownloadService
from datepipette.application.health import ReadinessProbe
from datepipette.application.refresh_runs import RefreshRunService
from datepipette.application.snapshot_queries import DatasetSnapshotQueryService
from datepipette.application.user_administration import UserAdministrationService
from datepipette.config import Settings, get_settings
from datepipette.infrastructure.catalog import PsycopgSourceCatalogInspector
from datepipette.infrastructure.celery_app import celery_app
from datepipette.infrastructure.crypto import AesGcmSecretCipher
from datepipette.infrastructure.dataset_preview import PsycopgSourceDatasetPreviewer
from datepipette.infrastructure.dataset_validation import PsycopgSourceDatasetValidator
from datepipette.infrastructure.duckdb_snapshot_queries import DuckDbLocalSnapshotQueryExecutor
from datepipette.infrastructure.export_dispatch import CeleryExportDispatcher
from datepipette.infrastructure.export_tokens import EncryptedExportDownloadTokenCodec
from datepipette.infrastructure.health import InfrastructureReadinessProbe
from datepipette.infrastructure.local_exports import LocalExportStorage
from datepipette.infrastructure.rate_limit import RedisLoginRateLimiter
from datepipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datepipette.infrastructure.repositories.access_control import SqlAlchemyAccessControlStore
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datepipette.infrastructure.repositories.connections import SqlAlchemyConnectionStore
from datepipette.infrastructure.repositories.datasets import SqlAlchemyDatasetStore
from datepipette.infrastructure.repositories.exports import SqlAlchemyExportStore
from datepipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datepipette.infrastructure.repositories.snapshot_queries import (
    SqlAlchemySnapshotQueryStore,
)
from datepipette.infrastructure.repositories.user_administration import (
    SqlAlchemyUserAdministrationStore,
)
from datepipette.infrastructure.runtime import create_runtime_resources
from datepipette.infrastructure.snapshot_cursor import EncryptedSnapshotCursorCodec
from datepipette.infrastructure.source_postgres import PsycopgSourceConnectionTester
from datepipette.logging import configure_logging


def create_app(
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
    auth_service: AuthenticationService | None = None,
    user_administration_service: UserAdministrationService | None = None,
    connection_service: ConnectionService | None = None,
    catalog_service: CatalogService | None = None,
    dataset_service: DatasetService | None = None,
    dataset_validation_service: DatasetValidationService | None = None,
    dataset_preview_service: DatasetPreviewService | None = None,
    refresh_run_service: RefreshRunService | None = None,
    snapshot_query_service: DatasetSnapshotQueryService | None = None,
    export_service: DatasetExportService | None = None,
    export_download_service: ExportDownloadService | None = None,
    group_service: GroupService | None = None,
    dataset_grant_service: DatasetGrantService | None = None,
    connection_grant_service: ConnectionGrantService | None = None,
) -> FastAPI:
    """Build an API application with optionally injected test dependencies."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        if readiness_probe is not None:
            app.state.readiness_probe = readiness_probe
            if auth_service is not None:
                app.state.auth_service = auth_service
            if user_administration_service is not None:
                app.state.user_administration_service = user_administration_service
            if connection_service is not None:
                app.state.connection_service = connection_service
            if catalog_service is not None:
                app.state.catalog_service = catalog_service
            if dataset_service is not None:
                app.state.dataset_service = dataset_service
            if dataset_validation_service is not None:
                app.state.dataset_validation_service = dataset_validation_service
            if dataset_preview_service is not None:
                app.state.dataset_preview_service = dataset_preview_service
            if refresh_run_service is not None:
                app.state.refresh_run_service = refresh_run_service
            if snapshot_query_service is not None:
                app.state.snapshot_query_service = snapshot_query_service
            if export_service is not None:
                app.state.export_service = export_service
            if export_download_service is not None:
                app.state.export_download_service = export_download_service
            if group_service is not None:
                app.state.group_service = group_service
            if dataset_grant_service is not None:
                app.state.dataset_grant_service = dataset_grant_service
            if connection_grant_service is not None:
                app.state.connection_grant_service = connection_grant_service
            yield
            return

        resources = create_runtime_resources(resolved_settings)
        app.state.readiness_probe = InfrastructureReadinessProbe(
            database_engine=resources.database_engine,
            redis_client=resources.redis_client,
            timeout_seconds=resolved_settings.readiness_timeout_seconds,
        )
        app.state.auth_service = auth_service or await AuthenticationService.create(
            store=SqlAlchemyAuthenticationStore(resources.session_factory),
            rate_limiter=RedisLoginRateLimiter(
                resources.redis_client,
                resolved_settings.login_rate_limit_attempts,
                resolved_settings.login_rate_limit_window_seconds,
            ),
            session_ttl_seconds=resolved_settings.session_ttl_seconds,
        )
        app.state.user_administration_service = (
            user_administration_service
            or UserAdministrationService(
                SqlAlchemyUserAdministrationStore(resources.session_factory)
            )
        )
        connection_store = SqlAlchemyConnectionStore(resources.session_factory)
        connection_access_store = SqlAlchemyConnectionAccessStore(resources.session_factory)
        connection_authorizer = ConnectionAccessAuthorizer(connection_access_store)
        connection_cipher = AesGcmSecretCipher(
            resolved_settings.connection_encryption_key.get_secret_value()
        )
        app.state.connection_service = connection_service or ConnectionService(
            connection_store,
            connection_cipher,
            PsycopgSourceConnectionTester(
                resolved_settings.source_connect_timeout_seconds,
                resolved_settings.source_statement_timeout_seconds,
            ),
            connection_access_store,
            connection_authorizer,
        )
        app.state.connection_grant_service = connection_grant_service or ConnectionGrantService(
            connection_access_store
        )
        app.state.catalog_service = catalog_service or CatalogService(
            connection_store,
            connection_cipher,
            PsycopgSourceCatalogInspector(
                resolved_settings.source_connect_timeout_seconds,
                resolved_settings.source_statement_timeout_seconds,
            ),
            connection_authorizer,
        )
        dataset_store = SqlAlchemyDatasetStore(resources.session_factory)
        app.state.dataset_service = dataset_service or DatasetService(
            dataset_store,
            connection_authorizer,
        )
        app.state.dataset_validation_service = (
            dataset_validation_service
            or DatasetValidationService(
                dataset_store,
                connection_store,
                connection_cipher,
                PsycopgSourceDatasetValidator(
                    resolved_settings.source_connect_timeout_seconds,
                    resolved_settings.source_statement_timeout_seconds,
                ),
                connection_authorizer,
            )
        )
        app.state.dataset_preview_service = dataset_preview_service or DatasetPreviewService(
            dataset_store,
            connection_store,
            connection_cipher,
            PsycopgSourceDatasetPreviewer(
                resolved_settings.source_connect_timeout_seconds,
                resolved_settings.source_statement_timeout_seconds,
            ),
            connection_authorizer,
        )
        app.state.refresh_run_service = refresh_run_service or RefreshRunService(
            SqlAlchemyRefreshRunStore(resources.session_factory),
            CeleryRefreshDispatcher(celery_app),
            dataset_store,
            connection_authorizer,
        )
        access_store = SqlAlchemyAccessControlStore(resources.session_factory)
        access_authorizer = DatasetAccessAuthorizer(access_store)
        app.state.group_service = group_service or GroupService(access_store)
        app.state.dataset_grant_service = dataset_grant_service or DatasetGrantService(
            access_store,
            access_authorizer,
        )
        app.state.snapshot_query_service = snapshot_query_service or DatasetSnapshotQueryService(
            SqlAlchemySnapshotQueryStore(resources.session_factory),
            DuckDbLocalSnapshotQueryExecutor(
                resolved_settings.snapshot_storage_path,
                timeout_seconds=resolved_settings.snapshot_query_timeout_seconds,
                memory_limit_mb=resolved_settings.snapshot_query_memory_limit_mb,
                threads=resolved_settings.snapshot_query_threads,
                temporary_directory_limit_mb=(resolved_settings.snapshot_query_temp_limit_mb),
                maximum_concurrent_queries=resolved_settings.snapshot_query_concurrency,
            ),
            EncryptedSnapshotCursorCodec(
                resolved_settings.connection_encryption_key.get_secret_value()
            ),
            resolved_settings.snapshot_query_max_response_bytes,
            access_authorizer,
        )
        export_store = SqlAlchemyExportStore(resources.session_factory)
        app.state.export_service = export_service or DatasetExportService(
            export_store,
            CeleryExportDispatcher(celery_app),
            access_authorizer,
        )
        app.state.export_download_service = export_download_service or ExportDownloadService(
            export_store,
            EncryptedExportDownloadTokenCodec(
                resolved_settings.connection_encryption_key.get_secret_value()
            ),
            LocalExportStorage(resolved_settings.snapshot_storage_path),
            access_authorizer,
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
    application.add_exception_handler(ApiProblem, api_problem_handler)
    application.add_exception_handler(RequestValidationError, validation_problem_handler)
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
