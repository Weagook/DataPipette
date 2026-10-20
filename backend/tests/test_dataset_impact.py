"""Dataset schema-impact classification tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from datapipette.domain.dataset_impact import (
    DatasetDependencyReference,
    DependencyResourceType,
    SchemaChangeCompatibility,
    SchemaChangeKind,
    compare_dataset_schema,
)
from datapipette.domain.datasets import (
    DatasetDraft,
    DatasetValidation,
    DatasetValidationState,
    DatasetVersion,
    DatasetVersionState,
    FieldDisplay,
    FieldSource,
    ValidatedDatasetField,
    VisualDatasetDefinition,
    VisualField,
    VisualRelation,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def _definition(
    relation_id: UUID,
    relation_name: str,
    fields: tuple[VisualField, ...],
) -> VisualDatasetDefinition:
    return VisualDatasetDefinition(
        relations=(VisualRelation(relation_id, "crm", relation_name, "source"),),
        fields=fields,
    )


def _field(
    field_id: UUID,
    relation_id: UUID,
    name: str,
    *,
    column: str | None = None,
    label: str | None = None,
) -> VisualField:
    return VisualField(
        id=field_id,
        source=FieldSource(relation_id, column or name),
        name=name,
        display=FieldDisplay(label or name),
    )


def _validation(
    revision: int,
    state: DatasetValidationState,
    fields: tuple[tuple[UUID, str, str, bool], ...],
) -> DatasetValidation:
    return DatasetValidation(
        state=state,
        validated_revision=revision,
        validated_at=NOW,
        compiled_sql="SELECT 1" if state is not DatasetValidationState.INVALID else None,
        result_schema=tuple(
            ValidatedDatasetField(field_id, name, data_type, nullable)
            for field_id, name, data_type, nullable in fields
        ),
        plan=None,
        findings=(),
    )


def test_initial_validated_draft_reports_compatible_additions() -> None:
    dataset_id = uuid4()
    relation_id = uuid4()
    field_id = uuid4()
    definition = _definition(relation_id, "customers", (_field(field_id, relation_id, "id"),))
    draft = DatasetDraft(
        id=uuid4(),
        dataset_id=dataset_id,
        revision=1,
        definition=definition,
        validation=_validation(
            1, DatasetValidationState.VALID, ((field_id, "id", "bigint", False),)
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )

    impact = compare_dataset_schema(dataset_id=dataset_id, baseline=None, draft=draft)

    assert impact.ready_for_publication is True
    assert impact.breaking_change_count == 0
    assert [(change.kind, change.compatibility) for change in impact.changes] == [
        (SchemaChangeKind.FIELD_ADDED, SchemaChangeCompatibility.COMPATIBLE)
    ]


def test_comparison_classifies_breaking_and_compatible_field_changes() -> None:
    dataset_id = uuid4()
    relation_id = uuid4()
    renamed_id, removed_id, required_id, unavailable_id, added_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    baseline_definition = _definition(
        relation_id,
        "customers",
        (
            _field(renamed_id, relation_id, "amount", label="Amount"),
            _field(removed_id, relation_id, "legacy"),
            _field(required_id, relation_id, "country"),
            _field(unavailable_id, relation_id, "email"),
        ),
    )
    baseline = DatasetVersion(
        id=uuid4(),
        dataset_id=dataset_id,
        state=DatasetVersionState.PUBLISHED,
        revision=2,
        definition=baseline_definition,
        validation=_validation(
            2,
            DatasetValidationState.VALID,
            (
                (renamed_id, "amount", "integer", False),
                (removed_id, "legacy", "text", True),
                (required_id, "country", "text", True),
                (unavailable_id, "email", "text", True),
            ),
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    draft_definition = _definition(
        relation_id,
        "customer_archive",
        (
            _field(renamed_id, relation_id, "total", column="amount", label="Total"),
            _field(required_id, relation_id, "country"),
            _field(unavailable_id, relation_id, "email"),
            _field(added_id, relation_id, "created_at"),
        ),
    )
    draft = DatasetDraft(
        id=uuid4(),
        dataset_id=dataset_id,
        revision=3,
        definition=draft_definition,
        validation=_validation(
            3,
            DatasetValidationState.INVALID,
            (
                (renamed_id, "total", "numeric(12,2)", True),
                (required_id, "country", "text", False),
                (added_id, "created_at", "timestamp with time zone", False),
            ),
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )

    impact = compare_dataset_schema(dataset_id=dataset_id, baseline=baseline, draft=draft)
    kinds = [change.kind for change in impact.changes]

    assert impact.ready_for_publication is False
    assert impact.breaking_change_count == 5
    assert SchemaChangeKind.FIELD_RENAMED in kinds
    assert SchemaChangeKind.FIELD_TYPE_CHANGED in kinds
    assert SchemaChangeKind.FIELD_BECAME_NULLABLE in kinds
    assert SchemaChangeKind.FIELD_DISPLAY_CHANGED in kinds
    assert SchemaChangeKind.FIELD_SOURCE_CHANGED in kinds
    assert SchemaChangeKind.FIELD_REMOVED in kinds
    assert SchemaChangeKind.FIELD_BECAME_REQUIRED in kinds
    assert SchemaChangeKind.FIELD_UNAVAILABLE in kinds
    assert SchemaChangeKind.FIELD_ADDED in kinds


def test_current_revision_validation_is_required() -> None:
    dataset_id = uuid4()
    relation_id = uuid4()
    field_id = uuid4()
    draft = DatasetDraft(
        id=uuid4(),
        dataset_id=dataset_id,
        revision=2,
        definition=_definition(
            relation_id,
            "customers",
            (_field(field_id, relation_id, "id"),),
        ),
        validation=_validation(
            1, DatasetValidationState.VALID, ((field_id, "id", "bigint", False),)
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )

    try:
        compare_dataset_schema(dataset_id=dataset_id, baseline=None, draft=draft)
    except ValueError as exc:
        assert "current draft revision" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("Expected stale validation to be rejected")


def test_breaking_field_change_reports_real_chart_dependency() -> None:
    dataset_id = uuid4()
    relation_id = uuid4()
    field_id = uuid4()
    chart_id = uuid4()
    baseline_definition = _definition(
        relation_id,
        "orders",
        (_field(field_id, relation_id, "amount"),),
    )
    baseline = DatasetVersion(
        id=uuid4(),
        dataset_id=dataset_id,
        state=DatasetVersionState.PUBLISHED,
        revision=1,
        definition=baseline_definition,
        validation=_validation(
            1,
            DatasetValidationState.VALID,
            ((field_id, "amount", "numeric(12,2)", False),),
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    draft = DatasetDraft(
        id=uuid4(),
        dataset_id=dataset_id,
        revision=2,
        definition=_definition(
            relation_id,
            "orders",
            (_field(field_id, relation_id, "amount"),),
        ),
        validation=_validation(
            2,
            DatasetValidationState.VALID,
            ((field_id, "amount", "bigint", False),),
        ),
        created_by=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    dependency = DatasetDependencyReference(DependencyResourceType.CHART, chart_id)

    impact = compare_dataset_schema(
        dataset_id=dataset_id,
        baseline=baseline,
        draft=draft,
        dependencies={field_id: (dependency,)},
    )

    assert impact.affected_dependencies == (dependency,)
    assert impact.blocking_dependencies == (dependency,)
    assert impact.ready_for_publication is False
