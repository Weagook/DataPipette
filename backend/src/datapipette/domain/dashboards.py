"""Private dashboard layout vocabulary."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from datapipette.domain.access_control import GrantPrincipalType

MAX_DASHBOARD_ITEMS = 50
DASHBOARD_GRID_COLUMNS = 12
MAX_DASHBOARD_ROW = 10_000
MAX_DASHBOARD_ITEM_HEIGHT = 100
MAX_DASHBOARD_FILTERS = 5
MAX_DASHBOARD_FILTER_LABEL = 60
MAX_DASHBOARD_FILTER_VALUES = 100


class DashboardFilterKind(StrEnum):
    VALUES = "values"
    DATE_RANGE = "date_range"


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
class DashboardFilterTarget:
    item_id: UUID
    field_id: UUID


@dataclass(frozen=True, slots=True)
class DashboardFilter:
    """A viewer-controlled restriction mapped to one field of selected dashboard items."""

    id: UUID
    label: str
    kind: DashboardFilterKind
    targets: tuple[DashboardFilterTarget, ...]

    def __post_init__(self) -> None:
        label = " ".join(self.label.split())
        if not label or len(label) > MAX_DASHBOARD_FILTER_LABEL:
            raise ValueError(
                f"Dashboard filter labels need 1 to {MAX_DASHBOARD_FILTER_LABEL} characters"
            )
        object.__setattr__(self, "label", label)
        if not self.targets:
            raise ValueError("Dashboard filters must apply to at least one item")
        item_ids = [target.item_id for target in self.targets]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("A dashboard filter may target each item only once")


@dataclass(frozen=True, slots=True)
class DashboardFilterSelection:
    """Values chosen for one dashboard filter when its items are queried."""

    filter_id: UUID
    values: tuple[str | int | bool, ...] = ()
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self) -> None:
        if len(self.values) > MAX_DASHBOARD_FILTER_VALUES:
            raise ValueError(
                f"A dashboard filter accepts at most {MAX_DASHBOARD_FILTER_VALUES} values"
            )
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("The filter start date must not be after its end date")

    @property
    def is_empty(self) -> bool:
        return not self.values and self.date_from is None and self.date_to is None


@dataclass(frozen=True, slots=True)
class DashboardDefinition:
    items: tuple[DashboardItem, ...] = ()
    schema_version: int = 1
    filters: tuple[DashboardFilter, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version not in {1, 2}
        ):
            raise ValueError("Unsupported dashboard definition schema version")
        if self.filters and self.schema_version < 2:
            raise ValueError("Dashboard filters require definition schema version 2")
        if len(self.filters) > MAX_DASHBOARD_FILTERS:
            raise ValueError(f"Dashboard supports at most {MAX_DASHBOARD_FILTERS} filters")
        filter_ids = [item.id for item in self.filters]
        if len(filter_ids) != len(set(filter_ids)):
            raise ValueError("Dashboard filter identifiers must be unique")
        known_items = {item.id for item in self.items}
        if any(
            target.item_id not in known_items for item in self.filters for target in item.targets
        ):
            raise ValueError("Dashboard filters must target items in the layout")
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

    def filter(self, filter_id: UUID) -> DashboardFilter | None:
        return next((item for item in self.filters if item.id == filter_id), None)

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
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
        if self.schema_version >= 2:
            document["filters"] = [
                {
                    "id": str(item.id),
                    "label": item.label,
                    "kind": item.kind.value,
                    "targets": [
                        {"item_id": str(target.item_id), "field_id": str(target.field_id)}
                        for target in item.targets
                    ],
                }
                for item in self.filters
            ]
        return document

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
                filters=tuple(
                    DashboardFilter(
                        id=UUID(item["id"]),
                        label=item["label"],
                        kind=DashboardFilterKind(item["kind"]),
                        targets=tuple(
                            DashboardFilterTarget(
                                item_id=UUID(target["item_id"]),
                                field_id=UUID(target["field_id"]),
                            )
                            for target in item["targets"]
                        ),
                    )
                    for item in document.get("filters", [])
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
