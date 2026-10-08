"""API health endpoint behavior."""

from fastapi.testclient import TestClient

from datepipette.application.health import DependencyStatus, ReadinessStatus
from datepipette.config import Settings
from datepipette.main import create_app


class StaticReadinessProbe:
    def __init__(self, *, database_available: bool, redis_available: bool) -> None:
        self._status = ReadinessStatus(
            checks={
                "metadata_database": DependencyStatus(available=database_available),
                "redis": DependencyStatus(available=redis_available),
            }
        )

    async def check(self) -> ReadinessStatus:
        return self._status


def create_test_client(probe: StaticReadinessProbe) -> TestClient:
    settings = Settings(environment="test", docs_enabled=False)
    return TestClient(create_app(settings=settings, readiness_probe=probe))


def test_liveness_returns_request_id() -> None:
    with create_test_client(
        StaticReadinessProbe(database_available=True, redis_available=True)
    ) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request"


def test_default_lifespan_does_not_require_dependencies_to_start() -> None:
    settings = Settings(environment="test", docs_enabled=False)

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200


def test_liveness_replaces_unsafe_request_id() -> None:
    with create_test_client(
        StaticReadinessProbe(database_available=True, redis_available=True)
    ) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": "unsafe id\n"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "unsafe id\n"


def test_readiness_is_ready_when_dependencies_are_available() -> None:
    with create_test_client(
        StaticReadinessProbe(database_available=True, redis_available=True)
    ) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "metadata_database": {"status": "available"},
            "redis": {"status": "available"},
        },
    }


def test_readiness_is_unavailable_without_exposing_errors() -> None:
    with create_test_client(
        StaticReadinessProbe(database_available=False, redis_available=True)
    ) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {
            "metadata_database": {"status": "unavailable"},
            "redis": {"status": "available"},
        },
    }
