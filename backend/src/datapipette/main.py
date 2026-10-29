"""FastAPI application factory and process entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from datapipette import __version__
from datapipette.api.middleware import RequestContextMiddleware
from datapipette.api.problems import (
    ApiProblem,
    api_problem_handler,
    validation_problem_handler,
)
from datapipette.api.router import api_v1_router
from datapipette.application.access_control import (
    DatasetAccessAuthorizer,
    DatasetGrantService,
    GroupService,
)
from datapipette.application.audit_events import AuditEventService
from datapipette.application.auth_service import AuthenticationService
from datapipette.application.catalog import CatalogService
from datapipette.application.charts import ChartService
from datapipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionGrantService,
)
from datapipette.application.connections import ConnectionService
from datapipette.application.dashboards import DashboardService
from datapipette.application.dataset_preview import DatasetPreviewService
from datapipette.application.dataset_validation import DatasetValidationService
from datapipette.application.datasets import DatasetService
from datapipette.application.exports import DatasetExportService, ExportDownloadService
from datapipette.application.health import ReadinessProbe
from datapipette.application.query_safety import QuerySafetyPolicyService
from datapipette.application.refresh_runs import RefreshRunService
from datapipette.application.schedules import RefreshScheduleService
from datapipette.application.service_accounts import (
    ApiTokenAuthenticationService,
    ServiceAccountService,
)
from datapipette.application.snapshot_queries import DatasetSnapshotQueryService
from datapipette.application.user_administration import UserAdministrationService
from datapipette.config import Settings, get_settings
from datapipette.infrastructure.catalog import PsycopgSourceCatalogInspector
from datapipette.infrastructure.celery_app import celery_app
from datapipette.infrastructure.crypto import AesGcmSecretCipher
from datapipette.infrastructure.dataset_preview import PsycopgSourceDatasetPreviewer
from datapipette.infrastructure.dataset_validation import PsycopgSourceDatasetValidator
from datapipette.infrastructure.duckdb_chart_queries import DuckDbChartQueryExecutor
from datapipette.infrastructure.duckdb_runtime import DuckDbQueryConcurrencyLimiter
from datapipette.infrastructure.duckdb_snapshot_queries import DuckDbSnapshotQueryExecutor
from datapipette.infrastructure.export_dispatch import CeleryExportDispatcher
from datapipette.infrastructure.export_tokens import EncryptedExportDownloadTokenCodec
from datapipette.infrastructure.health import InfrastructureReadinessProbe
from datapipette.infrastructure.interactive_admission import PostgresInteractiveQueryAdmission
from datapipette.infrastructure.object_storage import (
    ObjectExportDownloadResolver,
    create_object_storage_registry,
)
from datapipette.infrastructure.rate_limit import RedisLoginRateLimiter
from datapipette.infrastructure.refresh_dispatch import CeleryRefreshDispatcher
from datapipette.infrastructure.repositories.access_control import SqlAlchemyAccessControlStore
from datapipette.infrastructure.repositories.audit_events import SqlAlchemyAuditEventStore
from datapipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datapipette.infrastructure.repositories.charts import SqlAlchemyChartStore
from datapipette.infrastructure.repositories.connection_access import (
    SqlAlchemyConnectionAccessStore,
)
from datapipette.infrastructure.repositories.connections import SqlAlchemyConnectionStore
from datapipette.infrastructure.repositories.dashboards import SqlAlchemyDashboardStore
from datapipette.infrastructure.repositories.datasets import SqlAlchemyDatasetStore
from datapipette.infrastructure.repositories.exports import SqlAlchemyExportStore
from datapipette.infrastructure.repositories.query_safety import SqlAlchemyQuerySafetyPolicyStore
from datapipette.infrastructure.repositories.refresh_runs import SqlAlchemyRefreshRunStore
from datapipette.infrastructure.repositories.schedules import SqlAlchemyRefreshScheduleStore
from datapipette.infrastructure.repositories.service_accounts import SqlAlchemyServiceAccountStore
from datapipette.infrastructure.repositories.snapshot_queries import (
    SqlAlchemySnapshotQueryStore,
)
from datapipette.infrastructure.repositories.user_administration import (
    SqlAlchemyUserAdministrationStore,
)
from datapipette.infrastructure.runtime import create_runtime_resources
from datapipette.infrastructure.snapshot_cursor import EncryptedSnapshotCursorCodec
from datapipette.infrastructure.source_postgres import PsycopgSourceConnectionTester
from datapipette.logging import configure_logging


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
    refresh_schedule_service: RefreshScheduleService | None = None,
    snapshot_query_service: DatasetSnapshotQueryService | None = None,
    export_service: DatasetExportService | None = None,
    export_download_service: ExportDownloadService | None = None,
    group_service: GroupService | None = None,
    dataset_grant_service: DatasetGrantService | None = None,
    connection_grant_service: ConnectionGrantService | None = None,
    chart_service: ChartService | None = None,
    dashboard_service: DashboardService | None = None,
    service_account_service: ServiceAccountService | None = None,
    api_token_authentication_service: ApiTokenAuthenticationService | None = None,
    audit_event_service: AuditEventService | None = None,
    query_safety_policy_service: QuerySafetyPolicyService | None = None,
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
            if refresh_schedule_service is not None:
                app.state.refresh_schedule_service = refresh_schedule_service
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
            if chart_service is not None:
                app.state.chart_service = chart_service
            if dashboard_service is not None:
                app.state.dashboard_service = dashboard_service
            if service_account_service is not None:
                app.state.service_account_service = service_account_service
            if api_token_authentication_service is not None:
                app.state.api_token_authentication_service = api_token_authentication_service
            if audit_event_service is not None:
                app.state.audit_event_service = audit_event_service
            if query_safety_policy_service is not None:
                app.state.query_safety_policy_service = query_safety_policy_service
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
        service_account_store = SqlAlchemyServiceAccountStore(resources.session_factory)
        app.state.service_account_service = service_account_service or ServiceAccountService(
            service_account_store
        )
        app.state.api_token_authentication_service = (
            api_token_authentication_service or ApiTokenAuthenticationService(service_account_store)
        )
        app.state.audit_event_service = audit_event_service or AuditEventService(
            SqlAlchemyAuditEventStore(resources.session_factory)
        )
        default_query_safety_policy = resolved_settings.default_query_safety_policy()
        query_safety_policy_store = SqlAlchemyQuerySafetyPolicyStore(
            resources.session_factory,
            default_query_safety_policy,
        )
        app.state.query_safety_policy_service = (
            query_safety_policy_service or QuerySafetyPolicyService(query_safety_policy_store)
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
                    default_query_safety_policy,
                ),
                connection_authorizer,
                query_safety_policy_store,
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
            query_safety_policy_store,
        )
        app.state.refresh_run_service = refresh_run_service or RefreshRunService(
            SqlAlchemyRefreshRunStore(resources.session_factory),
            CeleryRefreshDispatcher(celery_app),
            dataset_store,
            connection_authorizer,
        )
        app.state.refresh_schedule_service = refresh_schedule_service or RefreshScheduleService(
            SqlAlchemyRefreshScheduleStore(
                resources.session_factory,
                resolved_settings.installation_timezone,
            ),
            dataset_store,
            ZoneInfo(resolved_settings.installation_timezone),
        )
        access_store = SqlAlchemyAccessControlStore(resources.session_factory)
        access_authorizer = DatasetAccessAuthorizer(access_store)
        object_storages = create_object_storage_registry(resolved_settings)
        app.state.group_service = group_service or GroupService(access_store)
        app.state.dataset_grant_service = dataset_grant_service or DatasetGrantService(
            access_store,
            access_authorizer,
        )
        snapshot_query_store = SqlAlchemySnapshotQueryStore(resources.session_factory)
        interactive_query_limiter = DuckDbQueryConcurrencyLimiter(
            PostgresInteractiveQueryAdmission(resources.advisory_lock_engine)
        )
        app.state.snapshot_query_service = snapshot_query_service or DatasetSnapshotQueryService(
            snapshot_query_store,
            DuckDbSnapshotQueryExecutor(
                object_storages,
                resolved_settings.snapshot_storage_path,
                limiter=interactive_query_limiter,
            ),
            EncryptedSnapshotCursorCodec(
                resolved_settings.connection_encryption_key.get_secret_value()
            ),
            access_authorizer,
            query_safety_policy_store,
            resolved_settings.installation_timezone,
        )
        resolved_chart_service = chart_service or ChartService(
            SqlAlchemyChartStore(resources.session_factory),
            snapshot_query_store,
            DuckDbChartQueryExecutor(
                object_storages,
                resolved_settings.snapshot_storage_path,
                limiter=interactive_query_limiter,
                installation_timezone=resolved_settings.installation_timezone,
            ),
            access_authorizer,
            query_safety_policy_store,
            resolved_settings.installation_timezone,
        )
        app.state.chart_service = resolved_chart_service
        app.state.dashboard_service = dashboard_service or DashboardService(
            SqlAlchemyDashboardStore(resources.session_factory),
            resolved_chart_service,
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
            ObjectExportDownloadResolver(
                object_storages,
                s3_download_delivery=resolved_settings.s3_download_delivery,
            ),
            access_authorizer,
        )
        try:
            yield
        finally:
            await resources.close()

    docs_url = "/docs" if resolved_settings.api_docs_enabled else None
    openapi_url = "/openapi.json" if resolved_settings.api_docs_enabled else None
    application = FastAPI(
        title="DataPipette API",
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
        "datapipette.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
    )
