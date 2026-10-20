"""Encrypted short-lived download capabilities for private exports."""

import base64
import binascii
import hashlib
import json
import os
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AAD = b"datapipette:export-download:v1"


class EncryptedExportDownloadTokenCodec:
    def __init__(self, installation_key: str) -> None:
        key = hashlib.sha256(
            b"datapipette:export-download:key:v1\x00" + installation_key.encode()
        ).digest()
        self._cipher = AESGCM(key)

    def encode(self, *, export_id: UUID, actor_id: UUID, expires_at: datetime) -> str:
        plaintext = json.dumps(
            {
                "version": 1,
                "export_id": str(export_id),
                "actor_id": str(actor_id),
                "expires_at": expires_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        nonce = os.urandom(12)
        return (
            base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, plaintext, _AAD))
            .decode()
            .rstrip("=")
        )

    def decode(self, value: str) -> tuple[UUID, UUID, datetime]:
        try:
            payload = base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
            if len(payload) < 29:
                raise ValueError
            document = json.loads(self._cipher.decrypt(payload[:12], payload[12:], _AAD))
            if document["version"] != 1:
                raise ValueError
            expiry = datetime.fromisoformat(document["expires_at"])
            if expiry.tzinfo is None:
                raise ValueError
            return UUID(document["export_id"]), UUID(document["actor_id"]), expiry
        except (
            binascii.Error,
            InvalidTag,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("Invalid export download token") from exc
