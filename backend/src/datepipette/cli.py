"""Installation administration commands."""

import argparse
import asyncio
from getpass import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError

from datepipette.application.auth_service import (
    BootstrapUnavailableError,
    bootstrap_first_admin,
)
from datepipette.config import get_settings
from datepipette.infrastructure.database import create_database_engine, create_session_factory
from datepipette.infrastructure.repositories.authentication import SqlAlchemyAuthenticationStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datepipette-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser(
        "bootstrap-admin",
        help="Create the first installation administrator",
    )
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    return parser


async def _bootstrap(email: str, display_name: str, password: str) -> None:
    settings = get_settings()
    engine = create_database_engine(settings)
    try:
        store = SqlAlchemyAuthenticationStore(create_session_factory(engine))
        user = await bootstrap_first_admin(store, email, display_name, password)
    finally:
        await engine.dispose()
    print(f"Created administrator {user.email} ({user.id})")


def main() -> None:
    args = _parser().parse_args()
    if args.command != "bootstrap-admin":
        raise SystemExit(2)
    try:
        email = str(TypeAdapter(EmailStr).validate_python(args.email))
    except ValidationError as exc:
        raise SystemExit("Invalid email address") from exc

    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    try:
        asyncio.run(_bootstrap(email, args.display_name, password))
    except BootstrapUnavailableError as exc:
        raise SystemExit("An administrator already exists; bootstrap was refused") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
