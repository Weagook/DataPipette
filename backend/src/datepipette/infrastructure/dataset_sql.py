"""Identifier-safe SQL compilation for visual dataset definitions."""

from psycopg import sql

from datepipette.domain.datasets import VisualDatasetDefinition, VisualJoinType

_JOIN_SQL = {
    VisualJoinType.INNER: sql.SQL("INNER JOIN"),
    VisualJoinType.LEFT: sql.SQL("LEFT JOIN"),
}


def compile_visual_select(definition: VisualDatasetDefinition) -> sql.Composed:
    """Compile one structured visual definition without interpolating identifiers."""

    relations = {relation.id: relation for relation in definition.relations}
    root = definition.relations[0]
    fields = sql.SQL(", ").join(
        sql.SQL("{alias}.{column} AS {output}").format(
            alias=sql.Identifier(relations[field.source.relation_id].alias),
            column=sql.Identifier(field.source.column),
            output=sql.Identifier(field.name),
        )
        for field in definition.fields
    )
    query = sql.SQL("SELECT {fields} FROM {relation} AS {alias}").format(
        fields=fields,
        relation=sql.Identifier(root.schema_name, root.relation_name),
        alias=sql.Identifier(root.alias),
    )
    for join in definition.joins:
        left = relations[join.left_relation_id]
        right = relations[join.right_relation_id]
        conditions = sql.SQL(" AND ").join(
            sql.SQL("{left_alias}.{left_column} = {right_alias}.{right_column}").format(
                left_alias=sql.Identifier(left.alias),
                left_column=sql.Identifier(condition.left_column),
                right_alias=sql.Identifier(right.alias),
                right_column=sql.Identifier(condition.right_column),
            )
            for condition in join.conditions
        )
        query += sql.SQL(" {join_type} {relation} AS {alias} ON {conditions}").format(
            join_type=_JOIN_SQL[join.type],
            relation=sql.Identifier(right.schema_name, right.relation_name),
            alias=sql.Identifier(right.alias),
            conditions=conditions,
        )
    return query
