"""Groups, memberships, and additive dataset ACL behavior against PostgreSQL."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select, text, update

from datepipette.api.dependencies.authentication import CSRF_COOKIE_NAME
from datepipette.application.access_control import (
    DatasetAccessAuthorizer,
    DatasetAccessDeniedError,
)
from datepipette.application.auth_service import bootstrap_first_admin
from datepipette.config import Settings
from datepipette.domain.access_control import DatasetPermission
from datepipette.domain.identity import AccountStatus, SystemRole, User
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.models import (
    AuditEventModel,
    DatasetModel,
    PostgresConnectionModel,
    UserModel,
)
from datepipette.infrastructure.repositories.access_control import SqlAlchemyAccessControlStore
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore
from datepipette.main import create_app

TEST_SETTINGS = Settings(
    environment="test",
    docs_enabled=False,
    login_rate_limit_attempts=20,
    login_rate_limit_window_seconds=60,
)
ADMIN_EMAIL = "acl-admin@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
USER_PASSWORD = "analyst password for ACL tests"


async def _reset() -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
    redis = Redis.from_url(TEST_SETTINGS.redis_url.get_secret_value(), decode_responses=True)
    try:
        await redis.flushdb()
    finally:
        await redis.aclose()


async def _bootstrap_dataset() -> UUID:
    engine = create_database_engine(TEST_SETTINGS)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    dataset_id = uuid4()
    try:
        await bootstrap_first_admin(
            SqlAlchemyAuthenticationStore(factory),
            ADMIN_EMAIL,
            "ACL Admin",
            ADMIN_PASSWORD,
        )
        async with factory() as session, session.begin():
            owner_id = await session.scalar(
                select(UserModel.id).where(UserModel.email == ADMIN_EMAIL)
            )
            assert owner_id is not None
            connection_id = uuid4()
            session.add(
                PostgresConnectionModel(
                    id=connection_id,
                    name="ACL source",
                    host="database.internal",
                    port=5432,
                    database_name="business",
                    username="readonly",
                    encrypted_password="encrypted",
                    encrypted_ca_certificate=None,
                    ssl_mode="require",
                    created_by=owner_id,
                    last_tested_at=None,
                    last_test_succeeded=None,
                    last_test_error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                DatasetModel(
                    id=dataset_id,
                    name="Revenue",
                    owner_id=owner_id,
                    connection_id=connection_id,
                    active_version_id=None,
                    active_snapshot_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        await engine.dispose()
    return dataset_id


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None, None, None]:
    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


def _login(client: TestClient, email: str, password: str) -> None:
    response = client.post("/api/v1/auth/sessions", json={"email": email, "password": password})
    assert response.status_code == 201, response.text


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]}


def _create_user(client: TestClient, *, email: str, role: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/users",
        json={
            "email": email,
            "display_name": email.split("@", maxsplit=1)[0],
            "password": USER_PASSWORD,
            "role": role,
        },
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


async def _user(email: str) -> User:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            model = await session.scalar(select(UserModel).where(UserModel.email == email))
            assert model is not None
            return User(
                model.id,
                model.email,
                model.display_name,
                SystemRole(model.role),
                AccountStatus(model.status),
                model.last_login_at,
                model.created_at,
                model.updated_at,
            )
    finally:
        await engine.dispose()


async def _access(actor: User, dataset_id: UUID) -> frozenset[DatasetPermission]:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        authorizer = DatasetAccessAuthorizer(
            SqlAlchemyAccessControlStore(create_session_factory(engine))
        )
        access = await authorizer.require(
            actor=actor,
            dataset_id=dataset_id,
            permissions=frozenset(),
        )
        return access.permissions
    finally:
        await engine.dispose()


async def _make_owner(user_id: UUID, dataset_id: UUID) -> None:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                update(DatasetModel).where(DatasetModel.id == dataset_id).values(owner_id=user_id)
            )
    finally:
        await engine.dispose()


async def _audit_actions() -> list[str]:
    engine = create_database_engine(TEST_SETTINGS)
    try:
        async with create_session_factory(engine)() as session:
            return list((await session.scalars(select(AuditEventModel.action))).all())
    finally:
        await engine.dispose()


def test_groups_and_dataset_grants_are_effective_and_revocable() -> None:
    dataset_id = asyncio.run(_bootstrap_dataset())

    with (
        TestClient(create_app(settings=TEST_SETTINGS)) as admin_client,
        TestClient(create_app(settings=TEST_SETTINGS)) as analyst_client,
        TestClient(create_app(settings=TEST_SETTINGS)) as viewer_client,
    ):
        _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        analyst = _create_user(admin_client, email="analyst-acl@example.com", role="analyst")
        viewer = _create_user(admin_client, email="viewer-acl@example.com", role="viewer")
        analyst_id = UUID(cast(str, analyst["id"]))
        viewer_id = UUID(cast(str, viewer["id"]))

        missing_csrf = admin_client.post("/api/v1/groups", json={"name": "Finance"})
        assert missing_csrf.status_code == 403
        created = admin_client.post(
            "/api/v1/groups", json={"name": "  Finance   Analysts "}, headers=_csrf(admin_client)
        )
        assert created.status_code == 201, created.text
        group_id = UUID(created.json()["id"])
        assert created.json()["name"] == "Finance Analysts"

        duplicate = admin_client.post(
            "/api/v1/groups", json={"name": "finance analysts"}, headers=_csrf(admin_client)
        )
        assert duplicate.status_code == 409
        assert admin_client.get("/api/v1/groups").json()["data"][0]["id"] == str(group_id)
        assert admin_client.get(f"/api/v1/groups/{group_id}").status_code == 200
        renamed = admin_client.patch(
            f"/api/v1/groups/{group_id}",
            json={"name": "BI Team"},
            headers=_csrf(admin_client),
        )
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "BI Team"

        for user_id in (analyst_id, viewer_id):
            added = admin_client.put(
                f"/api/v1/groups/{group_id}/members/{user_id}", headers=_csrf(admin_client)
            )
            assert added.status_code == 200, added.text
        repeated = admin_client.put(
            f"/api/v1/groups/{group_id}/members/{analyst_id}", headers=_csrf(admin_client)
        )
        assert repeated.status_code == 200
        members = admin_client.get(f"/api/v1/groups/{group_id}/members")
        assert {item["user_id"] for item in members.json()["data"]} == {
            str(analyst_id),
            str(viewer_id),
        }

        grant_ids: dict[str, UUID] = {}
        for permission in ("view_rows", "export"):
            granted = admin_client.post(
                f"/api/v1/datasets/{dataset_id}/grants",
                json={
                    "principal_type": "group",
                    "principal_id": str(group_id),
                    "permission": permission,
                },
                headers=_csrf(admin_client),
            )
            assert granted.status_code == 201, granted.text
            grant_ids[permission] = UUID(granted.json()["id"])
        manage = admin_client.post(
            f"/api/v1/datasets/{dataset_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": str(analyst_id),
                "permission": "manage",
            },
            headers=_csrf(admin_client),
        )
        assert manage.status_code == 201, manage.text
        manage_id = UUID(manage.json()["id"])

        repeated_grant = admin_client.post(
            f"/api/v1/datasets/{dataset_id}/grants",
            json={
                "principal_type": "group",
                "principal_id": str(group_id),
                "permission": "view_rows",
            },
            headers=_csrf(admin_client),
        )
        assert repeated_grant.status_code == 201
        assert UUID(repeated_grant.json()["id"]) == grant_ids["view_rows"]
        assert len(admin_client.get(f"/api/v1/datasets/{dataset_id}/grants").json()["data"]) == 3

        invalid_principal = admin_client.post(
            f"/api/v1/datasets/{dataset_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": str(viewer_id),
                "permission": "view_rows",
            },
            headers=_csrf(admin_client),
        )
        assert invalid_principal.status_code == 404

        _login(analyst_client, "analyst-acl@example.com", USER_PASSWORD)
        _login(viewer_client, "viewer-acl@example.com", USER_PASSWORD)
        assert analyst_client.get("/api/v1/groups").status_code == 403
        schedule_payload = {
            "kind": "interval",
            "interval_minutes": 60,
            "daily_time": None,
            "enabled": False,
        }
        schedule_url = f"/api/v1/datasets/{dataset_id}/refresh-schedule"
        assert (
            analyst_client.put(
                schedule_url,
                json=schedule_payload,
                headers=_csrf(analyst_client),
            ).status_code
            == 404
        )
        assert (
            viewer_client.put(
                schedule_url,
                json=schedule_payload,
                headers=_csrf(viewer_client),
            ).status_code
            == 404
        )
        assert analyst_client.get(f"/api/v1/datasets/{dataset_id}/grants").status_code == 200
        delegated = analyst_client.post(
            f"/api/v1/datasets/{dataset_id}/grants",
            json={
                "principal_type": "user",
                "principal_id": str(analyst_id),
                "permission": "view_rows",
            },
            headers=_csrf(analyst_client),
        )
        assert delegated.status_code == 201, delegated.text

        export_payload = {"format": "csv", "fields": [str(uuid4())]}
        authorized_export = analyst_client.post(
            f"/api/v1/datasets/{dataset_id}/exports",
            json=export_payload,
            headers={**_csrf(analyst_client), "Idempotency-Key": "authorized-no-snapshot"},
        )
        assert authorized_export.status_code == 409
        analyst_freshness = analyst_client.get(f"/api/v1/datasets/{dataset_id}/freshness")
        assert analyst_freshness.status_code == 200
        assert analyst_freshness.json()["data_state"] == "not_published"
        assert analyst_freshness.json()["snapshot_id"] is None
        viewer_rows = viewer_client.post(
            f"/api/v1/datasets/{dataset_id}/rows:query",
            json={"fields": [str(uuid4())]},
        )
        assert viewer_rows.status_code == 404
        assert viewer_client.get(f"/api/v1/datasets/{dataset_id}/freshness").status_code == 404

        analyst_user = asyncio.run(_user("analyst-acl@example.com"))
        assert asyncio.run(_access(analyst_user, dataset_id)) == frozenset(DatasetPermission)
        viewer_user = asyncio.run(_user("viewer-acl@example.com"))
        with pytest.raises(DatasetAccessDeniedError):
            asyncio.run(_access(viewer_user, dataset_id))

        removed = admin_client.delete(
            f"/api/v1/groups/{group_id}/members/{analyst_id}", headers=_csrf(admin_client)
        )
        assert removed.status_code == 204
        assert asyncio.run(_access(analyst_user, dataset_id)) == frozenset(
            {DatasetPermission.VIEW_ROWS, DatasetPermission.MANAGE}
        )
        denied_export = analyst_client.post(
            f"/api/v1/datasets/{dataset_id}/exports",
            json=export_payload,
            headers={**_csrf(analyst_client), "Idempotency-Key": "revoked-export"},
        )
        assert denied_export.status_code == 404
        assert analyst_client.get(f"/api/v1/datasets/{dataset_id}/exports").status_code == 404
        missing_membership = admin_client.delete(
            f"/api/v1/groups/{group_id}/members/{analyst_id}", headers=_csrf(admin_client)
        )
        assert missing_membership.status_code == 404

        revoked = admin_client.delete(
            f"/api/v1/datasets/{dataset_id}/grants/{manage_id}", headers=_csrf(admin_client)
        )
        assert revoked.status_code == 204
        assert analyst_client.get(f"/api/v1/datasets/{dataset_id}/grants").status_code == 404
        missing_grant = admin_client.delete(
            f"/api/v1/datasets/{dataset_id}/grants/{uuid4()}", headers=_csrf(admin_client)
        )
        assert missing_grant.status_code == 404

        asyncio.run(_make_owner(analyst_id, dataset_id))
        assert asyncio.run(_access(analyst_user, dataset_id)) == frozenset(DatasetPermission)

    actions = asyncio.run(_audit_actions())
    assert {
        "identity.group_created",
        "identity.group_renamed",
        "identity.group_member_added",
        "identity.group_member_removed",
        "dataset.grant_created",
        "dataset.grant_revoked",
    }.issubset(actions)


def test_missing_groups_and_datasets_are_concealed() -> None:
    asyncio.run(_bootstrap_dataset())
    app = create_app(settings=TEST_SETTINGS)
    missing_group = uuid4()
    missing_dataset = uuid4()

    with TestClient(app) as client:
        _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        assert client.get(f"/api/v1/groups/{missing_group}").status_code == 404
        assert client.get(f"/api/v1/groups/{missing_group}/members").status_code == 404
        assert (
            client.patch(
                f"/api/v1/groups/{missing_group}",
                json={"name": "Missing"},
                headers=_csrf(client),
            ).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/v1/groups/{missing_group}/members/{uuid4()}", headers=_csrf(client)
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/v1/groups/{missing_group}/members/{uuid4()}", headers=_csrf(client)
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/datasets/{missing_dataset}/grants").status_code == 404
        assert (
            client.post(
                f"/api/v1/datasets/{missing_dataset}/grants",
                json={
                    "principal_type": "group",
                    "principal_id": str(uuid4()),
                    "permission": "view_rows",
                },
                headers=_csrf(client),
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/v1/datasets/{missing_dataset}/grants/{uuid4()}",
                headers=_csrf(client),
            ).status_code
            == 404
        )
