"""Explicit live validation of visual dataset drafts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from datepipette.application.connection_access import (
    ConnectionAccessAuthorizer,
    ConnectionAccessDeniedError,
)
from datepipette.application.connections import (
    ConnectionSecretUnavailableError,
    ConnectionStore,
    SecretCipher,
)
from datepipette.application.datasets import (
    DatasetNotFoundError,
    DatasetRevisionConflictError,
    DatasetStore,
    require_dataset_owner,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import (
    DatasetDraft,
    DatasetPlanSummary,
    DatasetValidation,
    DatasetValidationFinding,
    DatasetValidationState,
    ValidatedDatasetField,
    VisualDatasetDefinition,
)
from datepipette.domain.identity import User


class SourceDatasetValidationUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SourceValidationResult:
    state: DatasetValidationState
    compiled_sql: str | None
    result_schema: tuple[ValidatedDatasetField, ...]
    plan: DatasetPlanSummary | None
    findings: tuple[DatasetValidationFinding, ...]


class SourceDatasetValidator(Protocol):
    async def validate(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        definition: VisualDatasetDefinition,
    ) -> SourceValidationResult: ...


class DatasetValidationService:
    def __init__(
        self,
        dataset_store: DatasetStore,
        connection_store: ConnectionStore,
        cipher: SecretCipher,
        source_validator: SourceDatasetValidator,
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._dataset_store = dataset_store
        self._connection_store = connection_store
        self._cipher = cipher
        self._source_validator = source_validator
        self._authorizer = authorizer

    async def validate(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        request_id: str,
    ) -> DatasetDraft:
        dataset = await self._dataset_store.get(dataset_id)
        draft = await self._dataset_store.get_draft(dataset_id)
        if dataset is None or draft is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        if draft.revision != expected_revision:
            raise DatasetRevisionConflictError
        try:
            await self._authorizer.require_relation(
                actor=actor,
                connection_id=dataset.connection_id,
                schema_name=draft.definition.relation.schema_name,
                relation_name=draft.definition.relation.relation_name,
            )
        except ConnectionAccessDeniedError as exc:
            raise DatasetNotFoundError from exc
        connection = await self._connection_store.get(dataset.connection_id)
        secrets = await self._connection_store.get_secrets(dataset.connection_id)
        if connection is None or secrets is None:
            raise DatasetNotFoundError
        try:
            password = self._cipher.decrypt(
                secrets.password,
                connection_id=connection.id,
                field="password",
            )
            ca_certificate = (
                self._cipher.decrypt(
                    secrets.ca_certificate,
                    connection_id=connection.id,
                    field="ca_certificate",
                )
                if secrets.ca_certificate is not None
                else None
            )
            source_result = await self._source_validator.validate(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                definition=draft.definition,
            )
        except (ConnectionSecretUnavailableError, SourceDatasetValidationUnavailableError) as exc:
            await self._dataset_store.record_validation_failure(
                dataset_id=dataset.id,
                actor_id=actor.id,
                attempted_revision=draft.revision,
                request_id=request_id,
                occurred_at=datetime.now(UTC),
            )
            raise SourceDatasetValidationUnavailableError from exc
        validation = DatasetValidation(
            state=source_result.state,
            validated_revision=draft.revision,
            validated_at=datetime.now(UTC),
            compiled_sql=source_result.compiled_sql,
            result_schema=source_result.result_schema,
            plan=source_result.plan,
            findings=source_result.findings,
        )
        stored = await self._dataset_store.record_validation(
            dataset_id=dataset.id,
            actor_id=actor.id,
            expected_revision=draft.revision,
            validation=validation,
            request_id=request_id,
        )
        if stored is None:
            raise DatasetNotFoundError
        return stored
