"""Advanced SQL dataset definition safety and compatibility tests."""

from uuid import uuid4

import pytest

from datapipette.domain.datasets import (
    DatasetDefinitionKind,
    FieldDisplay,
    SqlDatasetDefinition,
    SqlField,
    VisualDatasetDefinition,
    dataset_definition_from_document,
)


def _definition(
    statement: str = "SELECT customer.id AS customer_id FROM crm.customer",
) -> SqlDatasetDefinition:
    return SqlDatasetDefinition(
        statement=statement,
        fields=(SqlField(uuid4(), "customer_id", FieldDisplay("Customer ID")),),
    )


def test_sql_definition_normalizes_and_round_trips_with_source_relations() -> None:
    field_id = uuid4()
    definition = SqlDatasetDefinition(
        statement=(
            'WITH paid AS (SELECT "Order".customer_id FROM sales."Order") '
            "SELECT paid.customer_id AS customer_id FROM paid"
        ),
        fields=(SqlField(field_id, "customer_id", FieldDisplay("Customer ID")),),
    )

    assert definition.statement == (
        'WITH paid AS (SELECT "Order".customer_id FROM sales."Order") '
        "SELECT paid.customer_id AS customer_id FROM paid"
    )
    assert tuple(
        (relation.schema_name, relation.relation_name) for relation in definition.source_relations
    ) == (("sales", "Order"),)
    document = definition.to_document()
    assert document["kind"] == DatasetDefinitionKind.SQL.value
    assert SqlDatasetDefinition.from_document(document) == definition
    assert dataset_definition_from_document(document) == definition


@pytest.mark.parametrize(
    "statement",
    [
        "",
        "SELECT 1; SELECT 2",
        "DELETE FROM public.customer",
        "WITH changed AS (DELETE FROM public.customer RETURNING id) SELECT id FROM changed",
        "SELECT id INTO TEMP result FROM public.customer",
        "SELECT id FROM public.customer FOR UPDATE",
        "SELECT $1 AS customer_id FROM public.customer",
        "SELECT customer_id FROM customer",
        "SELECT customer_id FROM other_database.public.customer",
    ],
)
def test_sql_definition_rejects_unsafe_or_ambiguous_statements(statement: str) -> None:
    with pytest.raises(ValueError):
        _definition(statement)


def test_sql_definition_rejects_duplicate_output_identity_and_names() -> None:
    field_id = uuid4()
    with pytest.raises(ValueError):
        SqlDatasetDefinition(
            statement="SELECT 1 AS one, 2 AS two",
            fields=(
                SqlField(field_id, "one", FieldDisplay("One")),
                SqlField(field_id, "two", FieldDisplay("Two")),
            ),
        )
    with pytest.raises(ValueError):
        SqlDatasetDefinition(
            statement="SELECT 1 AS one, 2 AS two",
            fields=(
                SqlField(uuid4(), "duplicate", FieldDisplay("One")),
                SqlField(uuid4(), "DUPLICATE", FieldDisplay("Two")),
            ),
        )


def test_legacy_visual_document_without_kind_remains_readable() -> None:
    relation_id = uuid4()
    document = {
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": str(relation_id),
                    "schema": "crm",
                    "name": "customer",
                    "alias": "customer",
                }
            ]
        },
        "fields": [
            {
                "id": str(uuid4()),
                "source": {"relation_id": str(relation_id), "column": "id"},
                "name": "customer_id",
                "display": {"label": "Customer ID", "format": None, "visible": True},
            }
        ],
        "joins": [],
    }
    parsed = dataset_definition_from_document(document)

    assert isinstance(parsed, VisualDatasetDefinition)
    assert parsed.to_document()["kind"] == DatasetDefinitionKind.VISUAL.value
