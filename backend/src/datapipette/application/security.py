"""Password and opaque-token primitives."""

import hashlib
import secrets

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def normalize_email(email: str) -> str:
    """Return the canonical account lookup form."""

    return email.strip().lower()


def hash_password(password: str) -> str:
    """Hash a password with the configured Argon2id policy."""

    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without exposing algorithm details to callers."""

    return _password_hash.verify(password, password_hash)


def generate_opaque_token() -> str:
    """Generate a 256-bit URL-safe token."""

    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    """Create the deterministic lookup hash stored in metadata PostgreSQL."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
