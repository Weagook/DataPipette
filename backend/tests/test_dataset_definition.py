"""Visual dataset definition invariants."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from datepipette.domain.datasets import (
    DatasetPlanSummary,
    DatasetValidation,
    DatasetValidationFinding,
    DatasetValidationState,
    FieldDisplay,
    FieldDisplayFormat,
    FieldSource,
    ValidatedDatasetField,
    ValidationFindingCode,
    ValidationSeverity,
    VisualDatasetDefinition,
    VisualField,
    VisualRelation,
)


def _definition() -> VisualDatasetDefinition:
    relation_id = uuid4()
    return VisualDatasetDefinition(
        relation=VisualRelation(
            id=relation_id,
            schema_name=" crm ",
            relation_name=" customers ",
            alias="customers",
        ),
        fields=(
            VisualField(
                id=uuid4(),
                source=FieldSource(relation_id=relation_id, column=" id "),
                name="customer_id",
                display=FieldDisplay(label=" Customer ID "),
            ),
            VisualField(
                id=uuid4(),
                source=FieldSource(relation_id=relation_id, column="created_at"),
                name="created_at",
                display=FieldDisplay(
                    label="Created at",
                    format=FieldDisplayFormat.DATE_DD_MM_YYYY,
                ),
            ),
        ),
    )


def test_visual_definition_round_trips_as_versioned_json() -> None:
    definition = _definition()

    document = definition.to_document()

    assert document["schema_version"] == 1
    assert document["source"]["relations"][0]["schema"] == "crm"
    assert document["fields"][0]["source"]["column"] == "id"
    assert document["fields"][1]["display"]["format"] == "date_dd_mm_yyyy"
    assert document["joins"] == []
    assert VisualDatasetDefinition.from_document(document) == definition


@pytest.mark.parametrize(
    "change, message",
    [
        ("duplicate_id", "identifiers"),
        ("duplicate_name", "names"),
        ("foreign_relation", "single source relation"),
        ("bad_output_name", "ASCII identifier"),
        ("empty_fields", "At least one field"),
    ],
)
def test_visual_definition_rejects_invalid_structure(change: str, message: str) -> None:
    valid = _definition()
    with pytest.raises(ValueError, match=message):
        fields = list(valid.fields)
        if change == "duplicate_id":
            fields[1] = VisualField(
                id=fields[0].id,
                source=fields[1].source,
                name=fields[1].name,
                display=fields[1].display,
            )
        elif change == "duplicate_name":
            fields[1] = VisualField(
                id=fields[1].id,
                source=fields[1].source,
                name="CUSTOMER_ID",
                display=fields[1].display,
            )
        elif change == "foreign_relation":
            fields[1] = VisualField(
                id=fields[1].id,
                source=FieldSource(relation_id=uuid4(), column="created_at"),
                name=fields[1].name,
                display=fields[1].display,
            )
        elif change == "bad_output_name":
            fields[1] = VisualField(
                id=fields[1].id,
                source=fields[1].source,
                name="Created at",
                display=fields[1].display,
            )
        elif change == "empty_fields":
            fields = []
        VisualDatasetDefinition(relation=valid.relation, fields=tuple(fields))


def test_stored_definition_rejects_joins_and_unknown_versions() -> None:
    document = _definition().to_document()
    document["joins"] = [{"type": "inner"}]
    with pytest.raises(ValueError, match="Stored visual definition"):
        VisualDatasetDefinition.from_document(document)

    document = _definition().to_document()
    document["schema_version"] = 2
    with pytest.raises(ValueError, match="Stored visual definition"):
        VisualDatasetDefinition.from_document(document)


def test_dataset_validation_round_trips_as_revision_bound_json() -> None:
    validation = DatasetValidation(
        state=DatasetValidationState.VALID_WITH_WARNINGS,
        validated_revision=3,
        validated_at=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        compiled_sql='SELECT "source"."id" AS "record_id" FROM "crm"."customers" AS "source"',
        result_schema=(
            ValidatedDatasetField(
                field_id=uuid4(),
                name="record_id",
                data_type="bigint",
                nullable=False,
            ),
        ),
        plan=DatasetPlanSummary(
            node_type="Seq Scan",
            estimated_rows=1_000_000,
            startup_cost=0.0,
            total_cost=10_000.0,
        ),
        findings=(
            DatasetValidationFinding(
                code=ValidationFindingCode.LARGE_ESTIMATED_RESULT,
                severity=ValidationSeverity.WARNING,
            ),
        ),
    )

    document = validation.to_document()

    assert document["validated_revision"] == 3
    assert document["findings"][0]["code"] == "large_estimated_result"
    assert DatasetValidation.from_document(document) == validation

    document["state"] = "unknown"
    with pytest.raises(ValueError, match="Stored dataset validation"):
        DatasetValidation.from_document(document)
