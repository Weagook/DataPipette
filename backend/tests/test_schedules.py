"""Pure refresh schedule cadence tests."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from datapipette.domain.schedules import (
    RefreshScheduleKind,
    RefreshScheduleSpec,
    next_occurrence,
)


def test_interval_and_daily_occurrences_use_utc_and_installation_timezone() -> None:
    interval = RefreshScheduleSpec(RefreshScheduleKind.INTERVAL, interval_minutes=15)
    assert next_occurrence(
        interval,
        after=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        timezone=ZoneInfo("Asia/Almaty"),
    ) == datetime(2026, 1, 1, 0, 15, tzinfo=UTC)

    daily = RefreshScheduleSpec(RefreshScheduleKind.DAILY, daily_time=time(9, 30))
    assert next_occurrence(
        daily,
        after=datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        timezone=ZoneInfo("Asia/Almaty"),
    ) == datetime(2026, 1, 1, 4, 30, tzinfo=UTC)
    assert next_occurrence(
        daily,
        after=datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        timezone=ZoneInfo("Asia/Almaty"),
    ) == datetime(2026, 1, 2, 4, 30, tzinfo=UTC)


def test_daily_occurrence_has_deterministic_dst_gap_and_fold_behavior() -> None:
    timezone = ZoneInfo("America/New_York")
    daily = RefreshScheduleSpec(RefreshScheduleKind.DAILY, daily_time=time(2, 30))
    assert next_occurrence(
        daily,
        after=datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
        timezone=timezone,
    ) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)

    ambiguous = RefreshScheduleSpec(RefreshScheduleKind.DAILY, daily_time=time(1, 30))
    assert next_occurrence(
        ambiguous,
        after=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        timezone=timezone,
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "spec",
    [
        lambda: RefreshScheduleSpec(RefreshScheduleKind.INTERVAL, interval_minutes=4),
        lambda: RefreshScheduleSpec(
            RefreshScheduleKind.INTERVAL,
            interval_minutes=15,
            daily_time=time(9),
        ),
        lambda: RefreshScheduleSpec(RefreshScheduleKind.DAILY),
        lambda: RefreshScheduleSpec(
            RefreshScheduleKind.DAILY,
            interval_minutes=15,
            daily_time=time(9),
        ),
        lambda: RefreshScheduleSpec(
            RefreshScheduleKind.DAILY,
            daily_time=time(9, 0, 1),
        ),
    ],
)
def test_schedule_spec_rejects_inconsistent_cadence(spec: object) -> None:
    with pytest.raises(ValueError):
        spec()  # type: ignore[operator]
