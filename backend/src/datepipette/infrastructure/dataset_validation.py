"""PostgreSQL validation, plan advice, and safe SQL compilation for drafts."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    DatasetRelationStatistics,
    DatasetValidationFinding,
    DatasetValidationState,
    QuerySafetyPolicy,
    SqlDatasetDefinition,
    ValidatedDatasetField,
    ValidationFindingCode,
    ValidationSeverity,
    VisualJoinType,
)
from datepipette.infrastructure.dataset_sql import compile_dataset_select, compile_visual_select
from datepipette.infrastructure.source_postgres import open_read_only_source

_RELATION_SQL = """
SELECT relation.oid,
       GREATEST(relation.reltuples, 0)::bigint,
       pg_catalog.pg_relation_size(relation.oid),
       pg_catalog.pg_indexes_size(relation.oid),
       (
           SELECT count(*)
           FROM pg_catalog.pg_index AS relation_index
           WHERE relation_index.indrelid = relation.oid
             AND relation_index.indisvalid
             AND relation_index.indisready
       ),
       statistics.last_analyze,
       statistics.last_autoanalyze
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
LEFT JOIN pg_catalog.pg_stat_all_tables AS statistics ON statistics.relid = relation.oid
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


@dataclass(frozen=True, slots=True)
class _SequentialScan:
    schema_name: str | None
    relation_name: str | None
    estimated_rows: int


@dataclass(frozen=True, slots=True)
class _PlanAnalysis:
    summary: DatasetPlanSummary
    sequential_scans: tuple[_SequentialScan, ...]


def _latest_timestamp(first: Any, second: Any) -> datetime | None:
    values = [value for value in (first, second) if isinstance(value, datetime)]
    return max(values) if values else None


