"""Versioned API router."""

from fastapi import APIRouter

from datepipette.api.routes.auth import router as auth_router
from datepipette.api.routes.connections import router as connections_router
from datepipette.api.routes.health import router as health_router
from datepipette.api.routes.users import router as users_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(connections_router)
api_v1_router.include_router(health_router)
api_v1_router.include_router(users_router)
