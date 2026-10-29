"""Transient metadata-database failures become retryable problems."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from datapipette.config import Settings
from datapipette.main import create_app


def test_operational_database_errors_return_retryable_problem() -> None:
    app = create_app(Settings(environment="test"))

    @app.get("/probe/overloaded")
    async def overloaded() -> None:
        raise OperationalError("SELECT 1", None, Exception("too many clients already"))

    @app.get("/probe/integrity")
    async def integrity() -> None:
        raise IntegrityError("INSERT", None, Exception("duplicate key"))

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/probe/overloaded")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["code"] == "service_temporarily_unavailable"
    assert "too many clients" not in response.text
    assert client.get("/probe/integrity").status_code == 500
