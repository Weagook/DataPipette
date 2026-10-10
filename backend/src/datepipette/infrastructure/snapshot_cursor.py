"""Authenticated opaque cursors for snapshot keyset pagination."""

import base64
import binascii
import hashlib
import json
import os
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from datepipette.application.snapshot_queries import DecodedSnapshotCursor
from datepipette.domain.snapshot_queries import SnapshotQueryCursorPosition

_AAD = b"datepipette:snapshot-query-cursor:v1"


def _value_document(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"type": "scalar", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "value": base64.b64encode(value).decode("ascii"),
        }
    raise TypeError("Unsupported cursor value")


def _document_value(document: Any) -> Any:
    if not isinstance(document, dict):
        raise ValueError("Invalid cursor value")
    kind = document.get("type")
    value = document.get("value")
    if kind == "scalar" and (value is None or isinstance(value, (bool, int, float, str))):
        return value
    if kind == "decimal" and isinstance(value, str):
        return Decimal(value)
    if kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    if kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if kind == "time" and isinstance(value, str):
        return time.fromisoformat(value)
    if kind == "bytes" and isinstance(value, str):
        return base64.b64decode(value, validate=True)
    raise ValueError("Invalid cursor value")


class EncryptedSnapshotCursorCodec:
    def __init__(self, installation_key: str) -> None:
        key = hashlib.sha256(
            b"datepipette:snapshot-query-cursor:key:v1\x00" + installation_key.encode()
        ).digest()
        self._cipher = AESGCM(key)

    def encode(
        self,
        *,
        dataset_id: UUID,
        snapshot_id: UUID,
        query_digest: str,
        position: SnapshotQueryCursorPosition,
    ) -> str:
        document = {
            "version": 1,
            "dataset_id": str(dataset_id),
            "snapshot_id": str(snapshot_id),
            "query_digest": query_digest,
            "sort_values": [_value_document(value) for value in position.sort_values],
            "file_row_number": position.file_row_number,
        }
        plaintext = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(nonce, plaintext, _AAD)
        return base64.urlsafe_b64encode(nonce + encrypted).decode().rstrip("=")

    def decode(self, value: str) -> DecodedSnapshotCursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = base64.b64decode(padded, altchars=b"-_", validate=True)
            if len(payload) < 29:
                raise ValueError
            plaintext = self._cipher.decrypt(payload[:12], payload[12:], _AAD)
            document = json.loads(plaintext)
            if document["version"] != 1:
                raise ValueError
            digest = document["query_digest"]
            row_number = document["file_row_number"]
            sort_values = document["sort_values"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or not isinstance(row_number, int)
                or row_number < 0
                or not isinstance(sort_values, list)
            ):
                raise ValueError
            return DecodedSnapshotCursor(
                dataset_id=UUID(document["dataset_id"]),
                snapshot_id=UUID(document["snapshot_id"]),
                query_digest=digest,
                position=SnapshotQueryCursorPosition(
                    sort_values=tuple(_document_value(item) for item in sort_values),
                    file_row_number=row_number,
                ),
            )
        except (
            binascii.Error,
            DecimalException,
            InvalidTag,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("Invalid snapshot cursor") from exc
