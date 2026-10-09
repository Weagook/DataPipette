"""Identifier-safe SQL compilation for visual dataset definitions."""

from psycopg import sql

from datepipette.domain.datasets import VisualDatasetDefinition


def compile_visual_select(definition: VisualDatasetDefinition) -> sql.Composed:
    """Compile one structured visual definition without interpolating identifiers."""

    relation = definition.relation
    fields = sql.SQL(", ").join(
        sql.SQL("{alias}.{column} AS {output}").format(
            alias=sql.Identifier(relation.alias),
            column=sql.Identifier(field.source.column),
            output=sql.Identifier(field.name),
        )
        for field in definition.fields
    )
    return sql.SQL("SELECT {fields} FROM {relation} AS {alias}").format(
        fields=fields,
        relation=sql.Identifier(relation.schema_name, relation.relation_name),
        alias=sql.Identifier(relation.alias),
    )
