"""Visual dataset definition invariants."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from datepipette.domain.datasets import (
    DatasetPlanSummary,
    DatasetRecommendationCode,
    DatasetRelationStatistics,
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
    VisualJoin,
    VisualJoinCondition,
    VisualJoinType,
    VisualRelation,
    build_dataset_recommendations,
)
from datepipette.infrastructure.dataset_sql import compile_visual_select


def _definition() -> VisualDatasetDefinition:
    relation_id = uuid4()
    return VisualDatasetDefinition(
        relations=(
            VisualRelation(
                id=relation_id,
                schema_name=" crm ",
                relation_name=" customers ",
                alias="customers",
            ),
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
        ("foreign_relation", "source relation"),
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
        VisualDatasetDefinition(relations=valid.relations, fields=tuple(fields))


def test_stored_v1_definition_rejects_joins_and_unknown_versions() -> None:
    document = _definition().to_document()
    document["joins"] = [{"type": "inner"}]
    with pytest.raises(ValueError, match="Stored visual definition"):
        VisualDatasetDefinition.from_document(document)

    document = _definition().to_document()
    document["schema_version"] = 3
    with pytest.raises(ValueError, match="Stored visual definition"):
        VisualDatasetDefinition.from_document(document)


def _joined_definition() -> VisualDatasetDefinition:
    customers_id = uuid4()
    orders_id = uuid4()
    return VisualDatasetDefinition(
        schema_version=2,
        relations=(
            VisualRelation(customers_id, "crm", "customers", "customers"),
            VisualRelation(orders_id, "sales", "orders", "orders"),
        ),
        fields=(
            VisualField(
                uuid4(),
                FieldSource(customers_id, "id"),
                "customer_id",
                FieldDisplay("Customer ID"),
            ),
            VisualField(
                uuid4(),
                FieldSource(orders_id, "amount"),
                "order_amount",
                FieldDisplay("Order amount"),
            ),
        ),
        joins=(
            VisualJoin(
                uuid4(),
                VisualJoinType.LEFT,
                customers_id,
                orders_id,
                (VisualJoinCondition("id", "customer_id"),),
            ),
        ),
    )


def test_joined_definition_round_trips_and_compiles_identifier_safe_sql() -> None:
    definition = _joined_definition()

    document = definition.to_document()

    assert document["schema_version"] == 2
    assert len(document["source"]["relations"]) == 2
    assert document["joins"][0]["type"] == "left"
    assert VisualDatasetDefinition.from_document(document) == definition
    assert compile_visual_select(definition).as_string() == (
        'SELECT "customers"."id" AS "customer_id", '
        '"orders"."amount" AS "order_amount" '
        'FROM "crm"."customers" AS "customers" '
        'LEFT JOIN "sales"."orders" AS "orders" '
        'ON "customers"."id" = "orders"."customer_id"'
    )

    join = definition.joins[0]
    inner = VisualDatasetDefinition(
        schema_version=2,
        relations=definition.relations,
        fields=definition.fields,
        joins=(
            VisualJoin(
                join.id,
                VisualJoinType.INNER,
                join.left_relation_id,
                join.right_relation_id,
                join.conditions,
            ),
        ),
    )
    assert " INNER JOIN " in compile_visual_select(inner).as_string()

    hostile = VisualDatasetDefinition(
        schema_version=2,
        relations=definition.relations,
        fields=definition.fields,
        joins=(
            VisualJoin(
                join.id,
                VisualJoinType.LEFT,
                join.left_relation_id,
                join.right_relation_id,
                (VisualJoinCondition('id" OR true --', "customer_id"),),
            ),
        ),
    )
    hostile_sql = compile_visual_select(hostile).as_string()
    assert '"id"" OR true --"' in hostile_sql
    assert 'ON "customers"."id" OR true' not in hostile_sql


def test_recommendations_identify_visual_join_columns_without_generating_ddl() -> None:
    definition = _joined_definition()
    join = definition.joins[0]
    orders_id = definition.relations[1].id
    findings = (
        DatasetValidationFinding(
            code=ValidationFindingCode.LARGE_SEQUENTIAL_SCAN,
            severity=ValidationSeverity.WARNING,
            relation_id=orders_id,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.STALE_RELATION_STATISTICS,
            severity=ValidationSeverity.WARNING,
            relation_id=orders_id,
        ),
    )

    recommendations = build_dataset_recommendations(definition, findings)

    assert [item.code for item in recommendations] == [
        DatasetRecommendationCode.REVIEW_SEQUENTIAL_SCAN,
        DatasetRecommendationCode.CONSIDER_JOIN_INDEX,
        DatasetRecommendationCode.REFRESH_STATISTICS,
    ]
    candidate = recommendations[1]
    assert candidate.relation_id == orders_id
    assert candidate.join_id == join.id
    assert candidate.column_names == ("customer_id",)
    assert "CREATE INDEX" not in repr(recommendations)


def test_recommendations_cover_global_findings_and_the_left_join_side_once() -> None:
    definition = _joined_definition()
    customers_id = definition.relations[0].id
    findings = (
        DatasetValidationFinding(
            code=ValidationFindingCode.LARGE_ESTIMATED_RESULT,
            severity=ValidationSeverity.WARNING,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.HIGH_PLANNER_COST,
            severity=ValidationSeverity.WARNING,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.TOO_MANY_JOINS,
            severity=ValidationSeverity.WARNING,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.LARGE_SORT,
            severity=ValidationSeverity.WARNING,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.LARGE_SORT,
            severity=ValidationSeverity.WARNING,
        ),
        DatasetValidationFinding(
            code=ValidationFindingCode.LARGE_SEQUENTIAL_SCAN,
            severity=ValidationSeverity.WARNING,
            relation_id=customers_id,
        ),
    )

    recommendations = build_dataset_recommendations(definition, findings)

    assert [item.code for item in recommendations] == [
        DatasetRecommendationCode.REDUCE_RESULT_SIZE,
        DatasetRecommendationCode.REVIEW_QUERY_COST,
        DatasetRecommendationCode.REDUCE_JOIN_COMPLEXITY,
        DatasetRecommendationCode.REVIEW_SORT,
        DatasetRecommendationCode.REVIEW_SEQUENTIAL_SCAN,
        DatasetRecommendationCode.CONSIDER_JOIN_INDEX,
    ]
    assert recommendations[-1].column_names == ("id",)


@pytest.mark.parametrize(
    "change, message",
    [
        ("duplicate_relation", "Relation identifiers"),
        ("duplicate_alias", "Relation aliases"),
        ("unknown_field_relation", "source relation"),
        ("missing_join", "exactly one join"),
        ("reused_right", "right relation must be new"),
        ("unconnected_left", "left relation must already be connected"),
        ("duplicate_join_id", "Join identifiers"),
    ],
)
def test_joined_definition_rejects_ambiguous_or_disconnected_graphs(
    change: str, message: str
) -> None:
    valid = _joined_definition()
    relations = list(valid.relations)
    fields = list(valid.fields)
    joins = list(valid.joins)
    if change == "duplicate_relation":
        relations[1] = VisualRelation(
            relations[0].id,
            relations[1].schema_name,
            relations[1].relation_name,
            relations[1].alias,
        )
    elif change == "duplicate_alias":
        relations[1] = VisualRelation(
            relations[1].id,
            relations[1].schema_name,
            relations[1].relation_name,
            "CUSTOMERS",
        )
    elif change == "unknown_field_relation":
        fields[1] = VisualField(
            fields[1].id,
            FieldSource(uuid4(), fields[1].source.column),
            fields[1].name,
            fields[1].display,
        )
    elif change == "missing_join":
        joins = []
    elif change == "reused_right":
        third_id = uuid4()
        relations.append(VisualRelation(third_id, "sales", "payments", "payments"))
        joins.append(
            VisualJoin(
                uuid4(),
                VisualJoinType.INNER,
                relations[0].id,
                relations[1].id,
                (VisualJoinCondition("id", "customer_id"),),
            )
        )
    elif change == "unconnected_left":
        joins[0] = VisualJoin(
            joins[0].id,
            joins[0].type,
            relations[1].id,
            relations[0].id,
            joins[0].conditions,
        )
    elif change == "duplicate_join_id":
        third_id = uuid4()
        relations.append(VisualRelation(third_id, "sales", "payments", "payments"))
        joins.append(
            VisualJoin(
                joins[0].id,
                VisualJoinType.INNER,
                relations[1].id,
                third_id,
                (VisualJoinCondition("id", "order_id"),),
            )
        )

    with pytest.raises(ValueError, match=message):
        VisualDatasetDefinition(
            schema_version=2,
            relations=tuple(relations),
            fields=tuple(fields),
            joins=tuple(joins),
        )


def test_dataset_validation_round_trips_as_revision_bound_json() -> None:
    definition = _joined_definition()
    recommendations = build_dataset_recommendations(
        definition,
        (
            DatasetValidationFinding(
                code=ValidationFindingCode.LARGE_SEQUENTIAL_SCAN,
                severity=ValidationSeverity.WARNING,
                relation_id=definition.relations[1].id,
            ),
        ),
    )
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
                observed_value=1_000_000,
                threshold_value=1_000_000,
            ),
        ),
        relation_statistics=(
            DatasetRelationStatistics(
                relation_id=None,
                schema_name="crm",
                relation_name="customers",
                approximate_rows=1_000_000,
                relation_bytes=8_000_000,
                index_bytes=1_000_000,
                visible_index_count=1,
                statistics_updated_at=datetime(2026, 10, 8, 8, 0, tzinfo=UTC),
            ),
        ),
        recommendations=recommendations,
    )

    document = validation.to_document()

    assert document["validated_revision"] == 3
    assert document["findings"][0]["code"] == "large_estimated_result"
    assert document["recommendations"][1]["column_names"] == ["customer_id"]
    assert DatasetValidation.from_document(document) == validation

    legacy_document = validation.to_document()
    legacy_document.pop("relation_statistics")
    legacy_document.pop("recommendations")
    assert legacy_document["plan"] is not None
    for key in (
        "node_count",
        "join_count",
        "sort_count",
        "max_plan_rows",
        "max_sequential_scan_rows",
        "max_sort_rows",
    ):
        legacy_document["plan"].pop(key)
    legacy_document["findings"][0].pop("observed_value")
    legacy_document["findings"][0].pop("threshold_value")
    restored_legacy = DatasetValidation.from_document(legacy_document)
    assert restored_legacy.relation_statistics == ()
    assert restored_legacy.recommendations == ()
    assert restored_legacy.plan is not None
    assert restored_legacy.plan.max_plan_rows == 1_000_000
    assert restored_legacy.findings[0].observed_value is None

    document["state"] = "unknown"
    with pytest.raises(ValueError, match="Stored dataset validation"):
        DatasetValidation.from_document(document)
