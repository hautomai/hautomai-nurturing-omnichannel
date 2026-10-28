"""Password hashing and JWT auth for the single internal login."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

_ALGORITHM = "HS256"
_PBKDF2_ROUNDS = 240_000
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """PBKDF2-SHA256 in a self-describing string, so no extra dependency."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_access_token(
    user: User, settings: Settings | None = None, *, now: datetime | None = None
) -> tuple[str, int]:
    settings = settings or get_settings()
    issued = now or datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "iat": int(issued.timestamp()),
        "exp": int((issued + expires_delta).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials, settings)
    except jwt.PyJWTError as exc:  # expired or tampered
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return user


def verify_shared_secret(provided: str | None, expected: str) -> None:
    """Constant-time comparison for inbound webhook secrets."""
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")


def ensure_bootstrap_admin(db: Session, settings: Settings | None = None) -> User:
    """Create the single internal operator account if it is missing."""
    settings = settings or get_settings()
    existing = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email))
    if existing:
        return existing
    user = User(
        email=settings.bootstrap_admin_email,
        name="Hautomai Ops",
        password_hash=hash_password(settings.bootstrap_admin_password),
        role="admin",
    )
    db.add(user)
    db.flush()
    return user
