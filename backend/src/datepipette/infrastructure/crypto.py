"""Versioned authenticated encryption for metadata secrets."""

import base64
import binascii
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from datepipette.application.connections import ConnectionSecretUnavailableError


class InvalidEncryptionKeyError(ValueError):
    pass


class AesGcmSecretCipher:
    """Encrypt connection fields with a versioned AES-256-GCM envelope."""

    _VERSION = "v1"
    _NONCE_SIZE = 12

    def __init__(self, encoded_key: str) -> None:
        try:
            padding = "=" * (-len(encoded_key) % 4)
            key = base64.b64decode(
                encoded_key + padding,
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise InvalidEncryptionKeyError(
                "Connection encryption key must be URL-safe base64"
            ) from exc
        if len(key) != 32:
            raise InvalidEncryptionKeyError(
                "Connection encryption key must decode to exactly 32 bytes"
            )
        self._cipher = AESGCM(key)

    @classmethod
    def _associated_data(cls, connection_id: UUID, field: str) -> bytes:
        return f"datepipette:postgresql-connection:{connection_id}:{field}:{cls._VERSION}".encode()

    def encrypt(self, plaintext: str, *, connection_id: UUID, field: str) -> str:
        nonce = os.urandom(self._NONCE_SIZE)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext.encode(),
            self._associated_data(connection_id, field),
        )
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode().rstrip("=")
        return f"{self._VERSION}.{payload}"

    def decrypt(self, envelope: str, *, connection_id: UUID, field: str) -> str:
        try:
            version, payload = envelope.split(".", maxsplit=1)
            if version != self._VERSION:
                raise ValueError
            padding = "=" * (-len(payload) % 4)
            decoded = base64.b64decode(
                payload + padding,
                altchars=b"-_",
                validate=True,
            )
            if len(decoded) <= self._NONCE_SIZE:
                raise ValueError
            plaintext = self._cipher.decrypt(
                decoded[: self._NONCE_SIZE],
                decoded[self._NONCE_SIZE :],
                self._associated_data(connection_id, field),
            )
            return plaintext.decode()
        except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise ConnectionSecretUnavailableError(
                "Stored connection secret cannot be decrypted"
            ) from exc
