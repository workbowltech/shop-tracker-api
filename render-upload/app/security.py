"""Passwords, login tokens and the session cookie."""
import base64
import hashlib
import hmac
import re
import secrets
import string
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Response

from .config import settings

COOKIE_NAME = "ss_session"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 64}


# ---------- passwords (scrypt, from Python's standard library) ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    """Returns a reason the password is too weak, or None if it is fine."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain both letters and numbers."
    return None


def temporary_password() -> str:
    """Easy to read out to staff: 4 letters + 4 digits, e.g. kmtr-4821."""
    letters = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(4))
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    return f"{letters}-{digits}"


# ---------- login token in a secure cookie ----------
def make_token(user_id: int, token_version: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "v": token_version, "iat": now, "exp": now + timedelta(hours=settings.session_hours)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def read_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,  # page scripts cannot read it
        secure=not settings.is_dev,  # https only in production
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=not settings.is_dev, httponly=True, samesite="lax")


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
