"""Authenticated encryption and production-key configuration tests."""

import asyncio
import base64
import os
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from datapipette.application.connections import ConnectionSecretUnavailableError
from datapipette.config import DEVELOPMENT_CONNECTION_ENCRYPTION_KEY, Settings
from datapipette.infrastructure.crypto import AesGcmSecretCipher, InvalidEncryptionKeyError
from datapipette.infrastructure.source_postgres import _temporary_ca


def test_aes_gcm_round_trip_is_randomized_and_bound_to_owner() -> None:
    connection_id = uuid4()
    cipher = AesGcmSecretCipher(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY)
    first = cipher.encrypt("source-password", connection_id=connection_id, field="password")
    second = cipher.encrypt("source-password", connection_id=connection_id, field="password")

    assert first.startswith("v2.")
    assert first != second
    assert "source-password" not in first
    assert cipher.decrypt(first, connection_id=connection_id, field="password") == (
        "source-password"
    )

    with pytest.raises(ConnectionSecretUnavailableError):
        cipher.decrypt(first, connection_id=uuid4(), field="password")
    with pytest.raises(ConnectionSecretUnavailableError):
        cipher.decrypt(first, connection_id=connection_id, field="ca_certificate")
    tampered = first[:4] + ("A" if first[4] != "A" else "B") + first[5:]
    with pytest.raises(ConnectionSecretUnavailableError):
        cipher.decrypt(tampered, connection_id=connection_id, field="password")


def test_aes_gcm_decrypts_legacy_brand_envelopes() -> None:
    connection_id = uuid4()
    key = base64.urlsafe_b64decode(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY + "=")
    nonce = os.urandom(12)
    associated_data = f"datepipette:postgresql-connection:{connection_id}:password:v1".encode()
    encrypted = AESGCM(key).encrypt(nonce, b"legacy-password", associated_data)
    envelope = "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode().rstrip("=")

    assert (
        AesGcmSecretCipher(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY).decrypt(
            envelope,
            connection_id=connection_id,
            field="password",
        )
        == "legacy-password"
    )


def test_invalid_and_development_production_keys_are_rejected() -> None:
    with pytest.raises(InvalidEncryptionKeyError):
        AesGcmSecretCipher("not-base64!!!")
    with pytest.raises(InvalidEncryptionKeyError):
        AesGcmSecretCipher("YQ")
    with pytest.raises(ValidationError, match="explicit connection encryption key"):
        Settings(environment="production")


def test_custom_ca_exists_only_inside_restricted_context() -> None:
    async def exercise() -> str:
        async with _temporary_ca("private-ca-material") as path:
            assert path is not None
            assert oct(os.stat(path).st_mode & 0o777) == "0o600"
            with open(path, encoding="utf-8") as temporary_file:
                assert temporary_file.read() == "private-ca-material"
            return path

    temporary_path = asyncio.run(exercise())
    assert not os.path.exists(temporary_path)
