"""Focused columnar conversion tests for the refresh hot path."""

import json
from uuid import UUID, uuid4

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from datepipette.infrastructure.parquet_snapshots import (
    _adapter,
    _record_batch_from_rows,
)


def _batch(
    rows: list[tuple[object, ...]],
    specs: list[tuple[str, bool]],
) -> pa.RecordBatch:
    adapters = tuple(
        _adapter(uuid4(), f"field_{index}", data_type, nullable)
        for index, (data_type, nullable) in enumerate(specs)
    )
    schema = pa.schema(
        [
            pa.field(adapter.storage_name, adapter.arrow_type, nullable=adapter.nullable)
            for adapter in adapters
        ]
    )
    return _record_batch_from_rows(rows, schema=schema, adapters=adapters)


def test_native_values_build_a_columnar_record_batch() -> None:
    batch = _batch(
        [(1, "alpha", True), (2, "beta", False)],
        [("bigint", False), ("text", False), ("boolean", False)],
    )

    assert batch.to_pydict() == {
        batch.schema.names[0]: [1, 2],
        batch.schema.names[1]: ["alpha", "beta"],
        batch.schema.names[2]: [True, False],
    }


def test_converted_and_nullable_values_preserve_storage_contract() -> None:
    identifier = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    batch = _batch(
        [(identifier, {"tier": "gold"}), (None, None)],
        [("uuid", True), ("jsonb", True)],
    )

    values = batch.to_pydict()
    assert values[batch.schema.names[0]] == [str(identifier), None]
    assert json.loads(values[batch.schema.names[1]][0]) == {"tier": "gold"}
    assert values[batch.schema.names[1]][1] is None


def test_non_nullable_null_is_rejected_after_arrow_conversion() -> None:
    with pytest.raises(ValueError, match="Non-nullable source field returned null"):
        _batch([(1,), (None,)], [("bigint", False)])


def test_changed_source_column_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="Source refresh column count changed"):
        _batch([(1, "extra")], [("bigint", False)])
