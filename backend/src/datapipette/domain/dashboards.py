"""Private dashboard layout vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from datapipette.domain.access_control import GrantPrincipalType

MAX_DASHBOARD_ITEMS = 50
DASHBOARD_GRID_COLUMNS = 12
MAX_DASHBOARD_ROW = 10_000
MAX_DASHBOARD_ITEM_HEIGHT = 100


@dataclass(frozen=True, slots=True)
class DashboardItem:
    id: UUID
    chart_id: UUID
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("Dashboard item geometry must use integers")
        if not 0 <= self.x < DASHBOARD_GRID_COLUMNS:
            raise ValueError("Dashboard item x position is outside the grid")
        if not 0 <= self.y < MAX_DASHBOARD_ROW:
            raise ValueError("Dashboard item y position is outside the grid")
        if not 1 <= self.width <= DASHBOARD_GRID_COLUMNS:
            raise ValueError("Dashboard item width is outside the supported range")
        if self.x + self.width > DASHBOARD_GRID_COLUMNS:
            raise ValueError("Dashboard item extends beyond the grid width")
        if not 1 <= self.height <= MAX_DASHBOARD_ITEM_HEIGHT:
            raise ValueError("Dashboard item height is outside the supported range")


def _overlap(left: DashboardItem, right: DashboardItem) -> bool:
    return (
        left.x < right.x + right.width
        and right.x < left.x + left.width
        and left.y < right.y + right.height
        and right.y < left.y + left.height
    )


@dataclass(frozen=True, slots=True)
class DashboardDefinition:
    items: tuple[DashboardItem, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != 1
        ):
            raise ValueError("Unsupported dashboard definition schema version")
        if len(self.items) > MAX_DASHBOARD_ITEMS:
            raise ValueError(f"Dashboard supports at most {MAX_DASHBOARD_ITEMS} items")
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Dashboard item identifiers must be unique")
        chart_ids = [item.chart_id for item in self.items]
        if len(chart_ids) != len(set(chart_ids)):
            raise ValueError("Dashboard chart references must be unique")
        for index, item in enumerate(self.items):
            if any(_overlap(item, other) for other in self.items[index + 1 :]):
                raise ValueError("Dashboard items must not overlap")

    @property
    def chart_ids(self) -> frozenset[UUID]:
        return frozenset(item.chart_id for item in self.items)

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "items": [
                {
                    "id": str(item.id),
                    "chart_id": str(item.chart_id),
                    "x": item.x,
                    "y": item.y,
                    "width": item.width,
                    "height": item.height,
                }
                for item in self.items
            ],
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "DashboardDefinition":
        try:
            return cls(
                schema_version=document["schema_version"],
                items=tuple(
                    DashboardItem(
                        id=UUID(item["id"]),
                        chart_id=UUID(item["chart_id"]),
                        x=item["x"],
                        y=item["y"],
                        width=item["width"],
                        height=item["height"],
                    )
                    for item in document["items"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored dashboard definition is invalid") from exc


@dataclass(frozen=True, slots=True)
class Dashboard:
    id: UUID
    owner_id: UUID
    name: str
    revision: int
    definition: DashboardDefinition
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardGrant:
    id: UUID
    dashboard_id: UUID
    principal_type: GrantPrincipalType
    principal_id: UUID
    created_by: UUID
    created_at: datetime
