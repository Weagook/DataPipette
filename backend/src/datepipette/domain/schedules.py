"""Dataset refresh schedule vocabulary and timezone-aware occurrence calculation."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo


class RefreshScheduleKind(StrEnum):
    INTERVAL = "interval"
    DAILY = "daily"


@dataclass(frozen=True, slots=True)
class RefreshScheduleSpec:
    kind: RefreshScheduleKind
    interval_minutes: int | None = None
    daily_time: time | None = None

    def __post_init__(self) -> None:
        if self.daily_time is not None and (
            self.daily_time.tzinfo is not None
            or self.daily_time.second != 0
            or self.daily_time.microsecond != 0
        ):
            raise ValueError("Daily time must be a timezone-naive minute")
        if self.kind is RefreshScheduleKind.INTERVAL:
            if self.interval_minutes is None or not 5 <= self.interval_minutes <= 10_080:
                raise ValueError("Interval must be between 5 and 10,080 minutes")
            if self.daily_time is not None:
                raise ValueError("Interval schedules cannot define a daily time")
        elif self.interval_minutes is not None or self.daily_time is None:
            raise ValueError("Daily schedules require only a daily time")


@dataclass(frozen=True, slots=True)
class DatasetRefreshSchedule:
    id: UUID
    dataset_id: UUID
    spec: RefreshScheduleSpec
    enabled: bool
    timezone: str
    next_run_at: datetime | None
    last_triggered_at: datetime | None
    revision: int
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


def next_occurrence(
    spec: RefreshScheduleSpec,
    *,
    after: datetime,
    timezone: ZoneInfo,
) -> datetime:
    if after.tzinfo is None:
        raise ValueError("Occurrence calculation requires a timezone-aware timestamp")
    after_utc = after.astimezone(UTC)
    if spec.kind is RefreshScheduleKind.INTERVAL:
        assert spec.interval_minutes is not None
        return after_utc + timedelta(minutes=spec.interval_minutes)

    assert spec.daily_time is not None
    local_after = after_utc.astimezone(timezone)
    candidate_date = local_after.date()
    candidate = datetime.combine(candidate_date, spec.daily_time, timezone)
    candidate_utc = candidate.astimezone(UTC)
    if candidate_utc <= after_utc:
        candidate = datetime.combine(
            candidate_date + timedelta(days=1),
            spec.daily_time,
            timezone,
        )
        candidate_utc = candidate.astimezone(UTC)
    return candidate_utc
