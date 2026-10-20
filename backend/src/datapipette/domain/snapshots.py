"""Immutable analytical snapshot vocabulary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class SnapshotState(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class SnapshotArtifact:
    id: UUID
    storage_backend: str
    object_key: str
    row_count: int
    byte_size: int
    checksum_sha256: str
    schema_document: dict[str, Any]
