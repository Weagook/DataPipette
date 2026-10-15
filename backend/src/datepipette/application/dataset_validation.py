"""Explicit live validation of dataset drafts."""

from dataclasses import dataclass, replace
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
from datepipette.application.query_safety import (
    QuerySafetyPolicyStore,
    require_query_safety_administrator,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import (
    DatasetDefinition,
    DatasetDraft,
    DatasetPlanSummary,
    DatasetRelationStatistics,
    DatasetValidation,
    DatasetValidationFinding,
    DatasetValidationState,
    QuerySafetyOverride,
    QuerySafetyPolicy,
    ValidatedDatasetField,
    ValidationSeverity,
    build_dataset_recommendations,
    definition_relations,
)
from datepipette.domain.identity import User


class SourceDatasetValidationUnavailableError(Exception):
    pass


class QuerySafetyOverrideNotApplicableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SourceValidationResult:
    state: DatasetValidationState
    compiled_sql: str | None
    result_schema: tuple[ValidatedDatasetField, ...]
    plan: DatasetPlanSummary | None
    findings: tuple[DatasetValidationFinding, ...]
    relation_statistics: tuple[DatasetRelationStatistics, ...] = ()


class SourceDatasetValidator(Protocol):
    async def validate(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        definition: DatasetDefinition,
        policy: QuerySafetyPolicy,
    ) -> SourceValidationResult: ...


class DatasetValidationService:
    def __init__(
        self,
        dataset_store: DatasetStore,
        connection_store: ConnectionStore,
        cipher: SecretCipher,
        source_validator: SourceDatasetValidator,
        authorizer: ConnectionAccessAuthorizer,
        policy_store: QuerySafetyPolicyStore,
    ) -> None:
        self._dataset_store = dataset_store
        self._connection_store = connection_store
        self._cipher = cipher
        self._source_validator = source_validator
        self._authorizer = authorizer
        self._policy_store = policy_store

    async def validate(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        expected_revision: int,
        request_id: str,
        override_reason: str | None = None,
    ) -> DatasetDraft:
        normalized_reason = (
            " ".join(override_reason.split()) if override_reason is not None else None
        )
        if normalized_reason is not None:
            require_query_safety_administrator(actor)
            if not 10 <= len(normalized_reason) <= 500:
                raise ValueError("Override reason must contain between 10 and 500 characters")
        dataset = await self._dataset_store.get(dataset_id)
        draft = await self._dataset_store.get_draft(dataset_id)
        if dataset is None or draft is None:
            raise DatasetNotFoundError
        require_dataset_owner(actor, dataset)
        if draft.revision != expected_revision:
            raise DatasetRevisionConflictError
        try:
            await self._authorizer.require_relations(
                actor=actor,
                connection_id=dataset.connection_id,
                relations=definition_relations(draft.definition),
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
            managed_policy = await self._policy_store.get()
            source_result = await self._source_validator.validate(
                connection,
                password=password,
                ca_certificate=ca_certificate,
                definition=draft.definition,
                policy=managed_policy.policy,
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
        override = None
        findings = source_result.findings
        state = source_result.state
        if normalized_reason is not None:
            if state is not DatasetValidationState.BLOCKED:
                raise QuerySafetyOverrideNotApplicableError
            blocked_codes = tuple(
                finding.code
                for finding in findings
                if finding.severity is ValidationSeverity.BLOCKED
            )
            findings = tuple(
                replace(finding, severity=ValidationSeverity.WARNING)
                if finding.severity is ValidationSeverity.BLOCKED
                else finding
                for finding in findings
            )
            state = DatasetValidationState.VALID_WITH_WARNINGS
            override = QuerySafetyOverride(
                actor_id=actor.id,
                reason=normalized_reason,
                occurred_at=datetime.now(UTC),
                finding_codes=blocked_codes,
            )
        validation = DatasetValidation(
            state=state,
            validated_revision=draft.revision,
            validated_at=datetime.now(UTC),
            compiled_sql=source_result.compiled_sql,
            result_schema=source_result.result_schema,
            plan=source_result.plan,
            findings=findings,
            relation_statistics=source_result.relation_statistics,
            policy_revision=managed_policy.revision,
            query_safety_override=override,
            recommendations=build_dataset_recommendations(draft.definition, findings),
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
