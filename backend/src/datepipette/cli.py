"""Installation administration commands."""

import argparse
import asyncio
import os
from collections.abc import Sequence
from getpass import getpass
from pathlib import Path

from dotenv import dotenv_values
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
    password_source = bootstrap.add_mutually_exclusive_group()
    password_source.add_argument(
        "--password-env",
        metavar="NAME",
        help="Read the initial password from this environment variable",
    )
    password_source.add_argument(
        "--password-file",
        type=Path,
        metavar="PATH",
        help="Read the initial password from this secret file",
    )
    password_source.add_argument(
        "--password-dotenv-file",
        type=Path,
        metavar="PATH",
        help="Read DATEPIPETTE_BOOTSTRAP_ADMIN_PASSWORD from this dotenv secret file",
    )
    bootstrap.add_argument(
        "--if-needed",
        action="store_true",
        help="Exit successfully without changing credentials when an administrator exists",
    )
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


def _read_password(
    password_env: str | None,
    password_file: Path | None,
    password_dotenv_file: Path | None,
) -> str:
    if password_env is not None:
        password = os.environ.get(password_env)
        if not password:
            raise SystemExit(f"Environment variable {password_env} must be set and non-empty")
        return password
    if password_file is not None:
        try:
            password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise SystemExit(f"Password file {password_file} could not be read") from exc
        if not password:
            raise SystemExit(f"Password file {password_file} must be non-empty")
        return password
    if password_dotenv_file is not None:
        try:
            password = dotenv_values(password_dotenv_file).get(
                "DATEPIPETTE_BOOTSTRAP_ADMIN_PASSWORD"
            )
        except OSError as exc:
            raise SystemExit(
                f"Dotenv secret file {password_dotenv_file} could not be read"
            ) from exc
        if not password:
            raise SystemExit(
                "DATEPIPETTE_BOOTSTRAP_ADMIN_PASSWORD must be set and non-empty "
                f"in dotenv secret file {password_dotenv_file}"
            )
        return password

    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    return password


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command != "bootstrap-admin":
        raise SystemExit(2)
    try:
        email = str(TypeAdapter(EmailStr).validate_python(args.email))
    except ValidationError as exc:
        raise SystemExit("Invalid email address") from exc

    password = _read_password(
        args.password_env,
        args.password_file,
        args.password_dotenv_file,
    )
    try:
        asyncio.run(_bootstrap(email, args.display_name, password))
    except BootstrapUnavailableError as exc:
        if args.if_needed:
            print("An administrator already exists; bootstrap skipped")
            return
        raise SystemExit("An administrator already exists; bootstrap was refused") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
