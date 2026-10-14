"""Service-account application policy and token-authentication tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from datepipette.application.service_accounts import (
    ApiTokenAuthenticationService,
    ApiTokenNotFoundError,
    InvalidApiTokenError,
    ServiceAccountNotFoundError,
    ServiceAccountService,
    ServiceAccountUpdate,
)
from datepipette.domain.identity import AccountStatus, AccountType, SystemRole, User
from datepipette.domain.service_accounts import (
    ApiToken,
    ApiTokenScope,
    AuthenticatedApiToken,
    ServiceAccount,
)

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _user(role: SystemRole = SystemRole.ADMIN) -> User:
    return User(
        uuid4(),
        "admin@example.com",
        "Admin",
        role,
        AccountStatus.ACTIVE,
        None,
        NOW,
        NOW,
    )


def _store() -> SimpleNamespace:
    account = ServiceAccount(
        uuid4(),
        "Embedded CRM",
        SystemRole.VIEWER,
        AccountStatus.ACTIVE,
        NOW,
        NOW,
    )
    token = ApiToken(
        uuid4(),
        account.id,
        "Primary",
        frozenset({ApiTokenScope.DASHBOARDS_READ}),
        None,
        None,
        None,
        uuid4(),
        NOW,
    )
    return SimpleNamespace(
        account=account,
        token=token,
        create_account=AsyncMock(return_value=account),
        list_accounts=AsyncMock(return_value=(account,)),
        get_account=AsyncMock(return_value=account),
        update_account=AsyncMock(return_value=account),
        create_token=AsyncMock(return_value=token),
        list_tokens=AsyncMock(return_value=(token,)),
        revoke_token=AsyncMock(return_value=True),
        authenticate_token=AsyncMock(return_value=None),
    )


def test_service_account_policy_normalizes_values_and_rejects_invalid_changes() -> None:
    store = _store()
    service = ServiceAccountService(store)
    admin = _user()

    async def exercise() -> None:
        created = await service.create(
            actor=admin,
            name="  Embedded   CRM  ",
            role=SystemRole.VIEWER,
            request_id="req-create",
        )
        assert created == store.account
        assert store.create_account.await_args.kwargs["name"] == "Embedded CRM"
        assert await service.list(actor=admin, limit=10) == (store.account,)
        assert await service.get(actor=admin, account_id=store.account.id) == store.account

        await service.update(
            actor=admin,
            account_id=store.account.id,
            update=ServiceAccountUpdate(name="  Reporting   bot "),
            request_id="req-update",
        )
        assert store.update_account.await_args.kwargs["update"].name == "Reporting bot"

        for invalid_name in (" ", "x" * 101):
            with pytest.raises(ValueError):
                await service.create(
                    actor=admin,
                    name=invalid_name,
                    role=SystemRole.VIEWER,
                    request_id="req-invalid",
                )
        with pytest.raises(ValueError, match="administrator"):
            await service.create(
                actor=admin,
                name="Powerful bot",
                role=SystemRole.ADMIN,
                request_id="req-admin-role",
            )
        with pytest.raises(ServiceAccountNotFoundError):
            await service.list(actor=_user(SystemRole.ANALYST), limit=10)

        store.get_account.return_value = None
        with pytest.raises(ServiceAccountNotFoundError):
            await service.get(actor=admin, account_id=uuid4())
        store.update_account.return_value = None
        with pytest.raises(ServiceAccountNotFoundError):
            await service.update(
                actor=admin,
                account_id=uuid4(),
                update=ServiceAccountUpdate(status=AccountStatus.DISABLED),
                request_id="req-missing",
            )

    asyncio.run(exercise())


def test_api_token_policy_handles_expiry_missing_resources_and_authentication() -> None:
    store = _store()
    service = ServiceAccountService(store)
    admin = _user()
    scopes = frozenset({ApiTokenScope.DASHBOARDS_READ})

    async def exercise() -> None:
        with pytest.raises(ValueError, match="scope"):
            await service.create_token(
                actor=admin,
                account_id=store.account.id,
                name="Empty",
                scopes=frozenset(),
                expires_at=None,
                request_id="req-empty",
            )
        with pytest.raises(ValueError, match="timezone"):
            await service.create_token(
                actor=admin,
                account_id=store.account.id,
                name="Naive",
                scopes=scopes,
                expires_at=datetime(2026, 10, 15),
                request_id="req-naive",
            )
        with pytest.raises(ValueError, match="future"):
            await service.create_token(
                actor=admin,
                account_id=store.account.id,
                name="Past",
                scopes=scopes,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                request_id="req-past",
            )

        issued = await service.create_token(
            actor=admin,
            account_id=store.account.id,
            name="  Primary  ",
            scopes=scopes,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            request_id="req-token",
        )
        assert issued.token == store.token
        assert issued.plaintext.startswith("dpp_")
        assert store.create_token.await_args.kwargs["name"] == "Primary"
        assert len(store.create_token.await_args.kwargs["token_hash"]) == 64
        assert await service.list_tokens(actor=admin, account_id=store.account.id) == (store.token,)

        store.create_token.return_value = None
        with pytest.raises(ServiceAccountNotFoundError):
            await service.create_token(
                actor=admin,
                account_id=uuid4(),
                name="Missing",
                scopes=scopes,
                expires_at=None,
                request_id="req-missing",
            )
        store.list_tokens.return_value = None
        with pytest.raises(ServiceAccountNotFoundError):
            await service.list_tokens(actor=admin, account_id=uuid4())

        store.revoke_token.return_value = None
        with pytest.raises(ServiceAccountNotFoundError):
            await service.revoke_token(
                actor=admin,
                account_id=uuid4(),
                token_id=uuid4(),
                request_id="req-account-missing",
            )
        store.revoke_token.return_value = False
        with pytest.raises(ApiTokenNotFoundError):
            await service.revoke_token(
                actor=admin,
                account_id=store.account.id,
                token_id=uuid4(),
                request_id="req-token-missing",
            )

        authenticator = ApiTokenAuthenticationService(store)
        for invalid in (None, "wrong", "dpp_" + "x" * 300):
            with pytest.raises(InvalidApiTokenError):
                await authenticator.authenticate(invalid)
        with pytest.raises(InvalidApiTokenError):
            await authenticator.authenticate(issued.plaintext)

        identity = AuthenticatedApiToken(
            store.token.id,
            User(
                store.account.id,
                "service@internal.invalid",
                store.account.name,
                store.account.role,
                store.account.status,
                None,
                NOW,
                NOW,
                AccountType.SERVICE,
            ),
            scopes,
            None,
        )
        store.authenticate_token.return_value = identity
        assert await authenticator.authenticate(issued.plaintext) == identity

    asyncio.run(exercise())
