"""Application contract for dependency readiness."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Safe availability state for one runtime dependency."""

    available: bool


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    """Aggregated dependency readiness state."""

    checks: dict[str, DependencyStatus]

    @property
    def ready(self) -> bool:
        return all(check.available for check in self.checks.values())


class ReadinessProbe(Protocol):
    """Port implemented by runtime infrastructure health checks."""

    async def check(self) -> ReadinessStatus:
        """Return safe dependency availability without raising."""
        ...
