"""Tests for installation administration commands."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from datapipette import cli
from datapipette.application.auth_service import BootstrapUnavailableError


def bootstrap_args(*extra: str) -> list[str]:
    return [
        "bootstrap-admin",
        "--email",
        "admin@example.com",
        "--display-name",
        "Installation Admin",
        *extra,
    ]


def test_bootstrap_reads_password_from_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, str]] = []

    async def fake_bootstrap(email: str, display_name: str, password: str) -> None:
        captured.append((email, display_name, password))

    monkeypatch.setenv("DATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD", "a-secure-bootstrap-password")
    monkeypatch.setattr(cli, "_bootstrap", fake_bootstrap)

    cli.main(bootstrap_args("--password-env", "DATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD"))

    assert captured == [("admin@example.com", "Installation Admin", "a-secure-bootstrap-password")]


def test_bootstrap_preserves_interactive_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers: Iterator[str] = iter(["interactive-password", "interactive-password"])
    captured: list[str] = []

    async def fake_bootstrap(email: str, display_name: str, password: str) -> None:
        captured.append(password)

    monkeypatch.setattr(cli, "getpass", lambda _prompt: next(answers))
    monkeypatch.setattr(cli, "_bootstrap", fake_bootstrap)

    cli.main(bootstrap_args())

    assert captured == ["interactive-password"]


def test_bootstrap_reads_password_from_a_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    password_file = tmp_path / "bootstrap-password"
    password_file.write_text("file-backed-password\n", encoding="utf-8")
    captured: list[str] = []

    async def fake_bootstrap(email: str, display_name: str, password: str) -> None:
        captured.append(password)

    monkeypatch.setattr(cli, "_bootstrap", fake_bootstrap)

    cli.main(bootstrap_args("--password-file", str(password_file)))

    assert captured == ["file-backed-password"]


def test_bootstrap_reads_password_from_a_dotenv_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        'UNRELATED=value\nDATAPIPETTE_BOOTSTRAP_ADMIN_PASSWORD="dotenv-backed-password"\n',
        encoding="utf-8",
    )
    captured: list[str] = []

    async def fake_bootstrap(email: str, display_name: str, password: str) -> None:
        captured.append(password)

    monkeypatch.setattr(cli, "_bootstrap", fake_bootstrap)

    cli.main(bootstrap_args("--password-dotenv-file", str(dotenv_file)))

    assert captured == ["dotenv-backed-password"]


def test_bootstrap_rejects_a_dotenv_secret_without_the_password(
    tmp_path: Path,
) -> None:
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("UNRELATED=value\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="must be set and non-empty"):
        cli.main(bootstrap_args("--password-dotenv-file", str(dotenv_file)))


def test_bootstrap_rejects_a_missing_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_BOOTSTRAP_PASSWORD", raising=False)

    with pytest.raises(SystemExit, match="must be set and non-empty"):
        cli.main(bootstrap_args("--password-env", "MISSING_BOOTSTRAP_PASSWORD"))


def test_bootstrap_if_needed_skips_an_existing_installation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def existing_bootstrap(email: str, display_name: str, password: str) -> None:
        raise BootstrapUnavailableError

    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "a-secure-bootstrap-password")
    monkeypatch.setattr(cli, "_bootstrap", existing_bootstrap)

    cli.main(bootstrap_args("--password-env", "BOOTSTRAP_PASSWORD", "--if-needed"))

    assert capsys.readouterr().out == "An administrator already exists; bootstrap skipped\n"


def test_bootstrap_still_refuses_an_existing_installation_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def existing_bootstrap(email: str, display_name: str, password: str) -> None:
        raise BootstrapUnavailableError

    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "a-secure-bootstrap-password")
    monkeypatch.setattr(cli, "_bootstrap", existing_bootstrap)

    with pytest.raises(SystemExit, match="bootstrap was refused"):
        cli.main(bootstrap_args("--password-env", "BOOTSTRAP_PASSWORD"))
