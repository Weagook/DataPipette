"""PostgreSQL validation and safe SQL compilation for dataset drafts."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from datepipette.application.dataset_validation import (
    SourceDatasetValidationUnavailableError,
    SourceValidationResult,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import (
    DatasetDefinition,
    DatasetPlanSummary,
    DatasetValidationFinding,
    DatasetValidationState,
    SqlDatasetDefinition,
    ValidatedDatasetField,
    ValidationFindingCode,
    ValidationSeverity,
    VisualJoinType,
)
from datepipette.infrastructure.dataset_sql import compile_dataset_select, compile_visual_select
from datepipette.infrastructure.source_postgres import open_read_only_source

LARGE_ESTIMATED_RESULT_ROWS = 1_000_000

_RELATION_SQL = """
SELECT relation.oid
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = %s
  AND relation.relname = %s
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND pg_catalog.has_schema_privilege(namespace.oid, 'USAGE')
  AND pg_catalog.has_table_privilege(relation.oid, 'SELECT')
"""

_COLUMNS_SQL = """
SELECT attribute.attname,
       pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
       NOT attribute.attnotnull
FROM pg_catalog.pg_attribute AS attribute
WHERE attribute.attrelid = %s
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND pg_catalog.has_column_privilege(attribute.attrelid, attribute.attnum, 'SELECT')
ORDER BY attribute.attnum
"""


class PsycopgSourceDatasetValidator:
    def __init__(self, connect_timeout_seconds: int, statement_timeout_seconds: int) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds

    async def validate(
        self,
        connection: PostgresConnection,
        *,
        password: str,
        ca_certificate: str | None,
        definition: DatasetDefinition,
    ) -> SourceValidationResult:
        try:
            async with (
                open_read_only_source(
                    connection,
                    password=password,
                    ca_certificate=ca_certificate,
                    connect_timeout_seconds=self._connect_timeout,
                    statement_timeout_seconds=self._statement_timeout,
                ) as source,
                source.cursor() as cursor,
            ):
                if isinstance(definition, SqlDatasetDefinition):
                    return await self._validate_sql(source, cursor, definition)
                relation_columns: dict[UUID, dict[str, tuple[str, bool]]] = {}
                relation_findings: list[DatasetValidationFinding] = []
                for relation in definition.relations:
                    await cursor.execute(
                        _RELATION_SQL,
                        (relation.schema_name, relation.relation_name),
                    )
                    relation_row = await cursor.fetchone()
                    if relation_row is None:
                        relation_findings.append(
                            DatasetValidationFinding(
                                code=ValidationFindingCode.SOURCE_RELATION_NOT_FOUND,
                                severity=ValidationSeverity.ERROR,
                                relation_id=relation.id,
                            )
                        )
                        continue
                    await cursor.execute(_COLUMNS_SQL, (int(relation_row[0]),))
                    relation_columns[relation.id] = {
                        str(row[0]): (str(row[1]), bool(row[2])) for row in await cursor.fetchall()
                    }
                if relation_findings:
                    return SourceValidationResult(
                        state=DatasetValidationState.INVALID,
                        compiled_sql=None,
                        result_schema=(),
                        plan=None,
                        findings=tuple(relation_findings),
                    )

                nullable_relations: set[UUID] = set()
                for join in definition.joins:
                    if (
                        join.type is VisualJoinType.LEFT
                        or join.left_relation_id in nullable_relations
                    ):
                        nullable_relations.add(join.right_relation_id)
                result_schema = tuple(
                    ValidatedDatasetField(
                        field_id=field.id,
                        name=field.name,
                        data_type=relation_columns[field.source.relation_id][field.source.column][
                            0
                        ],
                        nullable=(
                            relation_columns[field.source.relation_id][field.source.column][1]
                            or field.source.relation_id in nullable_relations
                        ),
                    )
                    for field in definition.fields
                    if field.source.column in relation_columns[field.source.relation_id]
                )
                missing_fields = tuple(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SOURCE_FIELD_NOT_FOUND,
                        severity=ValidationSeverity.ERROR,
                        field_id=field.id,
                        relation_id=field.source.relation_id,
                    )
                    for field in definition.fields
                    if field.source.column not in relation_columns[field.source.relation_id]
                )
                missing_joins = tuple(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SOURCE_JOIN_COLUMN_NOT_FOUND,
                        severity=ValidationSeverity.ERROR,
                        relation_id=relation_id,
                        join_id=join.id,
                    )
                    for join in definition.joins
                    for relation_id, columns in (
                        (
                            join.left_relation_id,
                            tuple(condition.left_column for condition in join.conditions),
                        ),
                        (
                            join.right_relation_id,
                            tuple(condition.right_column for condition in join.conditions),
                        ),
                    )
                    if any(column not in relation_columns[relation_id] for column in columns)
                )
                missing = missing_fields + missing_joins
                if missing:
                    return SourceValidationResult(
                        state=DatasetValidationState.INVALID,
                        compiled_sql=None,
                        result_schema=result_schema,
                        plan=None,
                        findings=missing,
                    )

                select_query = compile_visual_select(definition)
                compiled_sql = select_query.as_string(source)
                explain_query = sql.SQL("EXPLAIN (FORMAT JSON) {}").format(select_query)
                await cursor.execute(explain_query)
                explain_row = await cursor.fetchone()
                plan = self._plan_summary(explain_row)
                state, findings = self._classify(plan)
                return SourceValidationResult(
                    state=state,
                    compiled_sql=compiled_sql,
                    result_schema=result_schema,
                    plan=plan,
                    findings=findings,
                )
        except (TimeoutError, psycopg.Error, IndexError, KeyError, TypeError, ValueError) as exc:
            raise SourceDatasetValidationUnavailableError from exc

    async def _validate_sql(
        self,
        source: psycopg.AsyncConnection[Any],
        cursor: psycopg.AsyncCursor[Any],
        definition: SqlDatasetDefinition,
    ) -> SourceValidationResult:
        for relation in definition.source_relations:
            await cursor.execute(
                _RELATION_SQL,
                (relation.schema_name, relation.relation_name),
            )
            if await cursor.fetchone() is None:
                return SourceValidationResult(
                    state=DatasetValidationState.INVALID,
                    compiled_sql=None,
                    result_schema=(),
                    plan=None,
                    findings=(
                        DatasetValidationFinding(
                            code=ValidationFindingCode.SQL_SOURCE_RELATION_NOT_FOUND,
                            severity=ValidationSeverity.ERROR,
                        ),
                    ),
                )

        select_query = compile_dataset_select(definition)
        try:
            await cursor.execute(sql.SQL("EXPLAIN (FORMAT JSON) {}").format(select_query))
            explain_row = await cursor.fetchone()
            plan = self._plan_summary(explain_row)
            await cursor.execute(
                sql.SQL('SELECT * FROM ({}) AS "_datepipette_validation" LIMIT 0').format(
                    select_query
                )
            )
        except psycopg.ProgrammingError:
            await source.rollback()
            return SourceValidationResult(
                state=DatasetValidationState.INVALID,
                compiled_sql=None,
                result_schema=(),
                plan=None,
                findings=(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SQL_QUERY_INVALID,
                        severity=ValidationSeverity.ERROR,
                    ),
                ),
            )
        description = cursor.description
        if description is None:
            raise ValueError("PostgreSQL returned no SQL result metadata")
        actual_names = tuple(column.name for column in description)
        expected_names = tuple(field.name for field in definition.fields)
        if actual_names != expected_names:
            return SourceValidationResult(
                state=DatasetValidationState.INVALID,
                compiled_sql=None,
                result_schema=(),
                plan=plan,
                findings=(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SQL_RESULT_SCHEMA_MISMATCH,
                        severity=ValidationSeverity.ERROR,
                    ),
                ),
            )
        type_oids = tuple({int(column.type_code) for column in description})
        await cursor.execute(
            """
            SELECT type.oid, pg_catalog.format_type(type.oid, NULL)
            FROM pg_catalog.pg_type AS type
            WHERE type.oid = ANY(%s::oid[])
            """,
            (list(type_oids),),
        )
        types = {int(row[0]): str(row[1]) for row in await cursor.fetchall()}
        result_schema = tuple(
            ValidatedDatasetField(
                field_id=field.id,
                name=field.name,
                data_type=types[int(column.type_code)],
                nullable=True,
            )
            for field, column in zip(definition.fields, description, strict=True)
        )
        state, findings = self._classify(plan)
        return SourceValidationResult(
            state=state,
            compiled_sql=select_query.as_string(source),
            result_schema=result_schema,
            plan=plan,
            findings=findings,
        )

    @staticmethod
    def _plan_summary(explain_row: tuple[Any, ...] | None) -> DatasetPlanSummary:
        if explain_row is None:
            raise ValueError("PostgreSQL returned no plan")
        document = explain_row[0]
        if not isinstance(document, list) or not document:
            raise ValueError("PostgreSQL returned an invalid plan")
        plan = document[0]["Plan"]
        return DatasetPlanSummary(
            node_type=str(plan["Node Type"]),
            estimated_rows=int(plan["Plan Rows"]),
            startup_cost=float(plan["Startup Cost"]),
            total_cost=float(plan["Total Cost"]),
        )

    @staticmethod
    def _classify(
        plan: DatasetPlanSummary,
    ) -> tuple[DatasetValidationState, tuple[DatasetValidationFinding, ...]]:
        if plan.estimated_rows < LARGE_ESTIMATED_RESULT_ROWS:
            return DatasetValidationState.VALID, ()
        return (
            DatasetValidationState.VALID_WITH_WARNINGS,
            (
                DatasetValidationFinding(
                    code=ValidationFindingCode.LARGE_ESTIMATED_RESULT,
                    severity=ValidationSeverity.WARNING,
                ),
            ),
        )
