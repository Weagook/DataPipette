"""Deterministic dataset-version schema comparison."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from datapipette.domain.datasets import (
    DatasetDefinition,
    DatasetDraft,
    DatasetField,
    DatasetValidationState,
    DatasetVersion,
    SqlDatasetDefinition,
    ValidatedDatasetField,
)


class SchemaChangeKind(StrEnum):
    FIELD_ADDED = "field_added"
    FIELD_REMOVED = "field_removed"
    FIELD_RENAMED = "field_renamed"
    FIELD_TYPE_CHANGED = "field_type_changed"
    FIELD_BECAME_NULLABLE = "field_became_nullable"
    FIELD_BECAME_REQUIRED = "field_became_required"
    FIELD_UNAVAILABLE = "field_unavailable"
    FIELD_DISPLAY_CHANGED = "field_display_changed"
    FIELD_SOURCE_CHANGED = "field_source_changed"


class SchemaChangeCompatibility(StrEnum):
    COMPATIBLE = "compatible"
    BREAKING = "breaking"


class DependencyResourceType(StrEnum):
    CHART = "chart"


@dataclass(frozen=True, slots=True)
class DatasetDependencyReference:
    resource_type: DependencyResourceType
    resource_id: UUID


@dataclass(frozen=True, slots=True)
class ImpactField:
    field_id: UUID
    name: str
    data_type: str | None
    nullable: bool | None


@dataclass(frozen=True, slots=True)
class DatasetSchemaChange:
    kind: SchemaChangeKind
    compatibility: SchemaChangeCompatibility
    field_id: UUID
    before: ImpactField | None
    after: ImpactField | None


@dataclass(frozen=True, slots=True)
class DatasetSchemaImpact:
    dataset_id: UUID
    baseline_version_id: UUID | None
    draft_version_id: UUID
    draft_revision: int
    draft_validation_state: DatasetValidationState
    changes: tuple[DatasetSchemaChange, ...]
    affected_dependencies: tuple[DatasetDependencyReference, ...] = ()
    blocking_dependencies: tuple[DatasetDependencyReference, ...] = ()

    @property
    def breaking_change_count(self) -> int:
        return sum(
            change.compatibility is SchemaChangeCompatibility.BREAKING for change in self.changes
        )

    @property
    def ready_for_publication(self) -> bool:
        return (
            self.draft_validation_state
            in {
                DatasetValidationState.VALID,
                DatasetValidationState.VALID_WITH_WARNINGS,
            }
            and not self.blocking_dependencies
        )


def _validated_fields(
    fields: tuple[ValidatedDatasetField, ...],
) -> dict[UUID, ValidatedDatasetField]:
    return {field.field_id: field for field in fields}


def _definition_fields(definition: DatasetDefinition) -> dict[UUID, DatasetField]:
    return {field.id: field for field in definition.fields}


def _impact_field(
    field_id: UUID,
    definition_field: DatasetField,
    validated: ValidatedDatasetField | None,
) -> ImpactField:
    return ImpactField(
        field_id=field_id,
        name=definition_field.name,
        data_type=validated.data_type if validated is not None else None,
        nullable=validated.nullable if validated is not None else None,
    )


def _display_signature(field: DatasetField) -> tuple[str, object, bool]:
    display = field.display
    return display.label, display.format, display.visible


def _source_signatures(definition: DatasetDefinition) -> dict[UUID, tuple[str, str, str]]:
    if isinstance(definition, SqlDatasetDefinition):
        return {}
    relations = {relation.id: relation for relation in definition.relations}
    return {
        field.id: (
            relations[field.source.relation_id].schema_name,
            relations[field.source.relation_id].relation_name,
            field.source.column,
        )
        for field in definition.fields
    }


def compare_dataset_schema(
    *,
    dataset_id: UUID,
    baseline: DatasetVersion | None,
    draft: DatasetDraft,
    dependencies: dict[UUID, tuple[DatasetDependencyReference, ...]] | None = None,
) -> DatasetSchemaImpact:
    """Compare one validated draft with its active published baseline."""

    if draft.validation is None or draft.validation.validated_revision != draft.revision:
        raise ValueError("Dataset impact requires validation of the current draft revision")
    before_definition = baseline.definition if baseline is not None else None
    before_validation = baseline.validation if baseline is not None else None
    before_fields = _definition_fields(before_definition) if before_definition is not None else {}
    after_fields = _definition_fields(draft.definition)
    before_validated = (
        _validated_fields(before_validation.result_schema) if before_validation is not None else {}
    )
    after_validated = _validated_fields(draft.validation.result_schema)
    before_sources = _source_signatures(before_definition) if before_definition is not None else {}
    after_sources = _source_signatures(draft.definition)
    changes: list[DatasetSchemaChange] = []

    for field_id in sorted(before_fields.keys() | after_fields.keys(), key=str):
        before_definition_field = before_fields.get(field_id)
        after_definition_field = after_fields.get(field_id)
        before = (
            _impact_field(field_id, before_definition_field, before_validated.get(field_id))
            if before_definition_field is not None
            else None
        )
        after = (
            _impact_field(field_id, after_definition_field, after_validated.get(field_id))
            if after_definition_field is not None
            else None
        )
        if before is None:
            changes.append(
                DatasetSchemaChange(
                    SchemaChangeKind.FIELD_ADDED,
                    SchemaChangeCompatibility.COMPATIBLE,
                    field_id,
                    None,
                    after,
                )
            )
        elif after is None:
            changes.append(
                DatasetSchemaChange(
                    SchemaChangeKind.FIELD_REMOVED,
                    SchemaChangeCompatibility.BREAKING,
                    field_id,
                    before,
                    None,
                )
            )
            continue
        else:
            assert before_definition_field is not None
            assert after_definition_field is not None
            if before.name != after.name:
                changes.append(
                    DatasetSchemaChange(
                        SchemaChangeKind.FIELD_RENAMED,
                        SchemaChangeCompatibility.BREAKING,
                        field_id,
                        before,
                        after,
                    )
                )
            if before.data_type is not None and after.data_type is not None:
                if before.data_type != after.data_type:
                    changes.append(
                        DatasetSchemaChange(
                            SchemaChangeKind.FIELD_TYPE_CHANGED,
                            SchemaChangeCompatibility.BREAKING,
                            field_id,
                            before,
                            after,
                        )
                    )
                if before.nullable is False and after.nullable is True:
                    changes.append(
                        DatasetSchemaChange(
                            SchemaChangeKind.FIELD_BECAME_NULLABLE,
                            SchemaChangeCompatibility.BREAKING,
                            field_id,
                            before,
                            after,
                        )
                    )
                elif before.nullable is True and after.nullable is False:
                    changes.append(
                        DatasetSchemaChange(
                            SchemaChangeKind.FIELD_BECAME_REQUIRED,
                            SchemaChangeCompatibility.COMPATIBLE,
                            field_id,
                            before,
                            after,
                        )
                    )
            if _display_signature(before_definition_field) != _display_signature(
                after_definition_field
            ):
                changes.append(
                    DatasetSchemaChange(
                        SchemaChangeKind.FIELD_DISPLAY_CHANGED,
                        SchemaChangeCompatibility.COMPATIBLE,
                        field_id,
                        before,
                        after,
                    )
                )
            if before_sources.get(field_id) != after_sources.get(field_id) and (
                field_id in before_sources or field_id in after_sources
            ):
                changes.append(
                    DatasetSchemaChange(
                        SchemaChangeKind.FIELD_SOURCE_CHANGED,
                        SchemaChangeCompatibility.COMPATIBLE,
                        field_id,
                        before,
                        after,
                    )
                )
        if after is not None and after.data_type is None:
            changes.append(
                DatasetSchemaChange(
                    SchemaChangeKind.FIELD_UNAVAILABLE,
                    SchemaChangeCompatibility.BREAKING,
                    field_id,
                    before,
                    after,
                )
            )

    affected_field_ids = {change.field_id for change in changes}
    breaking_field_ids = {
        change.field_id
        for change in changes
        if change.compatibility is SchemaChangeCompatibility.BREAKING
    }
    dependency_map = dependencies or {}
    affected_dependencies = {
        dependency
        for field_id in affected_field_ids
        for dependency in dependency_map.get(field_id, ())
    }
    blocking_dependencies = {
        dependency
        for field_id in breaking_field_ids
        for dependency in dependency_map.get(field_id, ())
    }

    def dependency_order(item: DatasetDependencyReference) -> tuple[str, str]:
        return item.resource_type.value, str(item.resource_id)

    return DatasetSchemaImpact(
        dataset_id=dataset_id,
        baseline_version_id=baseline.id if baseline is not None else None,
        draft_version_id=draft.id,
        draft_revision=draft.revision,
        draft_validation_state=draft.validation.state,
        changes=tuple(changes),
        affected_dependencies=tuple(sorted(affected_dependencies, key=dependency_order)),
        blocking_dependencies=tuple(sorted(blocking_dependencies, key=dependency_order)),
    )
