"""PostgreSQL validation and safe SQL compilation for one-relation datasets."""

from typing import Any

import psycopg
from psycopg import sql

from datepipette.application.dataset_validation import (
    SourceDatasetValidationUnavailableError,
    SourceValidationResult,
)
from datepipette.domain.connections import PostgresConnection
from datepipette.domain.datasets import (
    DatasetPlanSummary,
    DatasetValidationFinding,
    DatasetValidationState,
    ValidatedDatasetField,
    ValidationFindingCode,
    ValidationSeverity,
    VisualDatasetDefinition,
)
from datepipette.infrastructure.dataset_sql import compile_visual_select
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
        definition: VisualDatasetDefinition,
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
                relation = definition.relation
                await cursor.execute(
                    _RELATION_SQL,
                    (relation.schema_name, relation.relation_name),
                )
                relation_row = await cursor.fetchone()
                if relation_row is None:
                    return SourceValidationResult(
                        state=DatasetValidationState.INVALID,
                        compiled_sql=None,
                        result_schema=(),
                        plan=None,
                        findings=(
                            DatasetValidationFinding(
                                code=ValidationFindingCode.SOURCE_RELATION_NOT_FOUND,
                                severity=ValidationSeverity.ERROR,
                            ),
                        ),
                    )

                await cursor.execute(_COLUMNS_SQL, (int(relation_row[0]),))
                columns = {
                    str(row[0]): (str(row[1]), bool(row[2])) for row in await cursor.fetchall()
                }
                result_schema = tuple(
                    ValidatedDatasetField(
                        field_id=field.id,
                        name=field.name,
                        data_type=columns[field.source.column][0],
                        nullable=columns[field.source.column][1],
                    )
                    for field in definition.fields
                    if field.source.column in columns
                )
                missing = tuple(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SOURCE_FIELD_NOT_FOUND,
                        severity=ValidationSeverity.ERROR,
                        field_id=field.id,
                    )
                    for field in definition.fields
                    if field.source.column not in columns
                )
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
