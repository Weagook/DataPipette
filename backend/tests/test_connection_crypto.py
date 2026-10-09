"""Authenticated encryption and production-key configuration tests."""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError

from datepipette.application.connections import ConnectionSecretUnavailableError
from datepipette.config import DEVELOPMENT_CONNECTION_ENCRYPTION_KEY, Settings
from datepipette.infrastructure.crypto import AesGcmSecretCipher, InvalidEncryptionKeyError
from datepipette.infrastructure.source_postgres import _temporary_ca


def test_aes_gcm_round_trip_is_randomized_and_bound_to_owner() -> None:
    connection_id = uuid4()
    cipher = AesGcmSecretCipher(DEVELOPMENT_CONNECTION_ENCRYPTION_KEY)
    first = cipher.encrypt("source-password", connection_id=connection_id, field="password")
    second = cipher.encrypt("source-password", connection_id=connection_id, field="password")

    assert first.startswith("v1.")
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
