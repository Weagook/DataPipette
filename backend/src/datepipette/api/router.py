"""Versioned API router."""

from fastapi import APIRouter

from datepipette.api.routes.access_control import router as access_control_router
from datepipette.api.routes.audit_events import router as audit_events_router
from datepipette.api.routes.auth import router as auth_router
from datepipette.api.routes.catalog import router as catalog_router
from datepipette.api.routes.charts import router as charts_router
from datepipette.api.routes.connections import router as connections_router
from datepipette.api.routes.dashboards import router as dashboards_router
from datepipette.api.routes.datasets import router as datasets_router
from datepipette.api.routes.exports import router as exports_router
from datepipette.api.routes.health import router as health_router
from datepipette.api.routes.refresh_runs import router as refresh_runs_router
from datepipette.api.routes.schedules import router as schedules_router
from datepipette.api.routes.service_accounts import router as service_accounts_router
from datepipette.api.routes.snapshot_queries import router as snapshot_queries_router
from datepipette.api.routes.users import router as users_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(access_control_router)
api_v1_router.include_router(audit_events_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(catalog_router)
api_v1_router.include_router(charts_router)
api_v1_router.include_router(connections_router)
api_v1_router.include_router(dashboards_router)
api_v1_router.include_router(datasets_router)
api_v1_router.include_router(exports_router)
api_v1_router.include_router(health_router)
api_v1_router.include_router(refresh_runs_router)
api_v1_router.include_router(schedules_router)
api_v1_router.include_router(service_accounts_router)
api_v1_router.include_router(snapshot_queries_router)
api_v1_router.include_router(users_router)
