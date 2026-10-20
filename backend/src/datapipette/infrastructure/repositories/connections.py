"""SQLAlchemy persistence for PostgreSQL source connections."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datapipette.application.connections import (
    ConnectionAlreadyExistsError,
    ConnectionCreate,
    ConnectionUpdate,
)
from datapipette.domain.audit import AuditActorType, AuditOutcome
from datapipette.domain.connections import (
    ConnectionTestResult,
    EncryptedConnectionSecrets,
    PostgresConnection,
    PostgresSslMode,
)
from datapipette.infrastructure.models import AuditEventModel, PostgresConnectionModel


def to_connection(model: PostgresConnectionModel) -> PostgresConnection:
    return PostgresConnection(
        id=model.id,
        name=model.name,
        host=model.host,
        port=model.port,
        database_name=model.database_name,
        username=model.username,
        ssl_mode=PostgresSslMode(model.ssl_mode),
        has_ca_certificate=model.encrypted_ca_certificate is not None,
        created_by=model.created_by,
        last_tested_at=model.last_tested_at,
        last_test_succeeded=model.last_test_succeeded,
        last_test_error_code=model.last_test_error_code,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyConnectionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        value: ConnectionCreate,
        secrets: EncryptedConnectionSecrets,
        occurred_at: datetime,
        request_id: str,
    ) -> PostgresConnection:
        model = PostgresConnectionModel(
            id=connection_id,
            name=value.name,
            host=value.host,
            port=value.port,
            database_name=value.database_name,
            username=value.username,
            encrypted_password=secrets.password,
            encrypted_ca_certificate=secrets.ca_certificate,
            ssl_mode=value.ssl_mode.value,
            created_by=actor_id,
            last_tested_at=None,
            last_test_succeeded=None,
            last_test_error_code=None,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        try:
            async with self._session_factory() as session, session.begin():
                session.add(model)
                session.add(
                    self._audit(
                        actor_id,
                        "connection.created",
                        connection_id,
                        occurred_at,
                        request_id,
                        {"ssl_mode": value.ssl_mode.value},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == (
                "uq_postgresql_connections_name"
            ):
                raise ConnectionAlreadyExistsError from exc
            raise
        return to_connection(model)

    async def list(
        self,
        *,
        after_created_at: datetime | None,
        after_id: UUID | None,
        allowed_ids: frozenset[UUID] | None,
        limit: int,
    ) -> list[PostgresConnection]:
        if allowed_ids == frozenset():
            return []
        statement = (
            select(PostgresConnectionModel)
            .order_by(PostgresConnectionModel.created_at, PostgresConnectionModel.id)
            .limit(limit)
        )
        if allowed_ids is not None:
            statement = statement.where(PostgresConnectionModel.id.in_(allowed_ids))
        if after_created_at is not None and after_id is not None:
            statement = statement.where(
                or_(
                    PostgresConnectionModel.created_at > after_created_at,
                    and_(
                        PostgresConnectionModel.created_at == after_created_at,
                        PostgresConnectionModel.id > after_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            models = (await session.scalars(statement)).all()
        return [to_connection(model) for model in models]

    async def get(self, connection_id: UUID) -> PostgresConnection | None:
        async with self._session_factory() as session:
            model = await session.get(PostgresConnectionModel, connection_id)
        return to_connection(model) if model is not None else None

    async def get_secrets(self, connection_id: UUID) -> EncryptedConnectionSecrets | None:
        async with self._session_factory() as session:
            model = await session.get(PostgresConnectionModel, connection_id)
        if model is None:
            return None
        return EncryptedConnectionSecrets(
            password=model.encrypted_password,
            ca_certificate=model.encrypted_ca_certificate,
        )

    async def update(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        value: ConnectionUpdate,
        encrypted_password: str | None,
        encrypted_ca_certificate: str | None,
        occurred_at: datetime,
        request_id: str,
    ) -> PostgresConnection | None:
        try:
            async with self._session_factory() as session, session.begin():
                model = await session.get(PostgresConnectionModel, connection_id)
                if model is None:
                    return None
                changes: dict[str, Any] = {}
                for field in ("name", "host", "port", "database_name", "username"):
                    new_value = getattr(value, field)
                    if new_value is not None and new_value != getattr(model, field):
                        changes[field] = True
                        setattr(
                            model,
                            field,
                            new_value.strip() if isinstance(new_value, str) else new_value,
                        )
                if value.ssl_mode is not None and value.ssl_mode.value != model.ssl_mode:
                    model.ssl_mode = value.ssl_mode.value
                    changes["ssl_mode"] = True
                if encrypted_password is not None:
                    model.encrypted_password = encrypted_password
                    changes["password"] = True
                if value.ca_certificate_set:
                    model.encrypted_ca_certificate = encrypted_ca_certificate
                    changes["ca_certificate"] = True
                if changes:
                    model.updated_at = occurred_at
                    model.last_tested_at = None
                    model.last_test_succeeded = None
                    model.last_test_error_code = None
                    session.add(
                        self._audit(
                            actor_id,
                            "connection.updated",
                            connection_id,
                            occurred_at,
                            request_id,
                            {"changed_fields": sorted(changes)},
                        )
                    )
                    await session.flush()
                return to_connection(model)
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == (
                "uq_postgresql_connections_name"
            ):
                raise ConnectionAlreadyExistsError from exc
            raise

    async def record_test(
        self,
        *,
        connection_id: UUID,
        actor_id: UUID,
        result: ConnectionTestResult,
        request_id: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            model = await session.get(PostgresConnectionModel, connection_id)
            if model is None:
                return
            model.last_tested_at = result.tested_at
            model.last_test_succeeded = result.succeeded
            model.last_test_error_code = result.error_code
            session.add(
                self._audit(
                    actor_id,
                    "connection.tested",
                    connection_id,
                    result.tested_at,
                    request_id,
                    {
                        "succeeded": result.succeeded,
                        "error_code": result.error_code,
                    },
                    outcome=(AuditOutcome.SUCCESS if result.succeeded else AuditOutcome.FAILURE),
                )
            )

    @staticmethod
    def _audit(
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        occurred_at: datetime,
        request_id: str,
        details: dict[str, Any],
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
    ) -> AuditEventModel:
        return AuditEventModel(
            id=uuid4(),
            occurred_at=occurred_at,
            actor_type=AuditActorType.USER.value,
            actor_id=actor_id,
            action=action,
            outcome=outcome.value,
            resource_type="postgresql_connection",
            resource_id=resource_id,
            request_id=request_id,
            details=details,
        )
