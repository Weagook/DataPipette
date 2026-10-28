"""Connection-secret key verification against metadata PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text

from datapipette.backup import verify_connection_secrets
from datapipette.config import Settings
from datapipette.infrastructure.crypto import AesGcmSecretCipher
from datapipette.infrastructure.database import create_database_engine, create_session_factory
from datapipette.infrastructure.models import PostgresConnectionModel, UserModel

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
SETTINGS = Settings(environment="test", connection_encryption_key=KEY)


async def _seed() -> UUID:
    engine = create_database_engine(SETTINGS)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    user_id = uuid4()
    current = AesGcmSecretCipher(KEY)
    previous = AesGcmSecretCipher(OTHER_KEY)
    foreign_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))
        async with factory() as session, session.begin():
            session.add(
                UserModel(
                    id=user_id,
                    email="backup@example.com",
                    display_name="Backup",
                    password_hash="unused",
                    role="admin",
                    status="active",
                    last_login_at=None,
                    created_at=now,
                    updated_at=now,
                    account_type="human",
                )
            )
            await session.flush()
            for name, connection_id, cipher, with_ca in (
                ("Plain", uuid4(), current, False),
                ("Verified", uuid4(), current, True),
                ("Foreign", foreign_id, previous, False),
            ):
                session.add(
                    PostgresConnectionModel(
                        id=connection_id,
                        name=name,
                        host="source.invalid",
                        port=5432,
                        database_name="analytics",
                        username="readonly",
                        encrypted_password=cipher.encrypt(
                            "source-password", connection_id=connection_id, field="password"
                        ),
                        encrypted_ca_certificate=(
                            cipher.encrypt(
                                "-----BEGIN CERTIFICATE-----",
                                connection_id=connection_id,
                                field="ca_certificate",
                            )
                            if with_ca
                            else None
                        ),
                        ssl_mode="verify-full" if with_ca else "disable",
                        created_by=user_id,
                        last_tested_at=None,
                        last_test_succeeded=None,
                        last_test_error_code=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
    finally:
        await engine.dispose()
    return foreign_id


def test_key_verification_decrypts_every_secret_and_names_failures() -> None:
    foreign_id = asyncio.run(_seed())

    result = asyncio.run(verify_connection_secrets(SETTINGS))

    assert result.connections == 3
    assert result.secrets == 3
    assert result.failed_connection_ids == (foreign_id,)