class PsycopgSourceDatasetValidator:
    def __init__(
        self,
        connect_timeout_seconds: int,
        statement_timeout_seconds: int,
        policy: QuerySafetyPolicy | None = None,
    ) -> None:
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds
        self._policy = policy or QuerySafetyPolicy()

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
                relation_statistics: list[DatasetRelationStatistics] = []
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
                    relation_statistics.append(
                        self._relation_statistics(
                            relation_row,
                            relation_id=relation.id,
                            schema_name=relation.schema_name,
                            relation_name=relation.relation_name,
                        )
                    )
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
                        relation_statistics=tuple(relation_statistics),
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
                        relation_statistics=tuple(relation_statistics),
                    )

                select_query = compile_visual_select(definition)
                compiled_sql = select_query.as_string(source)
                explain_query = sql.SQL("EXPLAIN (FORMAT JSON) {}").format(select_query)
                await cursor.execute(explain_query)
                explain_row = await cursor.fetchone()
                analysis = self._analyze_plan(explain_row)
                state, findings = self._classify(
                    analysis,
                    tuple(relation_statistics),
                )
                return SourceValidationResult(
                    state=state,
                    compiled_sql=compiled_sql,
                    result_schema=result_schema,
                    plan=analysis.summary,
                    findings=findings,
                    relation_statistics=tuple(relation_statistics),
                )
        except (TimeoutError, psycopg.Error, IndexError, KeyError, TypeError, ValueError) as exc:
            raise SourceDatasetValidationUnavailableError from exc

    async def _validate_sql(
        self,
        source: psycopg.AsyncConnection[Any],
        cursor: psycopg.AsyncCursor[Any],
        definition: SqlDatasetDefinition,
    ) -> SourceValidationResult:
        relation_statistics: list[DatasetRelationStatistics] = []
        for relation in definition.source_relations:
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
                            code=ValidationFindingCode.SQL_SOURCE_RELATION_NOT_FOUND,
                            severity=ValidationSeverity.ERROR,
                        ),
                    ),
                    relation_statistics=tuple(relation_statistics),
                )
            relation_statistics.append(
                self._relation_statistics(
                    relation_row,
                    relation_id=None,
                    schema_name=relation.schema_name,
                    relation_name=relation.relation_name,
                )
            )

        select_query = compile_dataset_select(definition)
        try:
            await cursor.execute(sql.SQL("EXPLAIN (FORMAT JSON) {}").format(select_query))
            explain_row = await cursor.fetchone()
            analysis = self._analyze_plan(explain_row)
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
                relation_statistics=tuple(relation_statistics),
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
                plan=analysis.summary,
                findings=(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.SQL_RESULT_SCHEMA_MISMATCH,
                        severity=ValidationSeverity.ERROR,
                    ),
                ),
                relation_statistics=tuple(relation_statistics),
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
        state, findings = self._classify(analysis, tuple(relation_statistics))
        return SourceValidationResult(
            state=state,
            compiled_sql=select_query.as_string(source),
            result_schema=result_schema,
            plan=analysis.summary,
            findings=findings,
            relation_statistics=tuple(relation_statistics),
        )

    @staticmethod
    def _relation_statistics(
        row: tuple[Any, ...],
        *,
        relation_id: UUID | None,
        schema_name: str,
        relation_name: str,
    ) -> DatasetRelationStatistics:
        return DatasetRelationStatistics(
            relation_id=relation_id,
            schema_name=schema_name,
            relation_name=relation_name,
            approximate_rows=int(row[1]),
            relation_bytes=int(row[2]),
            index_bytes=int(row[3]),
            visible_index_count=int(row[4]),
            statistics_updated_at=_latest_timestamp(row[5], row[6]),
        )

    @staticmethod
    def _analyze_plan(explain_row: tuple[Any, ...] | None) -> _PlanAnalysis:
        if explain_row is None:
            raise ValueError("PostgreSQL returned no plan")
        document = explain_row[0]
        if not isinstance(document, list) or not document:
            raise ValueError("PostgreSQL returned an invalid plan")
        root = document[0]["Plan"]
        if not isinstance(root, dict):
            raise ValueError("PostgreSQL returned an invalid plan")
        stack = [root]
        node_count = 0
        join_count = 0
        sort_count = 0
        max_plan_rows = 0
        max_sequential_scan_rows = 0
        max_sort_rows = 0
        sequential_scans: list[_SequentialScan] = []
        while stack:
            node = stack.pop()
            node_type = str(node["Node Type"])
            plan_rows = int(node["Plan Rows"])
            node_count += 1
            max_plan_rows = max(max_plan_rows, plan_rows)
            if node_type == "Nested Loop" or node_type.endswith(" Join"):
                join_count += 1
            if "Sort" in node_type:
                sort_count += 1
                max_sort_rows = max(max_sort_rows, plan_rows)
            if node_type.endswith("Seq Scan"):
                max_sequential_scan_rows = max(max_sequential_scan_rows, plan_rows)
                sequential_scans.append(
                    _SequentialScan(
                        schema_name=(
                            str(node["Schema"]) if node.get("Schema") is not None else None
                        ),
                        relation_name=(
                            str(node["Relation Name"])
                            if node.get("Relation Name") is not None
                            else None
                        ),
                        estimated_rows=plan_rows,
                    )
                )
            children = node.get("Plans", [])
            if not isinstance(children, list):
                raise ValueError("PostgreSQL returned an invalid plan")
            if any(not isinstance(child, dict) for child in children):
                raise ValueError("PostgreSQL returned an invalid plan")
            stack.extend(children)
        summary = DatasetPlanSummary(
            node_type=str(root["Node Type"]),
            estimated_rows=int(root["Plan Rows"]),
            startup_cost=float(root["Startup Cost"]),
            total_cost=float(root["Total Cost"]),
            node_count=node_count,
            join_count=join_count,
            sort_count=sort_count,
            max_plan_rows=max_plan_rows,
            max_sequential_scan_rows=max_sequential_scan_rows,
            max_sort_rows=max_sort_rows,
        )
        return _PlanAnalysis(summary=summary, sequential_scans=tuple(sequential_scans))

    @classmethod
    def _plan_summary(cls, explain_row: tuple[Any, ...] | None) -> DatasetPlanSummary:
        return cls._analyze_plan(explain_row).summary

    def _classify(
        self,
        analysis: _PlanAnalysis,
        relation_statistics: tuple[DatasetRelationStatistics, ...] = (),
        *,
        evaluated_at: datetime | None = None,
    ) -> tuple[DatasetValidationState, tuple[DatasetValidationFinding, ...]]:
        plan = analysis.summary
        policy = self._policy
        findings: list[DatasetValidationFinding] = []

        def threshold_finding(
            *,
            value: float,
            warning: float,
            block: float,
            code: ValidationFindingCode,
        ) -> None:
            if value >= block:
                findings.append(
                    DatasetValidationFinding(
                        code=code,
                        severity=ValidationSeverity.BLOCKED,
                        observed_value=value,
                        threshold_value=block,
                    )
                )
            elif value >= warning:
                findings.append(
                    DatasetValidationFinding(
                        code=code,
                        severity=ValidationSeverity.WARNING,
                        observed_value=value,
                        threshold_value=warning,
                    )
                )

        threshold_finding(
            value=plan.estimated_rows,
            warning=policy.estimated_rows_warning,
            block=policy.estimated_rows_block,
            code=ValidationFindingCode.LARGE_ESTIMATED_RESULT,
        )
        threshold_finding(
            value=plan.total_cost,
            warning=policy.total_cost_warning,
            block=policy.total_cost_block,
            code=ValidationFindingCode.HIGH_PLANNER_COST,
        )
        threshold_finding(
            value=plan.join_count,
            warning=policy.join_count_warning,
            block=policy.join_count_block,
            code=ValidationFindingCode.TOO_MANY_JOINS,
        )

        statistics_by_name: dict[str, list[DatasetRelationStatistics]] = {}
        for item in relation_statistics:
            statistics_by_name.setdefault(item.relation_name, []).append(item)
        reported_scans: set[tuple[str | None, str | None]] = set()
        for scan in sorted(
            analysis.sequential_scans,
            key=lambda item: item.estimated_rows,
            reverse=True,
        ):
            if scan.estimated_rows < policy.sequential_scan_rows_warning:
                continue
            key = (scan.schema_name, scan.relation_name)
            if key in reported_scans:
                continue
            reported_scans.add(key)
            candidates = statistics_by_name.get(scan.relation_name or "", [])
            relation = next(
                (
                    item
                    for item in candidates
                    if scan.schema_name is None or item.schema_name == scan.schema_name
                ),
                None,
            )
            findings.append(
                DatasetValidationFinding(
                    code=ValidationFindingCode.LARGE_SEQUENTIAL_SCAN,
                    severity=ValidationSeverity.WARNING,
                    relation_id=relation.relation_id if relation else None,
                    observed_value=scan.estimated_rows,
                    threshold_value=policy.sequential_scan_rows_warning,
                )
            )
            if relation is not None and relation.visible_index_count == 0:
                findings.append(
                    DatasetValidationFinding(
                        code=ValidationFindingCode.NO_VISIBLE_INDEX_FOR_LARGE_SCAN,
                        severity=ValidationSeverity.INFO,
                        relation_id=relation.relation_id,
                        observed_value=0,
                    )
                )

        if plan.max_sort_rows >= policy.sort_rows_warning:
            findings.append(
                DatasetValidationFinding(
                    code=ValidationFindingCode.LARGE_SORT,
                    severity=ValidationSeverity.WARNING,
                    observed_value=plan.max_sort_rows,
                    threshold_value=policy.sort_rows_warning,
                )
            )

        now = evaluated_at or datetime.now(UTC)
        stale_before = now - timedelta(days=policy.stale_statistics_days)
        for item in relation_statistics:
            if item.approximate_rows < policy.sequential_scan_rows_warning:
                continue
            updated_at = item.statistics_updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
            if updated_at is not None and updated_at >= stale_before:
                continue
            age_days = (now - updated_at).days if updated_at is not None else None
            findings.append(
                DatasetValidationFinding(
                    code=ValidationFindingCode.STALE_RELATION_STATISTICS,
                    severity=ValidationSeverity.WARNING,
                    relation_id=item.relation_id,
                    observed_value=float(age_days) if age_days is not None else None,
                    threshold_value=policy.stale_statistics_days,
                )
            )

        result = tuple(findings)
        if any(item.severity is ValidationSeverity.BLOCKED for item in result):
            return DatasetValidationState.BLOCKED, result
        if result:
            return DatasetValidationState.VALID_WITH_WARNINGS, result
        return DatasetValidationState.VALID, ()
