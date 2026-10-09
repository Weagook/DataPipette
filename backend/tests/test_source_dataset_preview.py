"""JSON representation rules for source preview values."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from datepipette.infrastructure.dataset_preview import json_safe_preview_value


def test_preview_value_normalization_preserves_precision_and_structure() -> None:
    value = {
        "decimal": Decimal("1234567890.123456789"),
        "timestamp": datetime(2026, 10, 9, 8, 30, tzinfo=UTC),
        "date": date(2026, 10, 9),
        "time": time(8, 30),
        "interval": timedelta(days=2, seconds=5),
        "uuid": UUID("00000000-0000-0000-0000-000000000001"),
        "bytes": b"\x00\xff",
        "array": [Decimal("1.20"), None, True],
    }

    assert json_safe_preview_value(value) == {
        "decimal": "1234567890.123456789",
        "timestamp": "2026-10-09T08:30:00+00:00",
        "date": "2026-10-09",
        "time": "08:30:00",
        "interval": "2 days, 0:00:05",
        "uuid": "00000000-0000-0000-0000-000000000001",
        "bytes": "AP8=",
        "array": ["1.20", None, True],
    }


def test_preview_value_normalization_handles_non_finite_and_unknown_values() -> None:
    class CustomValue:
        def __str__(self) -> str:
            return "custom"

    assert json_safe_preview_value(float("inf")) == "inf"
    assert json_safe_preview_value(float("nan")) == "nan"
    assert json_safe_preview_value(CustomValue()) == "custom"
