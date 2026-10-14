"""Application boundary for typed reads from the active dataset snapshot."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from datepipette.application.access_control import (
    DatasetAccessAuthorizer,
    DatasetAccessDeniedError,
)
from datepipette.application.query_safety import (
    InteractiveQueryRuntime,
    QuerySafetyPolicyReader,
)
from datepipette.domain.access_control import DatasetPermission
from datepipette.domain.identity import User
from datepipette.domain.snapshot_queries import (
    ActiveSnapshotQueryContext,
    DatasetExploration,
    DatasetExplorationColumn,
    DatasetFreshness,
    SnapshotFilterOperator,
    SnapshotQuery,
    SnapshotQueryCursorPosition,
    SnapshotQueryExecution,
    SnapshotQueryField,
    SnapshotQueryFilter,
    SnapshotQueryPage,
    SnapshotQuerySort,
)

MAX_QUERY_FIELDS = 100
MAX_QUERY_FILTERS = 20
MAX_QUERY_SORTS = 5
MAX_QUERY_IN_VALUES = 100
MAX_QUERY_PAGE_ROWS = 200
MAX_QUERY_CURSOR_LENGTH = 4096

_NUMERIC = re.compile(r"^(?:numeric|decimal)\([0-9]+,-?[0-9]+\)$")
_CHARACTER = re.compile(r"^(?:character varying|varchar|character|char)(?:\([0-9]+\))?$")
_TIMESTAMP = re.compile(r"^timestamp(?:\([0-6]\))? (with|without) time zone$")
_TIME = re.compile(r"^time(?:\([0-6]\))? (with|without) time zone$")


class DatasetSnapshotNotFoundError(Exception):
    pass


class DatasetSnapshotNotPublishedError(Exception):
    pass


class DatasetSnapshotUnavailableError(Exception):
    pass


class DatasetSnapshotQueryTimeoutError(Exception):
    pass


class DatasetSnapshotResultTooLargeError(Exception):
    pass


class InvalidSnapshotQueryError(ValueError):
    pass


class InvalidSnapshotCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedSnapshotCursor:
    dataset_id: UUID
    snapshot_id: UUID
    query_digest: str
    position: SnapshotQueryCursorPosition


class SnapshotQueryStore(Protocol):
    async def get_freshness(self, dataset_id: UUID) -> DatasetFreshness | None: ...

    async def get_active_context(
        self,
        dataset_id: UUID,
    ) -> ActiveSnapshotQueryContext | None: ...

    async def record_query(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        actor_id: UUID,
        policy_revision: int,
        selected_field_count: int,
        filter_count: int,
        sort_count: int,
        requested_rows: int,
        returned_rows: int,
        truncated_by_bytes: bool,
        error_code: str | None,
        request_id: str,
        occurred_at: datetime,
    ) -> None: ...


class SnapshotQueryExecutor(Protocol):
    async def query(
        self,
        *,
        context: ActiveSnapshotQueryContext,
        selected_fields: tuple[SnapshotQueryField, ...],
        filters: tuple[SnapshotQueryFilter, ...],
        sorts: tuple[SnapshotQuerySort, ...],
        position: SnapshotQueryCursorPosition | None,
        page_size: int,
        runtime: InteractiveQueryRuntime,
    ) -> SnapshotQueryExecution: ...


class SnapshotCursorCodec(Protocol):
    def encode(
        self,
        *,
        dataset_id: UUID,
        snapshot_id: UUID,
        query_digest: str,
        position: SnapshotQueryCursorPosition,
    ) -> str: ...

    def decode(self, value: str) -> DecodedSnapshotCursor: ...


@dataclass(frozen=True, slots=True)
class PreparedSnapshotQuery:
    selected_fields: tuple[SnapshotQueryField, ...]
    filters: tuple[SnapshotQueryFilter, ...]
    sorts: tuple[SnapshotQuerySort, ...]


def prepare_snapshot_filters(
    context: ActiveSnapshotQueryContext,
    filters: tuple[SnapshotQueryFilter, ...],
) -> tuple[SnapshotQueryFilter, ...]:
    """Resolve field identifiers and normalize typed filter values."""

    if len(filters) > MAX_QUERY_FILTERS:
        raise InvalidSnapshotQueryError("Query contains too many filters")
    fields_by_id = {field.field_id: field for field in context.fields}
    if any(item.field_id not in fields_by_id for item in filters):
        raise InvalidSnapshotQueryError("Query references an unknown field")
    return tuple(_normalize_filter(item, fields_by_id[item.field_id]) for item in filters)


def prepare_snapshot_query(
    context: ActiveSnapshotQueryContext,
    value: SnapshotQuery,
    *,
    require_page: bool = True,
) -> PreparedSnapshotQuery:
    """Validate identifiers and normalize typed values for reads and exports."""

    if not 1 <= len(value.field_ids) <= MAX_QUERY_FIELDS:
        raise InvalidSnapshotQueryError("Select between 1 and 100 fields")
    if len(set(value.field_ids)) != len(value.field_ids):
        raise InvalidSnapshotQueryError("Selected fields must be unique")
    if len(value.filters) > MAX_QUERY_FILTERS or len(value.sorts) > MAX_QUERY_SORTS:
        raise InvalidSnapshotQueryError("Query contains too many filters or sorts")
    if require_page and not 1 <= value.page_size <= MAX_QUERY_PAGE_ROWS:
        raise InvalidSnapshotQueryError("Page size is outside the supported range")
    fields_by_id = {field.field_id: field for field in context.fields}
    try:
        selected_fields = tuple(fields_by_id[field_id] for field_id in value.field_ids)
        if len({item.field_id for item in value.sorts}) != len(value.sorts):
            raise InvalidSnapshotQueryError("Sort fields must be unique")
        for filter_item in value.filters:
            if filter_item.field_id not in fields_by_id:
                raise InvalidSnapshotQueryError("Query references an unknown field")
        for sort_item in value.sorts:
            if sort_item.field_id not in fields_by_id:
                raise InvalidSnapshotQueryError("Query references an unknown field")
    except KeyError as exc:
        raise InvalidSnapshotQueryError("Query references an unknown field") from exc
    filters = prepare_snapshot_filters(context, value.filters)
    return PreparedSnapshotQuery(selected_fields, filters, value.sorts)


def snapshot_type_family(data_type: str) -> str:
    normalized = " ".join(data_type.lower().split())
    if normalized == "boolean":
        return "boolean"
    if normalized in {"smallint", "integer", "bigint"}:
        return "integer"
    if normalized in {"real", "double precision"} or _NUMERIC.fullmatch(normalized):
        return "number"
    if normalized == "date":
        return "date"
    if _TIMESTAMP.fullmatch(normalized):
        return "datetime"
    if match := _TIME.fullmatch(normalized):
        return "time" if match.group(1) == "without" else "text"
    if normalized in {"json", "jsonb"}:
        return "json"
    if normalized == "bytea":
        return "binary"
    if normalized in {
        "text",
        "name",
        "citext",
        "uuid",
        "numeric",
        "decimal",
        "money",
        "interval",
        "inet",
        "cidr",
        "macaddr",
        "macaddr8",
        "xml",
    } or _CHARACTER.fullmatch(normalized):
        return "text"
    raise InvalidSnapshotQueryError("The snapshot contains an unsupported field type")


def supported_filter_operators(
    field: SnapshotQueryField,
) -> tuple[SnapshotFilterOperator, ...]:
    """Return the stable No-Code filter vocabulary accepted for one field."""

    try:
        family = snapshot_type_family(field.data_type)
    except InvalidSnapshotQueryError:
        return ()
    null_operators = (
        SnapshotFilterOperator.IS_NULL,
        SnapshotFilterOperator.IS_NOT_NULL,
    )
    equality_operators = (
        SnapshotFilterOperator.EQUALS,
        SnapshotFilterOperator.NOT_EQUALS,
        SnapshotFilterOperator.IN,
    )
    if family == "binary":
        return null_operators
    if family == "text":
        return (
            *equality_operators,
            SnapshotFilterOperator.CONTAINS,
            SnapshotFilterOperator.STARTS_WITH,
            *null_operators,
        )
    if family in {"integer", "number", "date", "datetime", "time"}:
        return (
            *equality_operators,
            SnapshotFilterOperator.GREATER_THAN,
            SnapshotFilterOperator.GREATER_THAN_OR_EQUAL,
            SnapshotFilterOperator.LESS_THAN,
            SnapshotFilterOperator.LESS_THAN_OR_EQUAL,
            SnapshotFilterOperator.BETWEEN,
            *null_operators,
        )
    return (*equality_operators, *null_operators)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed


def _normalize_value(field: SnapshotQueryField, value: Any) -> Any:
    family = snapshot_type_family(field.data_type)
    try:
        if family == "boolean":
            if not isinstance(value, bool):
                raise ValueError
            return value
        if family == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError
            return value
        if family == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError
            parsed = Decimal(str(value))
            if not parsed.is_finite():
                raise ValueError
            return parsed
        if family == "date":
            if not isinstance(value, str):
                raise ValueError
            return date.fromisoformat(value)
        if family == "datetime":
            if not isinstance(value, str):
                raise ValueError
            return _parse_datetime(value)
        if family == "time":
            if not isinstance(value, str):
                raise ValueError
            return time.fromisoformat(value)
        if family == "json":
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        if family == "text":
            if not isinstance(value, str):
                raise ValueError
            normalized = " ".join(field.data_type.lower().split())
            if normalized == "uuid":
                return str(UUID(value))
            return value
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidSnapshotQueryError(
            f"Filter value is invalid for field {field.field_id}"
        ) from exc
    raise InvalidSnapshotQueryError(f"Field {field.field_id} does not support values")


def _normalize_filter(
    item: SnapshotQueryFilter,
    field: SnapshotQueryField,
) -> SnapshotQueryFilter:
    operator = item.operator
    if operator not in supported_filter_operators(field):
        raise InvalidSnapshotQueryError("Filter operator is invalid for this field")
    if operator in {
        SnapshotFilterOperator.IS_NULL,
        SnapshotFilterOperator.IS_NOT_NULL,
    }:
        if item.value is not None or item.values:
            raise InvalidSnapshotQueryError("Null filters do not accept values")
        return item
    if operator in {SnapshotFilterOperator.IN, SnapshotFilterOperator.BETWEEN}:
        expected = 2 if operator is SnapshotFilterOperator.BETWEEN else None
        if not item.values or (expected is not None and len(item.values) != expected):
            raise InvalidSnapshotQueryError("Filter has an invalid number of values")
        if len(item.values) > MAX_QUERY_IN_VALUES:
            raise InvalidSnapshotQueryError("Filter contains too many values")
        if item.value is not None:
            raise InvalidSnapshotQueryError("Filter must not mix value and values")
        values = tuple(_normalize_value(field, value) for value in item.values)
        return SnapshotQueryFilter(item.field_id, operator, values=values)

    if item.value is None or item.values:
        raise InvalidSnapshotQueryError("Filter requires exactly one non-null value")
    return SnapshotQueryFilter(
        item.field_id,
        operator,
        value=_normalize_value(field, item.value),
    )


def _shape_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidSnapshotQueryError("Non-finite values are not supported")
    return value


def query_digest(
    field_ids: tuple[UUID, ...],
    filters: tuple[SnapshotQueryFilter, ...],
    sorts: tuple[SnapshotQuerySort, ...],
) -> str:
    document = {
        "fields": [str(value) for value in field_ids],
        "filters": [
            {
                "field_id": str(item.field_id),
                "operator": item.operator.value,
                "value": _shape_value(item.value),
                "values": [_shape_value(value) for value in item.values],
            }
            for item in filters
        ],
        "sorts": [
            {"field_id": str(item.field_id), "direction": item.direction.value} for item in sorts
        ],
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class DatasetSnapshotQueryService:
    def __init__(
        self,
        store: SnapshotQueryStore,
        executor: SnapshotQueryExecutor,
        cursor_codec: SnapshotCursorCodec,
        authorizer: DatasetAccessAuthorizer,
        policy_store: QuerySafetyPolicyReader,
    ) -> None:
        self._store = store
        self._executor = executor
        self._cursor_codec = cursor_codec
        self._authorizer = authorizer
        self._policy_store = policy_store

    async def freshness(self, *, actor: User, dataset_id: UUID) -> DatasetFreshness:
        try:
            await self._authorizer.require(
                actor=actor,
                dataset_id=dataset_id,
                permissions=frozenset({DatasetPermission.VIEW_ROWS}),
            )
        except DatasetAccessDeniedError as exc:
            raise DatasetSnapshotNotFoundError from exc
        freshness = await self._store.get_freshness(dataset_id)
        if freshness is None:
            raise DatasetSnapshotNotFoundError
        return freshness

    async def exploration(self, *, actor: User, dataset_id: UUID) -> DatasetExploration:
        try:
            await self._authorizer.require(
                actor=actor,
                dataset_id=dataset_id,
                permissions=frozenset({DatasetPermission.VIEW_ROWS}),
            )
        except DatasetAccessDeniedError as exc:
            raise DatasetSnapshotNotFoundError from exc
        context = await self._store.get_active_context(dataset_id)
        if context is None:
            raise DatasetSnapshotNotFoundError
        columns = tuple(
            DatasetExplorationColumn(
                field_id=field.field_id,
                name=field.name,
                label=field.label,
                data_type=field.data_type,
                nullable=field.nullable,
                visible=field.visible,
                display_format=field.display_format,
                filter_operators=supported_filter_operators(field),
                sortable=True,
            )
            for field in context.fields
        )
        return DatasetExploration(
            dataset_id=context.dataset_id,
            snapshot_id=context.snapshot_id,
            snapshot_row_count=context.snapshot_row_count,
            snapshot_activated_at=context.activated_at,
            refresh_state=context.refresh_state,
            last_refresh_error=context.last_refresh_error,
            last_refresh_error_at=context.last_refresh_error_at,
            is_stale=context.is_stale,
            columns=columns,
            default_field_ids=tuple(field.field_id for field in context.fields if field.visible)[
                :MAX_QUERY_FIELDS
            ],
        )

    async def query(
        self,
        *,
        actor: User,
        dataset_id: UUID,
        value: SnapshotQuery,
        request_id: str,
    ) -> SnapshotQueryPage:
        try:
            await self._authorizer.require(
                actor=actor,
                dataset_id=dataset_id,
                permissions=frozenset({DatasetPermission.VIEW_ROWS}),
            )
        except DatasetAccessDeniedError as exc:
            raise DatasetSnapshotNotFoundError from exc
        if value.cursor is not None and len(value.cursor) > MAX_QUERY_CURSOR_LENGTH:
            raise InvalidSnapshotCursorError("Cursor is too long")

        context = await self._store.get_active_context(dataset_id)
        if context is None:
            raise DatasetSnapshotNotFoundError
        prepared = prepare_snapshot_query(context, value)
        selected_fields = prepared.selected_fields
        filters = prepared.filters
        digest = query_digest(value.field_ids, filters, value.sorts)
        position = None
        if value.cursor is not None:
            try:
                decoded = self._cursor_codec.decode(value.cursor)
            except (TypeError, ValueError) as exc:
                raise InvalidSnapshotCursorError("Cursor is invalid") from exc
            if (
                decoded.dataset_id != dataset_id
                or decoded.snapshot_id != context.snapshot_id
                or decoded.query_digest != digest
                or len(decoded.position.sort_values) != len(value.sorts)
            ):
                raise InvalidSnapshotCursorError("Cursor does not match this active query")
            position = decoded.position

        managed_policy = await self._policy_store.get()
        runtime = InteractiveQueryRuntime.from_policy(managed_policy.policy)

        try:
            execution = await self._executor.query(
                context=context,
                selected_fields=selected_fields,
                filters=filters,
                sorts=value.sorts,
                position=position,
                page_size=value.page_size,
                runtime=runtime,
            )
        except (
            DatasetSnapshotQueryTimeoutError,
            DatasetSnapshotResultTooLargeError,
            DatasetSnapshotUnavailableError,
        ) as exc:
            await self._store.record_query(
                context=context,
                actor_id=actor.id,
                policy_revision=managed_policy.revision,
                selected_field_count=len(selected_fields),
                filter_count=len(filters),
                sort_count=len(value.sorts),
                requested_rows=value.page_size,
                returned_rows=0,
                truncated_by_bytes=False,
                error_code=(
                    "snapshot_query_timeout"
                    if isinstance(exc, DatasetSnapshotQueryTimeoutError)
                    else "snapshot_result_too_large"
                    if isinstance(exc, DatasetSnapshotResultTooLargeError)
                    else "snapshot_query_unavailable"
                ),
                request_id=request_id,
                occurred_at=datetime.now(UTC),
            )
            raise

        next_cursor = (
            self._cursor_codec.encode(
                dataset_id=dataset_id,
                snapshot_id=context.snapshot_id,
                query_digest=digest,
                position=execution.next_position,
            )
            if execution.next_position is not None
            else None
        )
        await self._store.record_query(
            context=context,
            actor_id=actor.id,
            policy_revision=managed_policy.revision,
            selected_field_count=len(selected_fields),
            filter_count=len(filters),
            sort_count=len(value.sorts),
            requested_rows=value.page_size,
            returned_rows=len(execution.rows),
            truncated_by_bytes=execution.truncated_by_bytes,
            error_code=None,
            request_id=request_id,
            occurred_at=datetime.now(UTC),
        )
        return SnapshotQueryPage(
            policy_revision=managed_policy.revision,
            columns=selected_fields,
            rows=execution.rows,
            snapshot_id=context.snapshot_id,
            snapshot_row_count=context.snapshot_row_count,
            snapshot_activated_at=context.activated_at,
            refresh_state=context.refresh_state,
            last_refresh_error=context.last_refresh_error,
            is_stale=context.is_stale,
            next_cursor=next_cursor,
            truncated_by_bytes=execution.truncated_by_bytes,
            last_refresh_error_at=context.last_refresh_error_at,
        )
