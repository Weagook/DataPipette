"""Bounded synchronous preview of a validated dataset draft."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
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
    DatasetPreview,
    DatasetValidationState,
    PreviewTruncationReason,
    VisualDatasetDefinition,
)
from datepipette.domain.identity import User

MAX_PREVIEW_ROWS = 200
MAX_PREVIEW_BYTES = 1_048_576


class DatasetDraftNotValidatedError(Exception):
    pass


class SourceDatasetPreviewUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SourcePreviewResult:
    rows: tuple[dict[str, Any], ...]
    truncation_reason: PreviewTruncationReason | None


class SourceDatasetPreviewer(Protocol):
    async def preview(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        definition: VisualDatasetDefinition,
        limit: int,
        maximum_bytes: int,
    ) -> SourcePreviewResult: ...


class DatasetPreviewService:
    def __init__(
        self,
        dataset_store: DatasetStore,
        connection_store: ConnectionStore,
        cipher: SecretCipher,
        source_previewer: SourceDatasetPreviewer,
        authorizer: ConnectionAccessAuthorizer,
    ) -> None:
        self._dataset_store = dataset_store
        self._connection_store = connection_store
        self._cipher = cipher
        self._source_previewer = source_previewer
        self._authorizer = authorizer

    async def preview(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        limit: int,
        request_id: str,
    ) -> DatasetPreview:
        if not 1 <= limit <= MAX_PREVIEW_ROWS:
            raise ValueError("Preview limit is outside the supported range")

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
        validation = draft.validation
        expected_fields = tuple((field.id, field.name) for field in draft.definition.fields)
        validated_fields = (
            tuple((field.field_id, field.name) for field in validation.result_schema)
            if validation is not None
            else ()
        )
        if (
            validation is None
            or validation.validated_revision != draft.revision
            or validation.compiled_sql is None
            or validation.state is DatasetValidationState.INVALID
            or validated_fields != expected_fields
        ):
            raise DatasetDraftNotValidatedError

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
            source_result = await self._source_previewer.preview(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                definition=draft.definition,
                limit=limit,
                maximum_bytes=MAX_PREVIEW_BYTES,
            )
        except (ConnectionSecretUnavailableError, SourceDatasetPreviewUnavailableError) as exc:
            await self._dataset_store.record_preview_failure(
                dataset_id=dataset.id,
                actor_id=actor.id,
                attempted_revision=draft.revision,
                request_id=request_id,
                occurred_at=datetime.now(UTC),
            )
            raise SourceDatasetPreviewUnavailableError from exc

        exists = await self._dataset_store.record_preview(
            dataset_id=dataset.id,
            actor_id=actor.id,
            expected_revision=draft.revision,
            requested_limit=limit,
            returned_rows=len(source_result.rows),
            truncation_reason=(
                source_result.truncation_reason.value
                if source_result.truncation_reason is not None
                else None
            ),
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
        if not exists:
            raise DatasetNotFoundError
        return DatasetPreview(
            revision=draft.revision,
            columns=validation.result_schema,
            rows=source_result.rows,
            requested_limit=limit,
            returned_rows=len(source_result.rows),
            truncated=source_result.truncation_reason is not None,
            truncation_reason=source_result.truncation_reason,
        )
